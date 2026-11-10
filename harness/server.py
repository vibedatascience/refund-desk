"""The dashboard's server. Python standard library only.

It does three things:
  1. serves one static page (dashboard.html) and the case photos;
  2. watches the runs' events.jsonl files, scores what it reads, and pushes the changes to the page (server-sent events);
  3. starts and stops the runner as a separate process, with the very command you could type yourself.
"""
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .common import (HARNESS, KEY_DIR, LABELS, PRICES, ROOT, RUNS, SHOP, WORKSHOP, case_dir, agent_versions, case_ids, dataset_fingerprint, default_concurrency, list_runs, outside, run_dir, sound, starter_ids, ticket_print, load_agent, load_case, load_labels,
                     new_tests_on, read_json, turn_new_tests)
from . import records, setup, versions
from .playbooks import forge
from .score import grade, same_settings, summarize, takes, timing

TOKEN = secrets.token_urlsafe(24)      # made anew at every start; the page we serve carries it, its requests send it back
KEY = {}                  # case id -> label (the answer key)
DATASET = {}              # {"fingerprint": ...} of the main set's tickets, orders and photos as they are on disk
PRINTS = {}               # case id -> the print of its ticket, order and photo as they are on disk
CASE_IDS = set()
FOLLOW = {"ts": 0, "set": None}      # the set of the run that began last, until the wall has followed it
WALL = {"set": "main"}     # which tickets every page is about: the 24, or the new tests once they are on ("new")
listeners = []            # one queue per open browser tab
one_big_answer = threading.Lock()
views = {}                # run name -> RunView
state_lock = threading.RLock()   # guards views and child: the watcher and the request threads share them
child = None              # the runner process started from the page, if any
stopping = False          # Stop was pressed and that runner is winding down
QUIET = 150               # a run that says "running" but has written nothing for this long has died


def shown(case_id: str) -> bool:
    """Hidden tickets count in a run's score; what the agent said about them does not reach the page."""
    return (KEY.get(case_id) or {}).get("set") != "hidden"


def on_the_wall(case_id: str) -> bool:
    """The page draws the main set: its wall, its counts, its clock. The other sets reach it as scores (summary.sets)."""
    return (KEY.get(case_id) or {}).get("set") == "main"


def new_tests_count() -> dict:
    """How many there are, whether they are on, and how many the first set holds."""
    which = [label["set"] for label in KEY.values()]
    return {"on": new_tests_on(), "n": which.count("main") if WALL["set"] == "new" else which.count("new") + which.count("hidden"),
            "first": which.count("first") if WALL["set"] == "new" else which.count("main")}


def main_cases() -> int:
    return sum(1 for label in KEY.values() if label["set"] == "main")


class RunView:
    """What the page needs to know about one run, kept up to date by reading new lines of its log."""

    def __init__(self, name):
        self.name, self.offset, self.first_line = name, 0, b""
        self.cases, self.results, self.notices = {}, {}, []
        self.report, self.state, self.started_ts, self.last_ts = None, "idle", None, None
        self.stage, self.extra_cost = None, 0.0
        self.scored, self.scored_at, self.retries = None, -1, 0
        self.settings = read_json(run_dir(name) / "run.json", {})

    def path(self):
        return run_dir(self.name) / "events.jsonl"

    def started_over(self):
        """True when the log on disk is no longer the one we were reading (a fresh run of the same name)."""
        with open(self.path(), "rb") as f:
            return f.readline() != self.first_line

    def read_new_lines(self):
        """Returns the ids of the cases that changed."""
        with open(self.path(), "rb") as f:
            if not self.first_line:
                self.first_line = f.readline()
            f.seek(self.offset)
            chunk = f.read()
        chunk = chunk[:chunk.rfind(b"\n") + 1]          # leave a half-written last line for next time
        self.offset += len(chunk)
        changed = set()
        for line in chunk.decode("utf-8", "replace").split("\n"):
            try:
                self.take(json.loads(line), changed)
            except (ValueError, KeyError, TypeError):
                pass                                       # one bad line must not stop the dashboard
        return changed

    def take(self, event, changed):
        kind = event["type"]
        self.started_ts = self.started_ts or event["ts"]
        self.last_ts = event["ts"]
        if kind == "report":
            self.extra_cost = event.get("cost_usd") or 0          # a run that grew in several goes has several reports: it costs one
        if kind == "start":
            self.state, self.started_ts = "running", event["ts"]
            self.settings = read_json(run_dir(self.name) / "run.json", {})      # the runner writes it anew at every start
            if event["ts"] > max(FOLLOW["ts"], time.time() - 15):       # a run has just begun: the wall shows the tickets it runs
                FOLLOW.update(ts=event["ts"], set="new" if event.get("sets") == ["new"] else "main")
        elif kind == "deployed":
            self.settings = read_json(run_dir(self.name) / "run.json", {})      # now with the platform's agent id and version
        elif kind == "stage":
            self.stage = event["stage"]
        elif kind == "end":
            self.state, self.stage = event["reason"], None
            for case in self.cases.values():               # nothing is left spinning after the end
                if case["status"] in ("queued", "running", "retrying"):
                    case["status"] = "waiting"
                    changed.add(case["id"])
        elif kind == "notice":
            self.notices.append(event)
        elif kind == "judged":          # the judge's verdict on one reply, written after the run
            if event.get("id") in self.results and not outside(self.name):
                from .claim import merge
                result = self.results[event["id"]]
                result = dict(result, answer=merge(result["answer"], event.get("verdict")))
                self.results[event["id"]] = result
                if event["id"] in self.cases:
                    self.cases[event["id"]]["result"] = result
                    if event["id"] in KEY:
                        self.cases[event["id"]]["grade"] = grade(result["answer"], KEY[event["id"]])
                    changed.add(event["id"])
        elif kind == "report":
            self.report = event["markdown"]
        elif kind == "case":
            result = sound(event.get("result")) if event["status"] == "done" else None
            if (event["status"] == "done" and not result) or (outside(self.name) and not shown(event["id"])):
                return      # not shaped like a result; or a hidden ticket in a run that the harness did not make
            event = dict(event, result=result)      # (with the fields it may leave out filled in empty)
            case = self.cases.setdefault(event["id"], {"id": event["id"]})
            case.update({k: v for k, v in event.items() if k in ("status", "step", "attempt", "why")})
            self.retries += event["status"] == "retrying"
            if event["status"] == "done":
                case["result"] = event["result"]
                self.results[event["id"]] = event["result"]
                if event["id"] in KEY:
                    case["grade"] = grade(event["result"]["answer"], KEY[event["id"]])
            changed.add(event["id"])

    def summary(self):
        statuses = [c["status"] for c in self.cases.values() if on_the_wall(c["id"])]      # every headline figure is the main set's
        if self.scored_at != self.offset:          # score again only when the log has grown
            self.scored, self.scored_at = summarize(self.results, KEY, self.extra_cost, statuses.count("failed")), self.offset
        s = dict(self.scored)
        state = self.state
        if state == "running" and time.time() - (self.last_ts or 0) > QUIET:
            state = "interrupted"
        s.update(state=state, in_flight=sum(x in ("running", "retrying") for x in statuses),
                 queued=statuses.count("queued"), total=main_cases(), stage=self.stage, timing=timing({c: r for c, r in self.results.items() if on_the_wall(c)}, sum(1 for n in self.notices if n.get("kind") == "hold"), self.retries, self.settings.get("concurrency")),
                 injections_planned=sum(1 for c in self.cases if (KEY.get(c) or {}).get("injection") and KEY[c]["set"] == "main"),
                 started_ts=self.started_ts, last_ts=self.last_ts)
        return s

    def head(self):
        settings = self.settings
        # stale: per set, how many of the run's results were decided on a ticket, order or photo that has changed since.
        # older_data: that is so for some ticket of the main set, so the run does not compare with newer runs.
        stale = {}
        for case_id, result in self.results.items():
            if result.get("ticket_print") not in (None, PRINTS.get(case_id)) and case_id in KEY:
                stale[KEY[case_id]["set"]] = stale.get(KEY[case_id]["set"], 0) + 1
        # older_agent: the version's prompt, handbook or settings have been edited since; the run kept its own copy.
        # takes: the same settings run several times (…-take2, …-take3): how many, their mean accuracy, lowest and highest.
        # Only finished runs over the very same tickets count, and only those on the very same agent files, cases and photos.
        wall = lambda view: {c for c in view.cases if on_the_wall(c)}
        same = [v.scored["accuracy"] for v in views.values() if v.state != "running"
                and v.name.startswith("ours:") == self.name.startswith("ours:")
                and same_settings(v.settings, settings) and v.summary()["n"] and wall(v) == wall(self)
                and all(r.get("ticket_print") in (None, PRINTS.get(c)) for c, r in v.results.items() if KEY.get(c, {}).get("set") == "main")]
        return {"name": self.name, "ours": self.name.startswith("ours:"), "settings": dict(settings, ours=self.name.startswith("ours:"), outside=outside(self.name)),      # outside: from my-work/experiments/, not made by the harness
                "summary": dict(self.summary(), takes=takes(same) if same else None, stale=stale, older_data=bool(stale.get("main")),
                                older_agent=settings.get("fingerprint") != agent_fingerprints().get(settings.get("agent")))}

    def whole(self):
        return dict(self.head(), cases=[c for c in self.cases.values() if on_the_wall(c["id"])], notices=self.notices[-5:], report=self.report,
                    files=read_json(run_dir(self.name) / "agent-files.json"))


def agent_fingerprints(cache={"at": 0.0, "value": {}}):
    """version -> fingerprint of the folder as it is on disk now. Read again at most every 3 seconds."""
    if time.time() - cache["at"] > 3:
        cache["value"] = {a["version"]: a["fingerprint"] for a in agents()}
        cache["at"] = time.time()
    return cache["value"]


def load_answer_key(seen={"stamp": None, "at": 0.0}):
    """Read the labels at the start, and again whenever it, a case file or a photo changes on disk (looked at every 2 s).
    Every run is then read and graded anew, so the page never shows new cases under old labels."""
    if time.time() - seen["at"] < 2 and not seen.pop("now", False):
        return
    seen["at"] = time.time()
    files = [LABELS] + [case_dir(c) / name for c in case_ids() for name in ("ticket.json", "order.json", "photo.jpg", "label.json")]
    on = new_tests_on()
    if seen.get("on") is False and on:
        WALL["set"] = "new"       # they were turned on this moment: show them
    if not on:
        WALL["set"] = "main"
    seen["on"] = on
    stamp = (on, WALL["set"]) + tuple(p.stat().st_mtime if p.exists() else 0 for p in files)
    if stamp == seen["stamp"]:
        return
    labels, ids, fingerprint = load_labels(), case_ids(), dataset_fingerprint()     # read everything first ...
    prints = {c: ticket_print(c) for c in ids}
    if WALL["set"] == "new":      # every page is about the set called main: for the while, the new tests carry that name and the 24 wait
        for label in labels.values():
            label["set"] = {"main": "first", "new": "main"}.get(label["set"], label["set"])
    if not labels:
        return          # the labels are being written this very moment: keep what we have and look again in 2 s
    with state_lock:    # ... then swap it in at once
        KEY.clear()
        KEY.update(labels)
        CASE_IDS.clear()
        CASE_IDS.update(ids)
        DATASET["fingerprint"] = fingerprint
        PRINTS.clear()
        PRINTS.update(prints)
        views.clear()
        turned = seen["stamp"] is not None and seen["stamp"][:2] != stamp[:2]
        seen["stamp"] = stamp
    if turned:
        broadcast({"reload": True})       # other tickets on the wall: every open page starts afresh


def watch():
    """Every quarter second: look for log files that grew, and tell the page what changed."""
    seen = None
    while True:
        try:
            if FOLLOW["set"]:
                if new_tests_on() and FOLLOW["set"] != WALL["set"]:
                    WALL["set"] = FOLLOW["set"]
                    load_answer_key.__defaults__[0]["now"] = True
                FOLLOW["set"] = None
            load_answer_key()
            if seen != versions.stamp():       # the log grew, or somebody (Claude Code, the editor) saved a file of the working copy
                seen = versions.stamp()
                broadcast({"versions": versions.view()})
        except Exception:
            pass              # a file vanished between two looks (an export is under way): the next look is 2 s away
        try:
            on_disk = list_runs(ours=True)
        except OSError:
            on_disk = list(views)       # a folder vanished between two looks
        with state_lock:
            for name in [n for n in views if n not in on_disk]:      # a run folder that was deleted or moved aside
                del views[name]
        for name in on_disk:
            try:
                with state_lock:
                    view = views.get(name)
                    reset = view is None or view.started_over()
                    if reset:
                        view = views[name] = RunView(name)
                    if view.path().stat().st_size > view.offset:
                        seen_notices = len(view.notices)
                        changed = view.read_new_lines()
                        broadcast({"run": view.head(), "cases": [view.cases[c] for c in changed if on_the_wall(c)], "reset": reset,
                                   "notices": view.notices[seen_notices:], "report": view.report})
            except Exception:
                pass          # the folder is being moved right now, or a log holds something odd: the watch goes on
        time.sleep(0.25)


def whole_or_nothing(view):
    """One run folder that cannot be read (copied from another kit, cut off, edited by hand) must not take the others down with it."""
    try:
        return view.whole()
    except Exception as error:
        if complaints.get("run " + view.name) != str(error):
            complaints["run " + view.name] = str(error)
            print("Leaving out the run %s: %s: %s" % (view.name, type(error).__name__, error), flush=True)
        return None


def all_runs(ours=False, caches={}):
    """The answer to /api/runs, as bytes: your runs, or with ours=True our saved ones as well. With many saved runs it is
    megabytes, so it is built once per change and every tab that asks gets the same bytes. The lock is held while building,
    never while sending."""
    cache = caches.setdefault(ours, {"key": None, "body": b"[]"})
    with one_big_answer:
        with state_lock:
            wanted = [v for v in views.values() if ours or not v.name.startswith("ours:")]
            key = (WALL["set"], int(time.time() / 5), tuple(sorted((v.name, v.offset, v.state) for v in wanted)))
            if key != cache["key"]:
                cache["body"] = json.dumps([w for w in map(whole_or_nothing, wanted) if w], ensure_ascii=False).encode("utf-8")
                cache["key"] = key
        return cache["body"]


def broadcast(message):
    data = json.dumps(message, ensure_ascii=False)
    for q in list(listeners):
        try:
            q.put_nowait(data)
        except queue.Full:        # a tab that stopped reading: let it go. When it comes back it fetches everything anew.
            listeners.remove(q)


def dataset():
    """Every case with its label, for the reading views."""
    index = {row["id"]: row for row in read_json(SHOP / "tickets" / "index.json", [])}
    out = []
    for case_id in case_ids():
        if (KEY.get(case_id) or {}).get("set") == "hidden":
            continue          # hidden tickets are scored, never shown: they are worth something only while nobody has read them
        case = load_case(case_id)
        out.append({"id": case_id, "ticket": case["ticket"], "order": case["order"], "label": KEY.get(case_id),
                    "item": index.get(case_id, {}).get("item"),
                    "photo_version": int(case["photo"].stat().st_mtime) if case["photo"].exists() else 0})
    return out


complaints = {}           # version -> the last thing we said about a folder that cannot be read


def agents():
    out = []
    for version in agent_versions():
        try:
            a = load_agent(version)
        except (Exception, SystemExit) as error:     # a folder caught in the middle of an edit: leave it out for now
            if complaints.get(version) != str(error):     # say it once, not every few seconds
                complaints[version] = str(error)
                print("Leaving out the version %s for now: %s" % (version, error), flush=True)
            continue
        complaints.pop(version, None)
        files = {k + ".md": v for k, v in a["prompts"].items() if k != "reporter"}
        files["agent.json"] = json.dumps(a["config"], indent=2)
        files.update({"handbook/" + k: v for k, v in a["handbook"].items()})
        out.append({"version": version, "ours": not version.isdigit(), "config": a["config"], "fingerprint": a["fingerprint"], "files": files})
    return out


def course():
    """The workshop's words, written by people, passed on to the page untouched."""
    found = {"course": read_json(WORKSHOP / "modules.json"), "course_facts": read_json(WORKSHOP / "facts.json")}
    return dict({k: v for k, v in found.items() if v is not None}, **background())


def background():
    """Reading matter for the page: the store's rule book, and the tools as they are deployed. Nothing of the answer key."""
    from . import tools
    text = lambda path: path.read_text(encoding="utf-8") if path.exists() else ""
    chapters = sorted((SHOP / "handbook").glob("*.md"), key=lambda p: (p.name != "SKILL.md", p.name))       # the contents page first
    everything = {"tools": list(tools.TOOLS)}
    return {"rulebook": [{"name": p.name, "text": text(p)} for p in chapters],
            "tools": {"readme": text(SHOP / "systems" / "README.md"),
                      "samples": {name: read_json(SHOP / "systems" / ("sample-%s.json" % name)) for name in tools.TOOLS},
                      "definitions": tools.definitions(everything, "client"),        # name, description, input schema of all four
                      "run_in": tools.RUN_IN,                                       # "sandbox": the look-ups are deployed as skills; "client": answered here
                      "note": tools.note(everything, tools.RUN_IN)}}                # how a version's prompt is told to call them


def models():
    return list(read_json(PRICES, {}).get("models", {}))


def running():
    return child is not None and child.poll() is None


def start_runner(options):
    """Start the runner as its own process. Returns the command line, to show on the page."""
    global child, stopping
    for _ in range(40):       # a runner whose run has ended, or was told to stop, needs a moment to leave
        if not running() or (not stopping and any(v.state == "running" for v in views.values())):
            break
        time.sleep(0.1)
    if why_no_live_runs():
        return {"error": "Set up first: " + why_no_live_runs() + "."}
    with state_lock:          # two quick clicks on Run must not start two runners
        if running():
            return {"error": "The last run is still stopping. Try again in a few seconds." if stopping
                    else "A run is already going. Press Stop first."}
        stopping = False
        only = str(options.get("only") or "")
        if (options.get("version") not in [None, ""] + agent_versions() or options.get("model") not in models()
                or options.get("effort") not in ("", None, "low", "medium", "high", "xhigh", "max")
                or not re.fullmatch(r"|\d+|starter|failures:(ours:)?[\w.~-]+", only)
                or (only.startswith("failures:") and only.split(":", 1)[1] not in views)):
            return {"error": "Pick the version, model, effort and cases from the menus."}
        if not options.get("version") and versions.view()["working"]["error"]:      # say it on the page, now, rather than in the runner's terminal
            return {"error": versions.view()["working"]["error"]}
        sets = str(options.get("set") or "")
        if not re.fullmatch(r"|(main|hidden|new|harder)(,(main|hidden|new|harder))*", sets) or ("new" in sets and not new_tests_on()):
            return {"error": "Pick the set of tickets from the menu."}
        command = ["run"] + ([options["version"]] if options.get("version") else []) + ["--model", options["model"],      # no version: the working copy
                   "--concurrency", str(max(1, min(200, int(options.get("concurrency", 10)))))]
        if versions.one_line(options.get("note")):
            command.append("--note=" + versions.one_line(options["note"]))      # (in one word: a note may begin with a hyphen)
        if options.get("effort"):
            command += ["--effort", options["effort"]]
        if sets:
            command += ["--set", sets]
        elif only:
            command += ["--only", only]
        else:
            command.append("--all")            # the page says what it wants: no choice of tickets means all 64, in a room too
        if options.get("fresh"):
            command.append("--fresh")
        if str(options.get("take") or "").isdigit() and 1 < int(options["take"]) < 100:
            command += ["--take", str(int(options["take"]))]
        if str(options.get("repeat") or "").isdigit() and 1 < int(options["repeat"]) <= 10:
            command += ["--repeat", str(int(options["repeat"]))]
        child = subprocess.Popen([sys.executable, "-m", __package__] + command, cwd=str(ROOT))
        return {"command": "./rd " + " ".join('"%s"' % c if " " in c else c for c in command)}


def stop_runner():
    """Ask every run that is going to stop, whoever started it: the runner looks for a file named STOP in its folder."""
    global stopping
    with state_lock:
        stopping = True
        if running():
            child.terminate()
        for view in views.values():        # the file reaches runs that were started in a terminal, too
            if view.state == "running" and not view.name.startswith("ours:") and (RUNS / view.name).is_dir():
                (RUNS / view.name / "STOP").write_text("")
    return {"ok": True}


def why_no_live_runs():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "no API key yet: type it into the first page"
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 20              # seconds: reading from or writing to a tab that has vanished fails instead of hanging

    def log_message(self, *args):
        pass

    def send(self, body, content_type="application/json", status=200, cache=False):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")        # no other site may put this page in a frame
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline' data:; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def local_only(self):
        """The page is for this laptop only. Refuse what another site's page or another host name could have sent."""
        host = self.headers.get("Host") or ""
        origin = self.headers.get("Origin")
        return host.split(":")[0] in ("127.0.0.1", "localhost") and (not origin or urlparse(origin).netloc == host)

    def do_GET(self):
        if not self.local_only():
            return self.send({"error": "This dashboard answers only to its own page on this laptop."}, status=403)
        url = urlparse(self.path)
        if url.path == "/":        # the page gets the token that its own requests must carry; no other site can read it
            page = (HARNESS / "dashboard.html").read_text(encoding="utf-8")
            return self.send(page.replace("/*STATIC*/", 'window.DESK_TOKEN = "%s";' % TOKEN).encode("utf-8"), "text/html; charset=utf-8")
        if url.path == "/api/setup":
            return self.send(setup.status())
        if url.path == "/api/dataset":
            return self.send({"cases": dataset(), "agents": agents(), "live": not why_no_live_runs(), **course(),
                              "mode": "presenter" if setup.presenter() else "room", "starter": starter_ids(),
                              "why_not_live": why_no_live_runs(), "models": models(), "wall": WALL["set"], "new_tests": new_tests_count(),
                              "concurrency": default_concurrency()})
        if url.path == "/api/agents":
            return self.send(agents())
        if url.path == "/api/versions":       # the log of your versions, and how the working copy stands to it
            return self.send(versions.view())
        if url.path == "/api/runs":           # your runs; ?ours=1: our saved ones as well; ?name=<run>: that one
            wanted = parse_qs(url.query).get("name")
            if not wanted:
                return self.send(all_runs(ours=parse_qs(url.query).get("ours") == ["1"]))
            with state_lock:
                answer = [w for w in (whole_or_nothing(v) for v in views.values() if v.name in wanted) if w]
            return self.send(answer)
        if url.path == "/api/myjudge":
            from . import claim
            try:
                return self.send(claim.view())
            except Exception:
                return self.send({"error": "The judge's files could not be read."}, status=500)
        if url.path == "/api/platform":         # a run's sessions, and what the platform said of them when it was last asked
            with state_lock:
                view = views.get((parse_qs(url.query).get("run") or [""])[0])
            try:
                found = records.platform(view.name) if view else None
            except Exception:
                found = None
            if not found:
                return self.send({"error": "not found"}, status=404)
            found["sessions"] = [s if shown(s["case"]) else dict(s, case="hidden", decision_event=None) for s in found["sessions"]]
            return self.send(found)
        if url.path in ("/api/session", "/api/fleet"):
            asked = {k: v[0] for k, v in parse_qs(url.query).items()}
            with state_lock:
                view = views.get(asked.get("run"))
                grades = {c["id"]: c["grade"]["correct"] for c in view.cases.values() if c.get("grade")} if view else {}
            if view is None or (url.path == "/api/session" and not re.fullmatch(r"[\w-]+", asked.get("id", ""))):
                return self.send({"error": "not found"}, status=404)
            try:
                found = records.fleet(view.name, grades, [c for c in KEY if on_the_wall(c)]) if url.path == "/api/fleet" else records.load(view.name, asked["id"])
            except Exception:          # a run folder from elsewhere, or edited by hand
                found = None
            if found and url.path == "/api/fleet":         # hidden tickets: when they ran, not what they were
                found["sessions"] = [s if shown(s["case"]) else dict(s, case="hidden", decision=None, correct=None, session_id=None)
                                     for s in found["sessions"]]
                found["retries"] = [r if shown(r["case"]) else dict(r, case="hidden", why="") for r in found["retries"]]
            elif found and (not found.get("case") or not shown(found["case"])):      # a record that belongs to no ticket may speak of any
                found = None
            return self.send(found, status=200) if found else self.send({"error": "This session has no saved record."}, status=404)
        if url.path == "/events":
            return self.stream()
        parts = url.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "cases" and parts[1] in CASE_IDS and shown(parts[1]) and parts[2] in ("photo.jpg", "thumb.jpg"):
            path = case_dir(parts[1]) / parts[2]
            if not path.exists():
                path = case_dir(parts[1]) / "photo.jpg"
            if not path.exists():
                return self.send(NO_PHOTO, "image/svg+xml")
            return self.send(path.read_bytes(), "image/jpeg", cache=True)
        self.send({"error": "not found"}, status=404)

    def do_POST(self):
        if not self.local_only() or self.headers.get("Content-Type") != "application/json":
            self.close_connection = True       # the body was not read: it must not be taken for the next request
            if not self.local_only():
                return self.send({"error": "This dashboard answers only to its own page on this laptop."}, status=403)
            return self.send({"error": "Expected application/json."}, status=415)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= 1_000_000:
                self.close_connection = True
                return self.send({"error": "That request is too large."}, status=413)
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send({"error": "The request body is not JSON."}, status=400)
        if not isinstance(body, dict):
            return self.send({"error": "The request body must be a JSON object."}, status=400)
        if not secrets.compare_digest((self.headers.get("X-Desk-Token") or "").encode("utf-8", "replace"), TOKEN.encode()):      # every request that changes something
            return self.send({"error": "This request did not come from the dashboard's own page."}, status=403)
        if self.path == "/api/setup/key":         # one field, one button: take the key, then do every step, in the background. The page polls
            if not isinstance(body.get("key"), str) or not re.fullmatch(r"[\x21-\x7e]{20,300}", body["key"].strip()):
                return self.send({"error": "That does not look like an API key."}, status=400)
            if setup.busy.locked():
                return self.send({"started": False, "error": "Another step is still running. Try again when it has finished."}, status=409)
            setup.set_key(body["key"])
            threading.Thread(target=setup.run, args=("all",), daemon=True).start()
            return self.send({"started": True})
        if self.path == "/api/wall":              # show the 24, or the new tests; "on" turns them on first
            if body.get("set") not in ("main", "new"):
                return self.send({"error": "The wall shows main or new."}, status=400)
            if body["set"] == "new" and not new_tests_on():
                if body.get("turn_on") is not True:
                    return self.send({"error": "The new tests are off."}, status=400)
                turn_new_tests(True)
            WALL["set"] = body["set"]
            load_answer_key.__defaults__[0]["now"] = True
            load_answer_key()
            return self.send({"wall": WALL["set"]})
        if self.path == "/api/mode":              # 16 practice tickets (a full room shares one rate limit) or all 64
            if body.get("mode") not in ("room", "presenter"):
                return self.send({"error": "The mode is room or presenter."}, status=400)
            os.environ["DESK_MODE"] = body["mode"]
            write_door()
            return self.send({"mode": body["mode"]})
        if self.path == "/api/setup/run":
            step = body.get("step", "all")
            if step != "all" and step not in setup.step_ids():
                return self.send({"error": "There is no such step."}, status=400)
            if setup.busy.locked():
                return self.send({"started": False, "error": "Another step is still running. Try again when it has finished."}, status=409)
            threading.Thread(target=setup.run, args=(step,), daemon=True).start()
            return self.send({"started": True})
        if self.path == "/api/setup/remove":
            if setup.busy.locked():
                return self.send({"started": False, "error": "Another step is still running. Try again when it has finished."}, status=409)
            threading.Thread(target=setup.remove, daemon=True).start()
            return self.send({"started": True})
        if self.path == "/api/forge":       # no model call, nothing is stored: the checks in code, tried on a made-up decision
            if (not isinstance(body.get("case"), str) or body["case"] not in CASE_IDS or not shown(body["case"])       # (a hidden ticket's record would show through)
                    or not isinstance(body.get("version"), (str, type(None))) or body.get("version") not in [None] + agent_versions()):
                return self.send({"error": "There is no such ticket or version."}, status=404)
            return self.send(forge(body["case"], body.get("version"), {"decision": body.get("decision"), "amount": body.get("amount")}))
        if self.path in ("/api/restore", "/api/agent-file"):
            try:
                if self.path == "/api/restore":
                    return self.send(versions.restore(str(body.get("version"))))
                versions.save_file(body.get("file"), str(body.get("text") or ""))
                return self.send({"saved": True})
            except (ValueError, TypeError) as error:
                return self.send({"error": str(error)}, status=400)
        if self.path == "/api/judge-file":
            from . import claim
            try:
                claim.save(str(body.get("file")), body.get("text"))
                return self.send({"saved": True})
            except (ValueError, TypeError) as error:
                return self.send({"error": str(error)}, status=400)
        if self.path == "/api/command":
            return self.hand_over(body.get("argv"))
        if self.path == "/api/start":
            try:
                return self.send(start_runner(body))
            except (AttributeError, TypeError, ValueError):
                return self.send({"error": "Pick the version, model, effort and cases from the menus."}, status=400)
        if self.path == "/api/stop":
            return self.send(stop_runner())
        self.send({"error": "not found"}, status=404)

    def hand_over(self, argv):       # (not "command": the base class keeps the request's method under that name)
        """./rd in a terminal has no key when the key was typed into the Setup page. It then hands its command to us,
        we run it with our key, and pass on what it prints. The key stays in this process and its children."""
        allowed = ("run", "setup", "pull", "cleanup", "grade", "prove-tools")
        if (not isinstance(argv, list) or not argv or argv[0] not in allowed
                or not all(isinstance(a, str) and (re.fullmatch(r"[\w.,:=~ +-]{1,400}", a) or ((argv[i - 1] == "--note" or a.startswith("--note=")) and len(a) <= 400 and a.isprintable()))
                           for i, a in enumerate(argv))):      # (a note is a sentence: any printable text. Nothing here passes through a shell)
            return self.send({"error": "That command cannot be handed to the dashboard."}, status=400)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        job = subprocess.Popen([sys.executable, "-u", "-m", __package__] + argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for line in job.stdout:
                self.wfile.write(line)
                self.wfile.flush()
            ended = job.wait()
            if argv[0] == "cleanup" and ended == 0:
                setup.state.clear()            # what the first page showed as deployed is gone
            self.wfile.write(b"::exit %d\n" % ended)
        except OSError:
            job.terminate()       # the terminal went away: so does its command

    def stream(self):
        """Server-sent events: one long response, one 'data:' line per change."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q = queue.Queue(maxsize=300)
        listeners.append(q)
        try:
            while q in listeners:
                try:
                    data = q.get(timeout=10)
                    self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": still here\n\n")
                self.wfile.flush()
        except OSError:
            pass
        finally:
            if q in listeners:
                listeners.remove(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, socket.timeout)):
            return      # a tab that closed in the middle of an answer is not worth a traceback in the terminal
        super().handle_error(request, client_address)


NO_PHOTO = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 30"><rect width="40" height="30" fill="#efede4"/>'
            b'<text x="20" y="17" font-size="4" text-anchor="middle" fill="#64625b" font-family="sans-serif">no photo</text></svg>')


def write_door():
    """How ./rd in a terminal finds us, and which mode we are in. Only this user can read the file."""
    door = ROOT / ".desk-server.json"
    door.touch(mode=0o600)
    door.write_text(json.dumps({"port": PORT["port"], "token": TOKEN, "pid": os.getpid(), "mode": os.environ.get("DESK_MODE") or "room"}))
    return door


PORT = {}


def serve(port=8321, open_browser=False):
    load_answer_key()
    other = read_json(ROOT / ".desk-server.json") or {}
    if isinstance(other.get("pid"), int) and other["pid"] > 1:       # one dashboard per folder: a second would take ./rd away from the first, which holds the key
        try:
            os.kill(other["pid"], 0)          # (signal 0 only asks whether it is alive)
        except OSError:
            pass                              # a file left by a dashboard that was killed
        else:
            raise SystemExit("A dashboard of this folder is running already: http://127.0.0.1:%s" % other.get("port"))
    try:
        server = Server(("127.0.0.1", port), Handler)
    except OSError:
        raise SystemExit("Port %d cannot be used. Is a dashboard already open at http://127.0.0.1:%d ? Otherwise take another port: DESK_PORT=8400 ./start.sh" % (port, port))
    PORT["port"] = port
    try:
        RUNS.mkdir(parents=True, exist_ok=True)
        door = write_door()
    except OSError:
        raise SystemExit("This folder cannot be written to. Move it to your home folder and start again.")
    threading.Thread(target=watch, daemon=True).start()
    address = "http://127.0.0.1:%d" % port
    reason = why_no_live_runs()
    print("Refund desk dashboard: %s   Ctrl-C to quit" % address)
    print("Ready for live runs." if not reason else "Next: %s." % reason, flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, [address]).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        door.unlink(missing_ok=True)
        if running():
            child.terminate()     # the dashboard takes the runner it started with it; runs started in a terminal go on
