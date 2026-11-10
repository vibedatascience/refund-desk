"""Runs many cases in parallel: a concurrency setting, paced session creates, retries with backoff,
a clear message when the API says slow down, stop, and resume."""
from __future__ import annotations

import json
import os
import platform
import random
import re
import signal
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import api as anthropic
from . import claim
from .claim import code_checks
from .common import (DEFAULT_MODEL, PRICES, RUNS, RunLog, agent_versions, check_photos, cost_usd, dataset_fingerprint, default_effort, finished_results, ids_in, load_agent, load_case,
                     load_labels, no_answers, read_events, read_json, still_valid, ticket_print, write_json)
from . import records, versions
from .playbooks import PLAYBOOKS, guard
from .score import failures, summarize
from .task import BudgetReached, SlowDown

MAX_ATTEMPTS = 5       # sessions that failed, per case. Waiting at the gate does not count.
MAX_HOLDS = 30         # times one case may be sent back to the gate before it is given up
CREATE_GAP = 0.25      # seconds between two session creates: 240 a minute, under the documented 300


class Gate:
    """All workers pass through here before they create a session.
    It spaces the creates out, and when the API says "slow down" it holds everybody, not just one worker."""

    def __init__(self):
        self.lock = threading.Lock()
        self.next_slot = 0.0      # when the next session may be created
        self.held_until = 0.0     # the end of the current hold, if any
        self.trouble = 0          # holds since the last session that was created, for the backoff

    def wait(self, stop):
        while not stop.is_set():
            with self.lock:
                if time.time() >= max(self.next_slot, self.held_until):     # open: take this slot, book the next
                    self.next_slot = time.time() + CREATE_GAP
                    return
            time.sleep(0.05)

    def hold(self, retry_after=None):
        """Close the gate for a while. Returns (seconds until it opens, whether this call is what closed it)."""
        with self.lock:
            new = time.time() >= self.held_until           # refusals that arrive during a hold do not lengthen it
            if new:
                self.trouble += 1
                self.held_until = time.time() + (retry_after or min(60, 2 ** self.trouble) + random.random())
            return self.held_until - time.time(), new

    def fine(self):
        self.trouble = 0


def run_name(version, model, effort, backend, take=1):
    """What ran, in the folder's name. A repeat of the same settings is a further "take": ...-take2, ...-take3."""
    name = "-".join(x for x in [version, model, effort, backend, "take%d" % take if take > 1 else ""] if x)
    if not re.fullmatch(r"[\w.~-]+", name):          # the name becomes a folder in my-work/runs, and nowhere else
        raise SystemExit("A model's name is letters, digits, dots and hyphens: %r" % model)
    return name


def plain(error) -> str:
    """The API's own sentence, without the JSON around it."""
    return str(error)[:300]        # (an API error's words hold the API's own sentence, the call and the request id)


def evidence(error) -> dict:
    """Everything an error can tell, for the log: kind, HTTP status, the API's request id, the whole body. Never a key."""
    body = getattr(error, "body", None)
    return {"kind": type(error).__name__, "status": getattr(error, "status_code", None), "request_id": getattr(error, "request_id", None),
            "body": body if isinstance(body, (dict, list, str, type(None))) else str(body), "text": str(error)[:2000],
            "session_id": getattr(error, "session_id", None),
            "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__))[-3000:]}


def retry_after(error):
    try:
        return float(error.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


class Run:
    """One agent version, one model, one pass over the cases."""

    def __init__(self, version, backend_name, model, effort, concurrency, fresh, say, take=1, note=""):
        if backend_name != "managed-agents":       # the one way to run an agent here: a session on Claude Managed Agents per claim
            raise SystemExit("Agents run on Claude Managed Agents, and nowhere else.")
        from . import cma as backend
        self.backend, self.backend_name, self.say, self.concurrency = backend, backend_name, say, concurrency
        self.agent = load_agent(version)
        self.config = self.agent["config"]
        self.model = model or self.config.get("model") or DEFAULT_MODEL
        # Every run says which effort level it ran at, also when nobody chose one: then it is the model's own default.
        asked = effort or self.config.get("effort")
        self.effort, self.effort_is_default = asked or default_effort(self.model), asked in (None, default_effort(self.model))
        self.name = run_name(version, self.model, self.effort, backend_name, take)
        self.unseen = set(ids_in(("hidden",)))          # of these nothing is ever printed but a count
        self.stop, self.gate, self.live = threading.Event(), Gate(), set()
        self.refused, self.worked, self.gave_up, self.ready, self.troubled = 0, False, False, None, {}
        self.workers = max(1, concurrency)

        try:       # is another process on this very run? (signal 0 only asks whether it is alive)
            os.kill(int((RUNS / self.name / "RUNNING").read_text()), 0)
            raise SystemExit("The run %s is already going in another process. Wait for it, or press Stop." % self.name)
        except (OSError, ValueError):
            (RUNS / self.name / "RUNNING").unlink(missing_ok=True)      # nobody, or somebody who is dead
        (RUNS / self.name).mkdir(parents=True, exist_ok=True)
        try:       # taken in one step, so that of two starts in the same instant only one goes on
            os.close(os.open(str(RUNS / self.name / "RUNNING"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            raise SystemExit("The run %s is already going in another process. Wait for it, or press Stop." % self.name)
        (RUNS / self.name / "RUNNING").write_text(str(os.getpid()))
        # Resume: the same agent, model, effort and backend carry on where they stopped. A changed agent starts over.
        # A changed ticket, order record or photo is decided again, that ticket alone (see still_valid).
        before, moved, dataset = read_json(RUNS / self.name / "run.json"), "", dataset_fingerprint()
        if before and (fresh or before.get("fingerprint") != self.agent["fingerprint"]):
            moved = "%s.old-%d" % (self.name, time.time())
            (RUNS / self.name).rename(RUNS / moved)
            (RUNS / moved / "RUNNING").unlink(missing_ok=True)
        self.log = RunLog(self.name, {
            "agent": version, "title": self.config["title"], "what_changed": self.config["what_changed"], "note": note or self.config["what_changed"],
            "platform": (before or {}).get("platform") if not moved else None,        # the agent's id and version on the platform: known once it is deployed
            "playbook": self.config["playbook"],
            "fingerprint": self.agent["fingerprint"], "dataset": dataset, "model": self.model, "effort": self.effort,
            "effort_is_default": self.effort_is_default, "take": take,
            "made_with": {"python": platform.python_version(),
                          "platform": platform.platform()},
            "backend": backend_name, "concurrency": concurrency, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "started_ts": round(time.time(), 3)})
        # Keep the very prompt and handbook this run used, so that a later diff shows what really ran.
        write_json(self.log.dir / "agent-files.json", dict(
            {k + ".md": v for k, v in self.agent["prompts"].items() if k != "reporter"},
            **{"handbook/" + k: v for k, v in self.agent["handbook"].items()},
            **{"agent.json": json.dumps(self.config, indent=2)},
            **({"tools.json": self.agent["tools"]} if self.agent.get("tools") else {})))       # the tool definitions as they were deployed
        self.stop_file = self.log.dir / "STOP"             # the dashboard's Stop button writes this file
        self.stop_file.unlink(missing_ok=True)
        self.running_file = self.log.dir / "RUNNING"       # holds our process id: one runner per run
        self.running_file.write_text(str(os.getpid()))
        self.moved = moved

    def notice(self, kind, text, **more):
        self.log.emit("notice", kind=kind, text=text, **more)
        self.say("  [%s] %s" % (kind, text))

    def give_up(self, text):
        """An error that no retry can cure: say it once, clearly, and stop the whole run."""
        if not self.stop.is_set():
            self.stop.set()
            self.gave_up = True
            self.notice("error", text)

    def results(self):
        return finished_results(read_events(self.name))

    # ── one session ───────────────────────────────────────────────────────
    def session(self, role, case_id, content):
        """One session, through the gate."""
        self.gate.wait(self.stop)
        if self.stop.is_set():
            raise InterruptedError()
        if case_id:
            tell = lambda step: self.log.emit("case", id=case_id, status="running", step=step)
        else:                              # the report writer works on the whole run, not on a ticket
            tell = lambda step: self.log.emit("stage", stage=role, step=step)
        tell("starting " + ("session" if role == "prompt" else role))
        mine = []

        def created(session_id):
            self.gate.fine()
            mine.append(session_id)
            self.live.add(session_id)
        # A role may have a model of its own: "role_models": {"reviewer": "claude-sonnet-5-5"} in agent.json.
        # At the default level each model runs at its own default; a level that was asked for holds for every role.
        model = self.config.get("role_models", {}).get(role, self.model)
        effort = default_effort(model) if self.effort_is_default else self.effort
        try:
            result = self.backend.ask(dict(self.ready, model=model, effort=effort), role, case_id or role, content, on_session=created,
                                      stopped=self.stop.is_set, on_step=tell)
        except Exception as error:         # write down all there is to know, then let work() decide what to do about it
            self.log.emit("trouble", id=case_id, role=role, **evidence(error))
            self.troubled[case_id] = getattr(error, "session_id", None)
            if getattr(error, "events", None):
                try:
                    records.save(self.name, dict(records.shape(
                        {"session_id": error.session_id, "role": role, "model": model, "effort": effort, "case": case_id,
                         "backend": self.backend_name, "started_ts": round(error.started_ts, 3), "ended_ts": round(time.time(), 3),
                         "usage": {}, "cost_usd": None}, error.events), failed=str(error)[:300]))
                except Exception:
                    pass
            raise
        finally:
            self.live.difference_update(mine)
        result["model"], result["effort"] = model, effort
        result["cost_usd"] = cost_usd(model, result["usage"], result["active_seconds"])
        # The session's full record goes into its own file, runs/<run>/sessions/<id>.json; the run's log keeps the summary.
        events = result.pop("events")
        # What the platform's own record says the agent called, with the platform's event ids: the proof that a deployed agent reached our tools.
        result["tool_calls"] = [{"name": e.get("name"), "event_id": e.get("id")} for e in events if e.get("type") in ("agent.custom_tool_use", "agent.tool_use")]
        try:
            records.save(self.name, records.shape(
                dict({k: result[k] for k in ("session_id", "role", "model", "effort", "started_ts", "ended_ts", "usage", "cost_usd")},
                     case=case_id, backend=self.backend_name), events))
        except Exception as error:       # a record that cannot be written must never cost a decision that has been paid for
            tell("record not saved: %s" % (str(error)[:100] or type(error).__name__))
        return result

    # ── one case ──────────────────────────────────────────────────────────
    def work(self, case_id):
        """Decide one case by the version's playbook. Try again when a session fails; wait when the API says wait."""
        case, attempt, holds, why, seen = load_case(case_id), 0, 0, "", ticket_print(case_id)
        while attempt < MAX_ATTEMPTS and holds < MAX_HOLDS and not self.stop.is_set():
            started = time.time()
            try:
                answer, sessions = PLAYBOOKS[self.config["playbook"]](
                    lambda role, content: self.session(role, case_id, content), case, self.config)
                answer, overruled = guard(answer, case, self.config)
                overruled = overruled or sessions[-1].get("overruled")       # the guard inside a tool may have spoken first
                took = round(time.time() - started, 1)
                answer = dict(answer, evidence=claim.evidence(case, answer, "\n".join(self.agent["handbook"].values())), reply_grade=code_checks(case, answer))       # four checks by code; the judge reads it next, see judge_soon
                self.log.emit("case", id=case_id, status="done", result={
                    "answer": answer, "overruled": overruled, "sessions": sessions, "attempts": attempt + 1, "ticket_print": seen,
                    "seconds": took,
                    "cost_usd": round(sum(s["cost_usd"] for s in sessions), 6)})
                hidden = case_id in self.unseen
                self.say("  a hidden claim is decided" if hidden else "  %s  %-8s $%8.2f  policy %-5s %5.1fs" % (case_id, answer["decision"], answer["amount"], answer.get("policy_section") or "none", took))
                self.worked = True
                self.judge_soon(case_id, answer)
                return
            except InterruptedError:
                return
            except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError, SlowDown) as error:
                # "Too fast", "in trouble" or "cannot reach you": no new session starts for a while, then we carry on.
                holds += 1
                seconds, new = self.gate.hold(retry_after(error))
                status = getattr(error, "status_code", None)
                what = ("The API asks us to slow down (HTTP 429)." if status == 429 else
                        "The API is having trouble (HTTP %d)." % status if status else
                        "The model is rate limited or overloaded." if isinstance(error, SlowDown) else
                        "The connection to the API is lost.")
                if new:                                    # one notice per hold, not one per refusal
                    self.notice("hold", "%s New sessions wait %.0f s, then the run carries on." % (what, seconds),
                                wait_s=round(seconds, 1), what=what)
                why = "HTTP %d" % status if status else str(error)[:80] if isinstance(error, SlowDown) else "connection lost"
                if isinstance(error, anthropic.APIConnectionError) and not self.worked and holds >= 3:      # nothing has worked yet: no use in waiting for minutes
                    return self.give_up("The API cannot be reached, so the run stops. %s" % plain(error))
            except anthropic.APIStatusError as error:      # 400, 401, 404, 413 ...: the same request would fail again
                why = "the API refused the request (HTTP %d): %s" % (error.status_code, plain(error))
                self.refused += 1
                if error.status_code in (401, 403):
                    return self.give_up("The API refused the key (HTTP %d). Check the key on the dashboard's first page." % error.status_code)
                if error.status_code == 451:           # refused for its content: never sent again, and no new session starts
                    return self.give_up("A request was denied (HTTP 451), so the whole run stops here. %s" % why)
                if self.refused >= 5 and not self.worked:      # nothing has worked yet: the fault is ours, not one case's
                    return self.give_up("The first %d requests were all refused, so the run stops. The last answer: %s" % (self.refused, why))
                break
            except BudgetReached as error:                 # a second session would spend the same again
                why = str(error)
                break
            except Exception as error:                     # the session failed, sent nonsense, ran out of time ...
                attempt += 1
                why = str(error)[:200] or type(error).__name__
                self.refused += 1
                if self.refused >= 5 and not self.worked:      # five failures and not one success: trying on would only burn sessions
                    return self.give_up("The first %d sessions all failed, so the run stops. The last one: %s" % (self.refused, why))
                self.stop.wait(min(20, 2 ** attempt) * random.random())
            if not self.stop.is_set():
                self.log.emit("case", id=case_id, status="retrying", attempt=attempt + 1, why=why, session_id=self.troubled.get(case_id))
        if not self.stop.is_set():
            self.log.emit("case", id=case_id, status="failed", why=why, session_id=self.troubled.get(case_id))
            self.say("  %s  NO ANSWER: %s" % ("a hidden claim" if case_id in self.unseen else case_id, why))

    # ── the judge ─────────────────────────────────────────────────────────
    def start_judge(self):
        """The judge reads each reply as it comes in, beside the sessions, off the agent's clock and bill. Without a judge that loads, the run goes on."""
        self.judge, self.judging = None, []
        try:
            self.judge = claim.load_judge()
            self.judge_system, self.judge_pool = claim.system_of(self.judge), ThreadPoolExecutor(max_workers=4)
        except (SystemExit, Exception) as error:
            self.notice("error", "The judge cannot read the replies: %s The run goes on without it." % str(error)[:200])

    def judge_soon(self, case_id, answer):
        if getattr(self, "judge", None) and answer.get("reply") and not self.stop.is_set():
            self.judging.append(self.judge_pool.submit(self.judge_one, case_id, answer))

    def judge_one(self, case_id, answer):
        from . import judge as calls
        if self.judge is None or self.stop.is_set():
            return
        try:
            verdict = claim.verdict_on(self.judge, case_id, answer, self.judge_system, calls.call)
        except calls.Hopeless as error:
            if self.judge is not None:
                self.judge = None
                self.notice("error", "The judge stops: %s" % str(error)[:200])
            return
        except Exception as error:         # one reply less is judged; ./rd grade catches up
            self.say("  %s  not judged: %s" % ("a hidden claim" if case_id in self.unseen else case_id, str(error)[:100]))
            return
        self.log.emit("judged", id=case_id, verdict=verdict)
        if case_id not in self.unseen:
            self.say(claim.line(case_id, verdict))

    def finish_judging(self):
        if not getattr(self, "judge", None):
            return
        self.log.emit("stage", stage="judge", step="reading the last replies")
        while not all(f.done() for f in self.judging) and not self.stop.is_set():
            time.sleep(0.2)
        self.judge_pool.shutdown(wait=False, cancel_futures=True)
        if self.stop.is_set() or self.judge is None:
            return
        try:                               # whatever was missed: a run carried on, a call that failed
            left = [r for r in self.results().values() if r["answer"].get("reply") and (r["answer"].get("reply_grade") or {}).get("judge_print") != self.judge["print"]]
            if left:
                claim.grade_run(self.name, self.say)
        except (SystemExit, Exception) as error:
            self.notice("error", "Some replies are not judged: %s" % str(error)[:160])

    # ── all cases in parallel ─────────────────────────────────────────────
    def all_cases(self, todo):
        for case_id in todo:
            self.log.emit("case", id=case_id, status="queued")
        pool = ThreadPoolExecutor(max_workers=self.workers)
        futures = [pool.submit(self.work, case_id) for case_id in todo]
        while not all(f.done() for f in futures) and not self.stop.is_set():
            time.sleep(0.2)
        if self.stop.is_set():             # nothing new starts; sessions in flight are interrupted
            self.say("  stopping: interrupting %d sessions in flight" % len(self.live))
            with ThreadPoolExecutor(max_workers=16) as helpers:
                list(helpers.map(self.backend.abandon, list(self.live)))
            deadline = time.time() + 10    # then wait 10 s; workers stuck on a dead connection are left behind
            while time.time() < deadline and not all(f.done() for f in futures):
                time.sleep(0.2)
        pool.shutdown(wait=False)

    # ── after the parallel pass ───────────────────────────────────────────
    def report(self):
        """One more session: the aggregated report, written from the scored results."""
        results, labels = self.results(), load_labels()
        facts = dict(summarize(results, labels, 0.0, no_answers(read_events(self.name))), run=self.name, failures=[
            dict(zip(("family", "case", "expected", "got", "agent_reason", "failed_at"), row)) for row in failures(results, labels)])
        written = self.session("reporter", None, [{"type": "text", "text": json.dumps(facts, indent=1)}])
        self.log.emit("report", markdown=written["text"], cost_usd=written["cost_usd"])
        self.say("  report written")

    def extra(self, what, step):
        try:
            step()
        except Exception as error:
            if getattr(error, "status_code", None) == 451:        # refused for its content: never sent again
                return self.give_up("A request was denied (HTTP 451), so the run stops here. %s" % plain(error))
            if not self.stop.is_set():
                self.notice("error", "%s did not finish: %s. Press Run to try that step again." % (
                    what, str(error)[:200] or type(error).__name__))

    def watch_for_stop(self):
        """In the background: see the Stop file, and write a line every 20 s so that the dashboard knows we are alive."""
        beats = 0
        while not self.done:
            if self.stop_file.exists():
                self.stop.set()
            beats += 1
            if beats % 100 == 0:
                self.log.emit("alive")
            time.sleep(0.2)


def next_take(version, backend_name, model, effort, wanted) -> int:
    """Run pressed again on the same version, model and effort is a repeat, kept as a run of its own (...-take2, ...-take3), so that repeats
    can be averaged. A run that still lacks some of the wanted tickets (it was stopped, or ran on fewer) is carried on instead."""
    config, take = load_agent(version)["config"], 1
    model = model or config.get("model") or DEFAULT_MODEL
    effort = effort or config.get("effort") or default_effort(model)
    while wanted and set(wanted) <= set(still_valid(finished_results(read_events(run_name(version, model, effort, backend_name, take))))):
        take += 1
    return take


def run(version, backend_name="managed-agents", model=None, effort=None, concurrency=10, only=None, fresh=False,
        report=True, say=print, take=None, sets=("main",), note=""):
    """Run one agent version over the cases.
    Returns the run's name, whether it was stopped, and whether something went wrong (it gave up, or cases got no answer)."""
    if version not in agent_versions():
        raise SystemExit("There is no version %r. my-work/versions/LOG.md lists your versions." % version)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("No API key. Type it into the dashboard's first page, then run this again.")
    check_photos()
    wanted = ids_in(sets) if only is None else list(only)
    r = Run(version, backend_name, model, effort, concurrency, fresh, say, take or (1 if fresh else next_take(version, backend_name, model, effort, wanted)), note)
    done = still_valid(r.results())
    todo = [c for c in wanted if c not in done]
    began = time.time()
    r.log.emit("start", todo=len(todo), skipped=len(wanted) - len(todo), concurrency=concurrency, sets=list(sets))
    say("%s: %d tickets to run, %d already done, %d at a time" % (r.name, len(todo), len(wanted) - len(todo), concurrency))
    client = r.backend.client          # say where the requests go and with which key, before the first paid call
    if "api.anthropic.com" not in str(client.base_url):
        r.notice("info", "Requests go to %s, not to api.anthropic.com (DESK_BASE_URL is set)." % client.base_url)
    signal.signal(signal.SIGINT, lambda *_: r.stop.set())
    signal.signal(signal.SIGTERM, lambda *_: r.stop.set())
    r.done = False
    threading.Thread(target=r.watch_for_stop, daemon=True).start()
    try:
        if r.moved:
            r.notice("info", "The agent changed (or 'start over' was asked), so this run starts from zero. "
                             "The earlier one is kept as my-work/runs/%s." % r.moved)
        so_far = read_events(r.name)
        latest = lambda wanted: max([i for i, e in enumerate(so_far) if wanted(e)] or [-1])
        report_is_old = latest(lambda e: e["type"] == "report") < latest(lambda e: e["type"] == "case" and e.get("status") == "done")
        owed = report and done and report_is_old      # a report written before the last decision no longer tells the truth
        if not todo and not owed:
            r.notice("info", "Nothing to run: no case in this selection is left to do. Tick 'start over' (--fresh) to run them again.")
            return r.name, False, False
        if r.model not in read_json(PRICES, {}).get("models", {}):
            r.notice("info", "No price for %s in prices.json: its cost will show as $0." % r.model)
        try:
            r.ready = r.backend.prepare(r.agent, r.model, r.effort)
        except Exception as error:
            status = getattr(error, "status_code", 0)
            hint = ("Check the key on the dashboard's first page." if status in (401, 403) else
                    "Something the desk created earlier is gone from the workspace: delete .desk-state.json and run again."
                    if status == 404 else
                    "Check the network, then press Run." if isinstance(error, anthropic.APIConnectionError) else "")
            r.log.emit("trouble", id=None, role="set-up", **evidence(error))
            r.give_up("Set-up failed: %s %s" % (plain(error), hint))
            return r.name, True, True
        on_platform = (r.ready.get("agents") or {}).get("prompt")      # the platform's own id and version number for what runs: into the run and the log
        if on_platform:
            write_json(r.log.dir / "run.json", dict(read_json(r.log.dir / "run.json"), platform={"agent_id": on_platform["id"], "agent_version": on_platform["version"]}))
            r.log.emit("deployed", agent_id=on_platform["id"], agent_version=on_platform["version"])
            versions.deployed(version, on_platform)
        r.start_judge()
        r.all_cases(todo)
        r.finish_judging()
        if report and r.results() and not r.stop.is_set():
            r.extra("The report", r.report)
        return r.name, r.stop.is_set(), r.gave_up or no_answers(read_events(r.name)) > 0
    finally:
        r.done = True
        if todo:
            spent, decided = time.time() - began, sum(1 for c in todo if c in still_valid(r.results()))
            if decided < len(todo):
                say("%d of %d tickets decided, after %d min %02d s" % (decided, len(todo), spent // 60, spent % 60))
        crashed = sys.exc_info()[0] not in (None, SystemExit)
        reason = "stopped" if r.stop.is_set() else "failed" if crashed else "finished"
        r.log.emit("end", reason=reason)     # the run's closing line

        r.running_file.unlink(missing_ok=True)
        r.stop_file.unlink(missing_ok=True)
        if "DESK_RUNS_DIR" not in os.environ:        # (our saved runs are made with that set: they are not yours to log)
            try:
                versions.record_run(r.name, reason)      # score, cost and time go into my-work/versions/LOG.md
            except Exception as error:                   # the run itself is whole and on disk: only its line in the log is missing
                say("  (not written into my-work/versions/LOG.md: %s)" % (str(error)[:200] or type(error).__name__))
