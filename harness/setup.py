"""Getting ready for live runs, step by step: the package, the key, the sandbox environment, the handbooks, the agents.

The Setup page and './rd setup' both use this file. Every step can be run again without harm: what exists is kept.
The API key lives in this process's memory only (its environment, which the runner it starts inherits).
It is never written to disk, to a log, to a run or to an export, and it is never sent back to the page."""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time

from . import versions
from .common import ROOT, load_agent, read_json, starter_ids

state = {}                    # step id -> {"state": todo | doing | done | failed, "detail": "..."}
busy = threading.Lock()
typed_in = False              # the key came from the Setup page


def key_source():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    if typed_in:
        return "this page"
    env_file = ROOT / ".env"
    return ".env file" if env_file.exists() and key in env_file.read_text(encoding="utf-8") else "environment"


def presenter() -> bool:
    """Two ways to run the desk. In a room, a hundred people share one organization's rate limit, so each creates as little
    as possible: the first page deploys the first agent only (the others follow when they are first run) and a run takes the
    every claim, as does the person on stage."""
    return os.environ.get("DESK_MODE") == "presenter"


# After the deployment, the checks: everything is read back FROM THE PLATFORM, and one test ticket runs as a real session.
CHECKS = {"check:agent": "Check: the agent, read back from the platform",
          "check:tools": "Check: its tools, as the platform lists them",
          "check:guide": "Check: its guide",
          "check:environment": "Check: the sandbox and its network setting",
          "check:session": "Check: a test ticket starts a session",
          "check:calls": "Check: the agent's look-ups run, and are answered",
          "check:decision": "Check: decision, policy and reply come back",
          "check:record": "Check: the session's record, fetched from the platform, shows the same"}


def step_ids() -> list:
    return ["key", "environment", "agent:yours"] + list(CHECKS) + ["ready"]


def label(step: str) -> str:
    names = {"key": "API key answers, with Managed Agents", "environment": "Sandbox environment, no network",
             "ready": "Ready for live runs"}
    return names.get(step) or CHECKS.get(step) or "Your agent deployed"


def known_from_before() -> dict:
    """After a restart of the dashboard: what this key deployed earlier is written in .desk-state.json, so it shows as done."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    key_id = hashlib.sha256(os.environ["ANTHROPIC_API_KEY"].encode()).hexdigest()[:8]        # as cma.py names a key in its state file
    earlier = (read_json(ROOT / ".desk-state.json") or {}).get(key_id, {})
    found = {}
    if earlier.get("environment_id"):
        found["environment"] = {"state": "done", "detail": earlier["environment_id"] + " (from an earlier start)"}
    return found


def status() -> dict:
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    earlier = known_from_before()
    return {"key": {"present": bool(key), "source": key_source(), "ends_in": key[-4:] if key else None},
            "busy": busy.locked(),
            "base_url": os.environ.get("DESK_BASE_URL") or None,       # set only when requests go elsewhere than to api.anthropic.com
            "steps": [dict({"id": s, "label": label(s), "state": "todo", "detail": ""}, **dict(earlier, **state).get(s, {})) for s in step_ids()]}


def set_key(key: str) -> None:
    global typed_in
    os.environ["ANTHROPIC_API_KEY"] = key.strip()
    typed_in = True
    state.clear()              # another key, another workspace: nothing is known about it yet


def plain(error) -> str:
    """The API's own sentence, with a hint where we have one."""
    code = getattr(error, "status_code", None)
    hint = {401: " The API does not accept this key.", 403: " This key's workspace may not use Managed Agents.",
            404: " This key's workspace may not have Managed Agents."}.get(code, "")
    elsewhere = os.environ.get("DESK_BASE_URL")
    if elsewhere and "api.anthropic.com" not in elsewhere and (code or "onnection" in str(error)):
        hint = " Requests go to %s, because DESK_BASE_URL is set in this shell: unset it and start again." % elsewhere
    return str(error)[:400].replace("Error code: ", "HTTP ", 1).replace(" - ", ": ", 1) + hint


def do(step: str) -> str:
    """Carry out one step and return a line of detail. Raises when it fails."""
    if step == "ready":
        return "You are ready."
    from . import cma
    if step == "key":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("No key yet. Type it in above.")
        cma.connect()
        cma.client.beta.agents.list(limit=1)          # harmless: reads one line, creates nothing, costs nothing
        return "key ending in " + os.environ["ANTHROPIC_API_KEY"][-4:] + ", at " + str(cma.client.base_url)
    if step == "environment":
        return "%s: a cloud sandbox, networking limited with no host allowed, nothing installed; at most $%.2f a session" % (
            cma.setup_environment(), int(cma.BUDGET["budget"]["max_list_cost"]["amount"]) / 100)
    if step.startswith("agent:"):
        agent = load_agent(versions.current())          # my-work/agent/ as it is now, logged as a version if it has changed
        roles = {role: cma.setup_agent(agent, role) for role in agent["prompts"]}
        versions.deployed(agent["version"], roles["prompt"])
        return "version %s: " % agent["version"] + ", ".join("%s = %s v%s" % (("agent" if r == "prompt" else r), a["id"], a["version"]) for r, a in roles.items() if r != "reporter") + (
            "; tools of the agent: " + ", ".join(cma.tool_names(agent, "prompt")))
    if step == "checks":
        return json.dumps(checks())
    raise ValueError("no such step: " + step)


BUSY = ("HTTP 429", "HTTP 5", "Error code: 429", "Error code: 5", "Connection error", "timed out", "rate limited", "overloaded")      # worth another try


def checks() -> list:
    """[[check id, passed, what was found], ...] for the first agent. It costs a fraction of a cent: one real session on one practice ticket."""
    from . import cma, tools
    version, found = versions.current(), []
    agent = load_agent(version)
    ready = cma.prepare(agent)
    pinned = ready["agents"]["prompt"]

    def check(name, look):
        """Each check stands for itself: what goes wrong in one is told at that one, and the others still get their turn."""
        try:
            passed, detail = look()
        except Exception as error:
            if any(sign in plain(error) for sign in BUSY):
                raise                      # a busy API is tried again; see patiently
            passed, detail = False, plain(error)
            note(name, "failed", detail, error)          # with all the API said: status, request id, body
        found.append([name, bool(passed), detail])

    held = {}

    def the_agent():
        held.update(cma.client.beta.agents.retrieve(pinned["id"], version=pinned["version"]).to_dict(mode="json"))
        return (held.get("id") == pinned["id"] and held.get("version") == pinned["version"] and not held.get("archived_at"),
                "%s: agent %s, version %s, model %s" % (version, held.get("id"), held.get("version"), (held.get("model") or {}).get("id")))

    def its_tools():
        listed = lambda tools: sorted([t.get("name") for t in tools if t.get("type") == "custom"]
                                      + [c.get("name") for t in tools if str(t.get("type")).startswith("agent_toolset") for c in t.get("configs") or [] if c.get("enabled")])
        there, wanted = listed(held.get("tools") or []), listed(cma.role_tools(agent, "prompt"))
        skills = [str(s.get("skill_id")) for s in held.get("skills") or []]
        baked = len(tools.skills_of(agent["config"])) if cma.in_sandbox(agent, "prompt") else 0
        if there != wanted:
            return False, "the platform lists %s; this kit deploys %s" % (", ".join(there) or "none", ", ".join(wanted))
        if len(skills) < baked:
            return False, "the look-up scripts and the store's records should be attached as %d skill(s); the platform lists %d" % (baked, len(skills))
        return True, ", ".join(there) + ("; the look-up scripts and the store's records are deployed with it, as a skill (%s)" % ", ".join(skills[:baked]) if baked else "")

    def its_guide():
        if cma.as_skill(agent):
            skills = held.get("skills") or []
            return bool(skills), ("the handbook is attached as a skill (%s)" % ", ".join(str(s.get("skill_id")) for s in skills)
                                  if skills else "no skill is attached to the agent")
        first_line = next(line for line in agent["handbook"]["SKILL.md"].splitlines() if line.strip() and not line.startswith("---"))
        inside = first_line.strip() in (held.get("system") or "")
        return inside, ("the one-page guide rides in the system prompt (%d characters on the platform)" % len(held.get("system") or "")
                        if inside else "the system prompt on the platform does not hold the guide")

    def the_sandbox():
        config = cma.client.beta.environments.retrieve(ready["environment_id"]).to_dict(mode="json").get("config") or {}
        net = config.get("networking") or {}
        shut = config.get("type") == "cloud" and net.get("type") == "limited" and not net.get("allowed_hosts")
        return shut, "%s: %s sandbox, networking %s, %s%s" % (
            ready["environment_id"], config.get("type"), net.get("type"),
            "no host allowed" if not net.get("allowed_hosts") else "hosts allowed: %s" % net["allowed_hosts"],
            "" if shut else ". This kit asks for a cloud sandbox with limited networking and no host: run ./rd cleanup, then set up again")

    check("check:agent", the_agent)
    check("check:tools", its_tools)
    check("check:guide", its_guide)
    check("check:environment", the_sandbox)
    shown = tools.proof(version, starter_ids()[0], say=lambda words: None)
    if not shown.get("session_id"):
        why = shown.get("error") or "it did not start"
        if any(sign in why for sign in BUSY):
            raise RuntimeError(why)
        return found + [["check:session", False, "The test session failed: %s. Press the button to try again." % why.rstrip(".")]]
    calls, answer = shown["calls"], shown.get("answer") or {}
    found.append(["check:session", True, "ticket %s, session %s" % (starter_ids()[0], shown["session_id"])])
    found.append(["check:calls", bool(calls) and all(c["event_id"] and c["answered"] for c in calls),
                  "%d calls: %s" % (len(calls), ", ".join("%s (%s)" % (c["tool"], c["event_id"]) for c in calls))])
    found.append(["check:decision", bool(answer.get("decision")), "%s, $%.2f, policy %s, and a reply of %d words" % (
        answer.get("decision"), answer.get("amount") or 0, answer.get("policy_section") or "none", len((answer.get("reply") or "").split()))])
    found.append(["check:record", bool(shown["ok"]), "tools, inputs and answers in the platform's history equal what our code was asked and answered, call by call"
                  if shown["ok"] else "the platform's history of the session differs from what our code saw: ./rd prove-tools shows both"])
    return found


def patiently(step: str, changed, shown_at=None) -> str:
    """Carry out a step. When the API says "slow down" or is in trouble (a whole room presses Deploy at once), wait and try again."""
    for attempt in range(6):
        try:
            return do(step)
        except Exception as error:
            busy_there = any(sign in str(error) for sign in BUSY) or getattr(error, "status_code", None) in (429, 500, 502, 503, 529) or type(error).__name__ in ("APIConnectionError", "APITimeoutError", "SlowDown")
            if not busy_there or attempt == 5 or "certificate" in str(error):       # (waiting mends no certificate)
                raise
            try:
                wait = float(error.response.headers.get("retry-after"))
            except (AttributeError, TypeError, ValueError):
                wait = 2.0 ** attempt
            wait = min(30.0, wait) * (1 + random.random() / 2)       # not everybody again at the same moment
            note(step, "waiting", "%s Trying again in %.0f s." % (plain(error), wait), error)
            state[shown_at or step] = {"state": "doing", "detail": "%s Trying again in %.0f s." % (
                "The API asks us to wait." if getattr(error, "status_code", None) or "HTTP " in str(error) or "Error code" in str(error) else "No answer yet.", wait)}
            changed()
            time.sleep(wait)


def note(step: str, outcome: str, detail: str, error=None) -> None:
    """One line per step in .desk-setup.log: what happened, and for a failure all the API said. Never the key."""
    body = getattr(error, "body", None)
    line = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "step": step, "outcome": outcome, "detail": detail}
    if error is not None:
        line.update(kind=type(error).__name__, status=getattr(error, "status_code", None), request_id=getattr(error, "request_id", None),
                    body=body if isinstance(body, (dict, list, str, type(None))) else str(body), text=str(error)[:2000])
    with open(ROOT / ".desk-setup.log", "a", encoding="utf-8") as log:
        log.write(json.dumps(line, ensure_ascii=True) + "\n")


def all_checks(changed) -> bool:
    for check in CHECKS:
        state[check] = {"state": "doing", "detail": ""}
    changed()
    first = next(iter(CHECKS))
    try:
        found = json.loads(patiently("checks", changed, shown_at=first))
    except (Exception, SystemExit) as error:
        state[first] = {"state": "failed", "detail": plain(error)}
        note("checks", "failed", plain(error), error)
        found = []
    for check in CHECKS:
        if state[check]["state"] == "doing":
            state[check] = {"state": "todo", "detail": ""}
    for check, passed, detail in found:
        state[check] = {"state": "done" if passed else "failed", "detail": detail}
        note(check, "done" if passed else "failed", detail)
    changed()
    return len(found) == len(CHECKS) and all(passed for _, passed, _ in found)


def run(which="all", changed=lambda: None) -> bool:
    """Run one step, or all of them in order until one fails. Returns whether all went well."""
    with busy:
        for step in (step_ids() if which == "all" else [which]):
            if step in CHECKS:
                if step != next(iter(CHECKS)):
                    continue                     # they are carried out together, with the first
                if not all_checks(changed):
                    return False
                continue
            state[step] = {"state": "doing", "detail": ""}
            changed()
            try:
                state[step] = {"state": "done", "detail": patiently(step, changed)}
                note(step, "done", state[step]["detail"])
            except (Exception, SystemExit) as error:
                state[step] = {"state": "failed", "detail": plain(error)}
                note(step, "failed", state[step]["detail"], error)
                changed()
                return False
            changed()
    return True


def remove(changed=lambda: None) -> list:
    """Take away everything this key created: sessions, skill and environment are deleted, agents archived."""
    with busy:
        try:
            from . import cma
            left = cma.cleanup(say=lambda words: None)
        except Exception as error:           # the network, the API: say so, or the page would report success
            left = [{"error": plain(error)}]
        state.clear()
        if left:
            state["ready"] = {"state": "failed", "detail": "%d things could not be removed; try again. %s" % (len(left), left[0]["error"][:200])}
        changed()
    return left
