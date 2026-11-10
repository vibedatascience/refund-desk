"""The main backend: Claude Managed Agents.

Set up once:     an environment (the sandbox), skills (the look-up scripts with the store's records; the handbook), and the agent.
For each ticket: one session. Open its event stream, send the ticket with the photo, follow what the agent does in its sandbox,
                 and take its decision when it calls submit_decision.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import re
import secrets
import threading
import time

from . import api as anthropic         # the API over plain HTTPS, standard library only; the names are the official SDK's

from . import tools as desk_tools
from .common import DEFAULT_MODEL, ROOT, load_case, read_json, write_json
from .playbooks import TEXT_ROLES
from .task import BudgetReached, SessionFailed, SlowDown, answer_tool, clean_answer, handbook_text

# max_retries=0: the runner does the retries, so every one of them shows on the dashboard.
# timeout=30: no single call may hang on a bad network for longer than that.
client = None


def connect() -> None:
    """(Again) make the client, from ANTHROPIC_API_KEY as it is now. The Setup page calls this after a key was typed in."""
    global client
    client = anthropic.Anthropic(max_retries=0, timeout=30)


connect()

CASE_TIME_LIMIT = 300        # seconds before a session is given up and retried
QUIET_LIMIT = 30             # seconds of silence on a stream before we reconnect
LOOK_UP_EVERY = 45           # seconds after which we leave a stream for a moment to check the clock and the Stop button
# A hard ceiling on what one session may spend: 100 cents. The platform enforces it.
BUDGET = {"budget": {"type": "limit", "max_list_cost": {"amount": "100", "currency": "USD"}}}

# The agent reads hostile text (customer messages, labels in photos). What holds it: a sandbox with no network and no credential in it,
# made-up data, a budget, and ONE typed way out (submit_decision), whose content our code checks. Inside the sandbox it has a shell, to
# run the look-up scripts that were deployed with it; whatever it does there stands in the session's record, and the eval reads it.
# With real customers' data, follow the public guide on secure deployment instead: look-ups as custom tools that run in your own code.
ENVIRONMENT = {"type": "cloud", "networking": {"type": "limited"}}   # limited, and no host allowed
READ_ONLY_TOOLS = ("read", "glob", "grep")
SANDBOX_TOOLS = ("bash", "read", "glob", "grep")        # bash runs the look-up scripts; there is no network to reach and nothing to write to but the sandbox
SANDBOX_TOOLSET = {"type": "agent_toolset_20260401",
                   "default_config": {"enabled": False, "permission_policy": {"type": "auto"}},      # the server weighs each call by itself. In the few
                   "configs": [{"name": name, "enabled": True} for name in SANDBOX_TOOLS]}          # cases where it asks us, see ask(): this is no gate
TOOLSET = {"type": "agent_toolset_20260401",
           "default_config": {"enabled": False, "permission_policy": {"type": "auto"}},
           "configs": [{"name": name, "enabled": True} for name in READ_ONLY_TOOLS]}


# ── set up once ───────────────────────────────────────────────────────────
# Environments, skills and agents persist in your workspace. We create each one once, keep its id
# in .desk-state.json, and publish a new version only when the folder on disk has changed.
STATE_FILE = ROOT / ".desk-state.json"

_sessions_file_lock = threading.Lock()


def _key_id() -> str:
    """Ids belong to the workspace of one API key. Another key gets its own set, so switching keys never mixes them up."""
    return hashlib.sha256((client.api_key or "").encode()).hexdigest()[:8]


def sessions_file():
    """One line per session created with this key, so that cleanup can always find them."""
    return ROOT / (".desk-sessions-%s.txt" % _key_id())


def _state() -> dict:
    state = (read_json(STATE_FILE) or {}).get(_key_id())
    if not state:  # the handle is a tag on everything we create, so a shared workspace stays tidy
        state = {"handle": "desk-" + secrets.token_hex(3), "versions": {}}       # random: nobody's login name goes to a workspace that a room shares
        _save(state)
    return state


def _save(state: dict) -> None:
    write_json(STATE_FILE, dict(read_json(STATE_FILE) or {}, **{_key_id(): state}))


def owner() -> dict:
    return {"app": "refund-desk", "owner": _state()["handle"]}


def setup_environment() -> str:
    state = _state()
    if "environment_id" not in state:
        environment = client.beta.environments.create(
            name="refund-desk-" + state["handle"], config=ENVIRONMENT, metadata=owner())
        state["environment_id"] = environment.id
        _save(state)
    return state["environment_id"]


def upload_skill(which: str, title: str, files: list) -> dict:
    """Upload a skill: a folder with a SKILL.md and whatever else it needs (chapters, scripts, data). files: [(path, bytes), ...], the top
    folder named as the skill is named at the head of its SKILL.md. The same skill gets a new version when its files change."""
    state = _state()
    id_key = "skill_id" if which == "handbook" else "skill_id:" + which
    key = "skill:%s:%s" % (which, hashlib.sha256(repr(sorted(files)).encode()).hexdigest()[:12])      # same files, same version
    if key not in state["versions"]:
        if id_key not in state:
            skill = client.beta.skills.create(files=files, display_name="%s (%s)" % (title, state["handle"]))
            state[id_key], version = skill.id, skill.latest_version_id
        else:
            version = client.beta.skills.versions.create(state[id_key], files=files).id
        state["versions"][key] = version
        _save(state)
    # Pinning a skill's version inside each agent version keeps v1, v2 and v3 runnable side by side.
    return {"type": "custom", "skill_id": state[id_key], "version": state["versions"][key]}


def setup_skill(agent: dict) -> dict:
    """The version's handbook folder, as a skill."""
    folder = re.search(r"^name:\s*(\S+)", agent["handbook"]["SKILL.md"], re.M).group(1)
    return upload_skill("handbook", "Refund handbook", [(folder + "/" + name, text.encode("utf-8")) for name, text in sorted(agent["handbook"].items())])


def in_sandbox(agent: dict, role: str) -> bool:
    """Do this role's look-ups run on the platform, in the session's own sandbox? (The scripts and the store's records are deployed as skills.)
    The other way, DESK_TOOLS=client, has the look-ups answered by this application over the session's event stream."""
    return desk_tools.RUN_IN == "sandbox" and desk_tools.uses_tools(agent["config"], role)


def system_skills(agent: dict) -> list:
    """The store's systems as skills: scripts and data. Which ones a version gets follows from the tools in its agent.json."""
    return [upload_skill(which, "Refund desk " + which, desk_tools.skill_files(which)) for which in desk_tools.skills_of(agent["config"])]


def as_skill(agent: dict) -> bool:
    """A one-page guide rides in the system prompt: the agent decides in a single model request, with no file to open.
    A handbook of many files is uploaded as a skill: the agent opens the chapters it needs. agent.json can say which."""
    return agent["config"].get("guide_as", "prompt" if len(agent["handbook"]) == 1 else "skill") == "skill"


def role_tools(agent: dict, role: str) -> list:
    """The tools a role is deployed with. Their definitions (name, description, input schema) become part of the agent on the
    platform; the code behind the custom ones stays here, and answers when the agent calls."""
    if role in TEXT_ROLES:
        return []
    if in_sandbox(agent, role):          # look-ups: scripts in the sandbox, run with bash. The decision: the one typed way out
        return [SANDBOX_TOOLSET] + [dict(tool, type="custom") for tool in desk_tools.definitions(agent["config"], "sandbox")]
    if desk_tools.uses_tools(agent["config"], role):      # look-ups: custom tools that this application answers (harness/tools.py)
        custom = desk_tools.definitions(agent["config"])
    else:
        custom = [answer_tool(role)]
    return ([TOOLSET] if as_skill(agent) else []) + [dict(tool, type="custom") for tool in custom]


def tool_names(agent: dict, role: str) -> list:
    built_in = {id(TOOLSET): READ_ONLY_TOOLS, id(SANDBOX_TOOLSET): SANDBOX_TOOLS}
    return [name for tool in role_tools(agent, role) for name in built_in.get(id(tool), [tool.get("name")])]


def setup_agent(agent: dict, role: str = "prompt") -> dict:
    """Each version's folder becomes one version of a single agent on the platform. A session pins the version it runs.
    A playbook with a second role (the reviewer) has one such agent per role."""
    reports_in_text = role in TEXT_ROLES          # no decision to submit, no handbook to read: no tools at all
    in_prompt = not as_skill(agent)
    skills = [] if reports_in_text or in_prompt else [setup_skill(agent)]
    if in_sandbox(agent, role):
        skills = system_skills(agent) + skills
    tools = role_tools(agent, role)
    state = _state()
    config = dict(name="refund-desk-%s-%s" % (role, state["handle"]), model=DEFAULT_MODEL,
                  system=agent["prompts"][role] + (desk_tools.note(agent["config"], "sandbox" if in_sandbox(agent, role) else "client")
                                                   if desk_tools.uses_tools(agent["config"], role) else "")
                  + (handbook_text(agent) if in_prompt and not reports_in_text else ""),
                  skills=skills, tools=tools, metadata=owner())
    # Two folders whose role comes out the same (the report writer, say) share one platform
    # version, and nothing is sent for the second: folder names are ours, version numbers are the platform's.
    same = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    id_key, version_key = "agent_id:" + role, "agent:%s:%s" % (role, same)
    if version_key not in state["versions"]:
        if id_key not in state:
            saved = client.beta.agents.create(**config)
            state[id_key] = saved.id
        else:
            latest = client.beta.agents.retrieve(state[id_key]).version
            saved = client.beta.agents.update(state[id_key], version=latest, **config)
        state["versions"][version_key] = saved.version
        _save(state)
    return {"id": state[id_key], "version": state["versions"][version_key]}


def prepare(agent: dict, model=None, effort=None) -> dict:
    """Everything a run needs before its first session: environment, skill, one agent per role."""
    ready = {"environment_id": setup_environment(), "model": model, "effort": effort,
             "agents": {role: setup_agent(agent, role) for role in agent["prompts"]}}
    if agent["config"].get("tools"):
        shop = desk_tools.Shop()
        ready["desk"] = lambda case_id: desk_tools.Desk(shop, load_case(case_id), agent["config"])      # one Desk per session
    return ready


# ── one session per case ──────────────────────────────────────────────────
def start_session(ready: dict, role: str, case_id: str) -> str:
    agent = dict(ready["agents"][role], type="agent")           # pin the agent version
    if ready["model"]:  # same agent version, other model or effort, for this session only
        model = {"id": ready["model"]}
        if ready["effort"]:
            model["effort"] = ready["effort"]
        agent = dict(agent, type="agent_with_overrides", model=model)
    session = client.beta.sessions.create(
        agent=agent,
        environment_id=ready["environment_id"],
        title="Refund ticket %s (%s)" % (case_id, role),
        metadata=dict(owner(), case=case_id),
        **BUDGET,
    )
    with _sessions_file_lock, open(sessions_file(), "a") as f:
        f.write(session.id + "\n")
    return session.id


def follow(session_id: str, first_message: dict, deadline: float, on_drop=lambda why: None, stopped=lambda: False):
    """Send the first message and yield every event of the session once, whatever the connection does."""
    seen, sent, tries = set(), "no", 0
    while time.time() < deadline and not stopped():
        tries += 1
        pause, alarm = min(5.0, tries), None       # 1 s, 2 s ... 5 s between reconnects
        try:
            # Open the stream before sending, so no event is missed.
            with client.beta.sessions.events.stream(session_id, timeout=QUIET_LIMIT) as stream:
                # A stream that carries nothing but keep-alive lines never times out and never yields, so the checks further
                # down would never run. An alarm closes it from outside: at the deadline, and every few seconds to look for Stop.
                alarm = threading.Timer(max(0.5, min(deadline - time.time(), LOOK_UP_EVERY)), stream.close)
                alarm.daemon = True
                alarm.start()
                # Reconnecting? Catch up from the session's stored history, then go on live.
                history = [] if sent == "no" else list(client.beta.sessions.events.list(session_id))
                if sent != "yes" and not any(event.type == "user.message" for event in history):
                    sent = "perhaps"      # if this call dies half way, the history will tell whether it arrived
                    client.beta.sessions.events.send(session_id, events=[first_message])
                sent = "yes"
                for event in itertools.chain(history, stream):
                    event_id = getattr(event, "id", None)
                    if event_id not in seen:
                        seen.add(event_id)
                        yield event
                    if time.time() > deadline or stopped():
                        break
            if alarm.is_alive():
                on_drop("the stream ended")
            else:
                tries = 0                 # our own alarm: look up, then listen again at once
        except (anthropic.RateLimitError, anthropic.InternalServerError) as error:
            on_drop("HTTP %d" % error.status_code)      # the session itself is still working: wait, then look again
            pause = 5.0
        except anthropic.APIStatusError:
            raise                                       # a plain no (400, 401, 404, ...): waiting will not help
        except Exception as error:
            if alarm is not None and not alarm.is_alive():
                tries = 0                 # our own alarm cut the read
            else:
                on_drop("%s: %s" % (type(error).__name__, error))   # dropped or silent connection
        finally:
            if alarm is not None:
                alarm.cancel()
        time.sleep(pause if tries else 0)
    raise SessionFailed("stopped" if stopped() else "no decision after %d seconds" % CASE_TIME_LIMIT)


def denied(error) -> bool:
    """HTTP 451, "unavailable for legal reasons": somebody on the way refuses this content. Never swallowed, never sent again: the whole run stops."""
    return getattr(error, "status_code", None) == 451


def ask(ready: dict, role: str, case_id: str, content: list, on_step=lambda text: None, on_session=lambda sid: None,
        stopped=lambda: False) -> dict:
    """Run one session: send the case, follow the events, return the structured decision with its usage."""
    started = time.time()
    session_id = start_session(ready, role, case_id)
    on_session(session_id)
    answer, text, usage, trace, trouble, first_event_s = None, "", None, [], "", None
    first_message = {"type": "user.message", "content": content}
    desk = ready["desk"](case_id) if "desk" in ready and role in desk_tools.DECIDERS else None
    listening = desk_tools.RUN_IN == "sandbox"
    said = []

    def reconnecting(why):            # say it once per cause, not once per try
        if said[-1:] != [why]:
            said.append(why)
            on_step("reconnecting (%s)" % why[:80])

    try:
        for event in follow(session_id, first_message, started + CASE_TIME_LIMIT, reconnecting, stopped):
            if first_event_s is None and event.type.startswith(("agent.", "span.")):
                first_event_s = round(time.time() - started, 2)       # from "create the session" to the agent's first sign of life
            if desk and listening and event.type in ("agent.tool_use", "agent.tool_result"):
                for step in desk.hear(event.to_dict()):       # what ran in the sandbox, and what it printed: our Desk keeps its own log of it
                    trace.append(step)
                    on_step(step)
            if event.type == "agent.tool_use":               # a built-in tool, run in the sandbox
                if not (desk and listening):
                    step = (event.name + " " + str(event.input.get("file_path") or event.input.get("pattern") or "")).strip()
                    trace.append(step)
                    on_step(step)
                if event.evaluated_permission == "ask":       # the platform is in doubt and asks us: yes to the handbook and to our own scripts
                    if listening:
                        fine = (desk_tools.allowed_command(event.input.get("command") or "") if event.name == "bash"
                                else event.name in READ_ONLY_TOOLS and desk_tools.allowed_reading(event.input))
                    else:
                        fine = event.name in READ_ONLY_TOOLS
                    _patiently(lambda: client.beta.sessions.events.send(session_id, events=[
                        {"type": "user.tool_confirmation", "tool_use_id": event.id, "result": "allow" if fine else "deny"}]), tries=8)

            elif event.type == "agent.custom_tool_use" and desk:     # the deployed agent calls one of our tools: our code answers
                step = ("%s %s" % (event.name, "" if event.name == "submit_decision" else next(iter(event.input.values()), "")))[:80].strip()
                trace.append(step)
                on_step(step)
                result, is_error = desk.answer(event.name, event.input)
                answer = desk.decision
                # The session waits for this answer, so a hiccup must not lose it. (A second result for the same call has no effect.)
                _patiently(lambda: client.beta.sessions.events.send(session_id, events=[
                    {"type": "user.custom_tool_result", "custom_tool_use_id": event.id, "is_error": is_error,
                     "content": [{"type": "text", "text": result}]}]), tries=8)

            elif event.type == "agent.custom_tool_use":       # our tool: its input is the structured decision
                answer = clean_answer(event.input)
                trace.append(event.name)
                _patiently(lambda: client.beta.sessions.events.send(session_id, events=[
                    {"type": "user.custom_tool_result", "custom_tool_use_id": event.id,
                     "content": [{"type": "text", "text": "Recorded."}]}]), tries=8)

            elif event.type == "agent.message":
                text += "".join(block.text for block in event.content if block.type == "text")

            elif event.type == "session.usage":               # running totals of tokens and active seconds
                usage = event.usage

            elif event.type == "session.error":               # the platform retries by itself; we only take note
                trouble = event.error.type
                on_step("session error: " + event.error.message)

            elif event.type == "session.status_terminated":
                raise SessionFailed("the session was terminated")

            elif event.type == "session.status_idle" and event.stop_reason.type != "requires_action":
                if answer or (role in TEXT_ROLES and text):
                    break
                if event.stop_reason.type == "budget_reached":
                    raise BudgetReached("the session hit its budget before it decided")
                if trouble in ("model_rate_limited_error", "model_overloaded_error"):
                    raise SlowDown("the model was rate limited or overloaded")
                raise SessionFailed("the agent finished without calling submit_decision" if event.stop_reason.type == "end_turn"
                                    else "the session stopped (%s) before a decision" % event.stop_reason.type)
    except Exception as error:
        if answer is None or denied(error):         # no decision in hand. (With one in hand, a late hiccup does not throw paid work away.)
            error.session_id, error.started_ts = session_id, started      # what the failed session did is the evidence: take it along
            try:
                error.events = [] if denied(error) else pull(session_id)      # (after a denial nothing is fetched either)
            except Exception:
                error.events = []
            abandon(session_id)
            raise
        abandon(session_id)

    try:
        usage = usage or client.beta.sessions.retrieve(session_id).usage
    except Exception as error:
        if denied(error):
            raise
    try:
        history = pull(session_id)       # the platform keeps the whole session: fetch its record and save it with the run
    except Exception as error:
        if denied(error):
            raise
        history = []                     # the decision stands without it; './rd pull <run>' fetches it later
    cache_writes = sum(v or 0 for v in usage.cache_creation.to_dict().values()) if usage and usage.cache_creation else 0
    count = lambda name: (getattr(usage, name, 0) or 0) if usage else 0
    return {
        "role": role, "answer": answer, "text": text, "session_id": session_id, "trace": trace, "events": history,
        "started_ts": round(started, 3), "ended_ts": round(time.time(), 3), "seconds": round(time.time() - started, 1),
        "first_event_s": first_event_s,
        "tool_log": desk.log if desk else None,            # every call our code answered, and what the guard said to it
        "tool_config": desk_tools.switches(desk.config) if desk else None,
        "overruled": desk.overruled if desk else None,     # why code's own answer stands in place of the agent's, if it does
        "active_seconds": count("active_seconds"),
        "usage": {"input_tokens": count("input_tokens"), "output_tokens": count("output_tokens"),
                  "cache_read_input_tokens": count("cache_read_input_tokens"),
                  "cache_creation_input_tokens": cache_writes},
        # the API's own price for the session, in whole cents: a cross-check on ours
        "api_list_cost_cents": int(usage.list_cost.amount) if usage and usage.list_cost else None,
    }


def pull(session_id: str) -> list:
    """Every event of a session, oldest first, as plain dictionaries: messages, tool calls, model requests, usage."""
    return [event.to_dict(mode="json") for event in client.beta.sessions.events.list(session_id)]


def state_of(session_id: str) -> dict:
    """What the platform says of a session now: status, agent and its version, environment, tokens, active seconds, its own price. Nothing of its content."""
    s = client.beta.sessions.retrieve(session_id).to_dict(mode="json")
    usage, agent = s.get("usage") or {}, s.get("agent") if isinstance(s.get("agent"), dict) else {"id": s.get("agent")}
    cost = (usage.get("list_cost") or {}) if isinstance(usage.get("list_cost"), dict) else {}
    number = lambda x: x if isinstance(x, (int, float)) and not isinstance(x, bool) else None
    return {"session_id": session_id, "status": str(s.get("status") or "")[:30], "agent_id": str(agent.get("id") or "")[:80],
            "agent_version": number(agent.get("version")), "environment_id": str(s.get("environment_id") or "")[:80],
            "created_at": str(s.get("created_at") or "")[:40], "tokens_in": number(usage.get("input_tokens")), "tokens_out": number(usage.get("output_tokens")),
            "cache_read": number(usage.get("cache_read_input_tokens")), "active_seconds": number(usage.get("active_seconds") or s.get("active_seconds")),
            "list_cost_cents": str(cost.get("amount") or "")[:20]}


def interrupt(session_id: str) -> None:
    client.with_options(timeout=5).beta.sessions.events.send(session_id, events=[{"type": "user.interrupt"}])


def abandon(session_id: str) -> None:
    """We give up on this session: do not leave it running (and spending) in the cloud."""
    try:
        interrupt(session_id)
    except Exception:
        pass


# ── tidy up ───────────────────────────────────────────────────────────────
def _patiently(call, tries=6):
    """Calls are rate limited, and networks hiccup: wait and try again."""
    for attempt in range(tries):
        try:
            return call()
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as error:
            if attempt == tries - 1:
                raise
            header = getattr(getattr(error, "response", None), "headers", {}).get("retry-after")
            time.sleep(float(header) if header else 2 ** attempt)


def delete_session(session_id: str) -> None:
    """A running session cannot be deleted: interrupt it, wait until it is idle, then delete."""
    if _patiently(lambda: client.beta.sessions.retrieve(session_id)).status in ("running", "rescheduling"):
        _patiently(lambda: interrupt(session_id))
        for _ in range(30):
            if _patiently(lambda: client.beta.sessions.retrieve(session_id)).status not in ("running", "rescheduling"):
                break
            time.sleep(1)
    _patiently(lambda: client.beta.sessions.delete(session_id))


def cleanup(session_ids=(), say=print) -> list:
    """Delete the sessions given, then the agents, skill and environment recorded in .desk-state.json.
    Returns what could not be deleted; the state file is kept in that case, so that cleanup can be run again."""
    state, left = _state(), []
    remembered = sessions_file().read_text().split() if sessions_file().exists() else []
    session_ids = sorted(set(session_ids) | set(remembered))

    def attempt(what, call):
        try:
            call()
            return True
        except anthropic.NotFoundError:
            return True          # already gone
        except Exception as error:
            left.append({"what": what, "error": "%s: %s" % (type(error).__name__, str(error)[:300])})
            say("  could NOT delete %s: %s" % (what, error))
            return False

    gone = 0
    for number, s in enumerate(session_ids, 1):
        gone += attempt("session " + s, lambda: delete_session(s))
        if number % 25 == 0:
            say("  ... %d of %d sessions" % (number, len(session_ids)))
    say("  sessions deleted: %d of %d" % (gone, len(session_ids)))
    for key in [k for k in state if k.startswith("agent_id:")]:
        attempt("agent " + state[key], lambda: _patiently(lambda: client.beta.agents.archive(state[key])))
    for skill_id in [state[k] for k in state if k == "skill_id" or k.startswith("skill_id:")]:
        versions = []
        attempt("skill versions", lambda: versions.extend(_patiently(lambda: list(client.beta.skills.versions.list(skill_id)))))
        for version in versions:
            attempt("skill version " + version.id,
                    lambda: _patiently(lambda: client.beta.skills.versions.delete(version.id, skill_id=skill_id)))
        attempt("skill " + skill_id, lambda: _patiently(lambda: client.beta.skills.delete(skill_id)))
    if "environment_id" in state:
        attempt("environment " + state["environment_id"],
                lambda: _patiently(lambda: client.beta.environments.delete(state["environment_id"])))
    if left:
        say("  %d things are left; the .desk-* files are kept, run the clean-up again later." % len(left))
    elif len(state) <= 2 and not session_ids:
        say("  nothing to clean up for this key.")
    else:
        say("  agents archived, skill and environment deleted.")
        others = {k: v for k, v in (read_json(STATE_FILE) or {}).items() if k != _key_id()}
        write_json(STATE_FILE, others) if others else STATE_FILE.unlink(missing_ok=True)
        sessions_file().unlink(missing_ok=True)
    return left
