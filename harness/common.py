"""Shared plumbing: where things live, loading cases and agents, and the run log.

A run is a folder (yours in my-work/runs/, ours in saved-runs/, if this copy has any) with two files:
  run.json      the settings of the run (agent version, model, backend, ...)
  events.jsonl  one line of JSON for everything that happened, in order
The runner only appends lines to a run (starting over moves the old folder aside).
The dashboard and the scorer only read them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import os
import re
import threading
import time
from pathlib import Path

# ── where everything lives. No other file spells out a folder's name. ─────
HARNESS = Path(__file__).absolute().parent      # the machinery: this package
ROOT = HARNESS.parent
MY = ROOT / "my-work"                           # the only place you and Claude Code change
SHOP = ROOT / "shop"                            # the made-up store: tickets, systems, handbook. Read-only
KEY_DIR = ROOT / "answer-key"                   # the labels, and the hidden tickets. Not to be opened
WORKSHOP = ROOT / "workshop"                    # the words of the dashboard's modules, the stage script

NEW_TESTS = SHOP / "new-tests"                 # the hidden tickets, once somebody has turned them on (./rd new-tests on)
TICKET_FOLDERS = [SHOP / "tickets", NEW_TESTS, KEY_DIR / "hidden-tickets"]
VERSION_FOLDERS = [MY / "versions"]             # "1", "2", ...: the harness logs them, see versions.py. No finished agent ships with the kit
RUNS = ROOT / os.environ.get("DESK_RUNS_DIR", "my-work/runs")      # your runs: empty when the kit is new
EXPERIMENTS = MY / "experiments"                # runs of your own making, in the same format: see my-work/FORMAT.md
OURS = ROOT / "saved-runs"                      # runs that ship with a copy of the kit, if any. Named "ours:<run>"
LABELS = KEY_DIR / "labels.json"
PRICES = HARNESS / "prices.json"


def case_dir(case_id: str) -> Path:
    """A ticket's folder: in the store, or among the hidden ones."""
    for folder in TICKET_FOLDERS:
        if (folder / case_id / "ticket.json").exists():
            return folder / case_id
    return TICKET_FOLDERS[0] / case_id


def agent_dir(version: str) -> Path:
    """An agent version's folder: one of your own, or one of ours."""
    for folder in VERSION_FOLDERS:
        if (folder / version / "prompt.md").exists():
            return folder / version
    return VERSION_FOLDERS[0] / version


DECISIONS = ["refund", "replace", "deny", "escalate"]


def load_env() -> None:
    """Read KEY=value lines from .env into the environment (real variables win)."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def default_concurrency() -> int:
    try:
        return max(1, int(os.environ.get("CONCURRENCY", "12")))
    except ValueError:
        return 12


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):       # not there, not readable, not JSON
        return default


def write_json(path, value) -> None:
    """Write to a temporary file, then rename: a reader never sees a half-written file."""
    temporary = Path("%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident()))      # its own name: several writers never collide
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


# ── cases ─────────────────────────────────────────────────────────────────
def case_ids() -> list:
    return sorted({p.name for folder in TICKET_FOLDERS if folder.exists() for p in folder.iterdir() if (p / "ticket.json").exists()})


def load_case(case_id: str) -> dict:
    """Everything the agent is allowed to see about one case. Labels are not in here."""
    folder = case_dir(case_id)
    return {
        "id": case_id,
        "ticket": read_json(folder / "ticket.json"),
        "order": read_json(folder / "order.json"),
        "photo": folder / "photo.jpg",
    }


def ticket_print(case_id: str) -> str:
    """Changes whenever this ticket, its order record or its photo changes. Every result remembers the print of what it decided on."""
    digest = hashlib.sha256()
    for name in ("ticket.json", "order.json", "photo.jpg"):
        path = case_dir(case_id) / name
        digest.update(path.read_bytes() if path.exists() else b"-")
    return digest.hexdigest()[:12]


def still_valid(results: dict, prints={}) -> dict:
    """The results whose ticket, order record and photo have not changed since they were decided.
    (Within one command each ticket's print is worked out once.)"""
    for c in results:
        if c not in prints:
            prints[c] = ticket_print(c)
    return {c: r for c, r in results.items() if r.get("ticket_print") in (None, prints[c])}      # (a result that carries no print claims nothing)


def dataset_fingerprint() -> str:
    """Changes whenever a ticket, an order record or a photo of the main set changes. A run remembers which data it ran on."""
    digest = hashlib.sha256()
    for case_id in ids_in(("main",)):
        for name in ("ticket.json", "order.json", "photo.jpg"):
            path = case_dir(case_id) / name
            digest.update(path.read_bytes() if path.exists() else b"-")
    return digest.hexdigest()[:12]


def check_photos() -> None:
    """Each ticket's attachment record carries the sha256 of its photo. Stop if a photo on disk is not that file:
    a ticket must describe the picture that lies beside it."""
    wrong = []
    for case_id in case_ids():
        case = load_case(case_id)
        if not isinstance(case["ticket"], dict):          # an unreadable ticket fails alone when its turn comes
            continue
        record = next((a for a in case["ticket"].get("attachments") or [] if isinstance(a, dict)), {})
        if (case["photo"].exists() and record.get("sha256")       # (a ticket without a fingerprint has nothing to contradict)
                and record["sha256"] != hashlib.sha256(case["photo"].read_bytes()).hexdigest()):
            wrong.append(case_id)
    if wrong:
        raise SystemExit("The photo of %d cases (%s ...) is not the file their ticket.json describes (attachments[0].sha256). "
                         "The photos were changed without the tickets." % (len(wrong), ", ".join(wrong[:3])))


def load_labels() -> dict:
    """The answer key."""
    labels = read_json(LABELS, {})
    for label in labels.values():
        label.setdefault("theme", label["family"])      # the broad group a case family belongs to
        label.setdefault("set", "hidden" if label.get("held_out") else "main")
        if label["set"] == "hidden" and new_tests_on():
            label["set"] = "new"        # turned on: open to read, to run and to show, as a set of their own
    return labels


def new_tests_on() -> bool:
    return NEW_TESTS.is_dir()


def turn_new_tests(on: bool) -> int:
    """Turn the hidden tickets into the new tests, or back. On: they are copied into shop/new-tests/, where everybody may read them, and
    form the set "new". Runs that took them before keep their results. Returns how many there are."""
    if on and not new_tests_on():
        half = SHOP / ".new-tests-being-copied"
        shutil.rmtree(half, ignore_errors=True)
        shutil.copytree(KEY_DIR / "hidden-tickets", half)
        os.replace(half, NEW_TESTS)       # all at once, so that nobody sees half of them
    elif not on and new_tests_on():
        shutil.rmtree(NEW_TESTS)
    return sum(1 for p in (KEY_DIR / "hidden-tickets").iterdir() if (p / "ticket.json").exists())


def starter_ids() -> list:
    """The tickets of a live run in a full room: those marked "room16" in the labels. Without such marks, as in this kit, all of them."""
    labels = load_labels()
    marked = [c for c in ids_in(("main",)) if labels[c].get("room16")]
    return marked or ids_in(("main",))


def ids_in(sets=("main",)) -> list:
    """The cases of one or more sets. "main" is the wall everybody works on; "hidden" are tickets nobody has read;
    "harder" are tougher ones. A run covers "main" unless it is told otherwise."""
    labels = load_labels()
    return [c for c in case_ids() if (labels.get(c) or {}).get("set") in sets]       # (a folder without a label belongs to no set)


# ── agent versions ────────────────────────────────────────────────────────
def agent_versions() -> list:
    names = [p.name for folder in VERSION_FOLDERS if folder.exists() for p in folder.iterdir()
             if (p / "prompt.md").exists() and (p.name.isdigit() or folder != VERSION_FOLDERS[0])]      # (in my-work/versions/ only the numbered folders are versions)
    return sorted(names, key=lambda n: (not n.isdigit(), int(n) if n.isdigit() else 0, n))      # yours in the order they were logged, then ours


def real(path: Path) -> bool:
    """Links are not followed: a link in a version's folder could pull in the answer key, or point a save at a file elsewhere."""
    return not path.is_symlink() and not any(p.is_symlink() for p in path.parents if MY in p.parents)


def load_agent(version: str, folder=None) -> dict:
    """An agent version is a plain folder:
      agent.json   the model, the tools, and the playbook (single, or review: a second look); in ours also title and what changed
      prompt.md    the system prompt (a playbook with more roles has one more .md file per role)
      handbook/    the support handbook, uploaded as a skill
    """
    folder = folder or agent_dir(version)
    handbook = folder / "handbook"
    files = {p.relative_to(handbook).as_posix(): p.read_text(encoding="utf-8")
             for p in sorted(handbook.rglob("*.md")) if not p.name.startswith(".") and real(p)}     # text only: no .DS_Store and the like
    if "SKILL.md" not in files:
        raise SystemExit("%s/handbook/SKILL.md is missing: an agent version needs its handbook." % os.path.relpath(str(folder), str(ROOT)))
    prompts = {p.stem: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md")) if real(p)}
    prompts["reporter"] = (HARNESS / "reporter.md").read_text(encoding="utf-8")   # writes the end-of-run report
    try:       # a typo here must stop the run: read as "no settings", a version would quietly run without its tools and its guard
        settings = json.loads((folder / "agent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SystemExit("%s/agent.json cannot be read: %s" % (os.path.relpath(str(folder), str(ROOT)), error))
    if not isinstance(settings, dict):
        raise SystemExit("%s/agent.json must hold an object: { ... }." % os.path.relpath(str(folder), str(ROOT)))
    if folder.parent == VERSION_FOLDERS[0]:       # one of yours: its title and what changed come from the log, nobody types them
        from .versions import words
        settings = dict(settings, **words(version))
    config = dict({"title": version, "what_changed": "", "playbook": "single"}, **settings)
    needs = {"single": [], "review": ["reviewer"]}
    if config.get("model") is not None and not (isinstance(config["model"], str) and re.fullmatch(r"[\w.-]+", config["model"])):
        raise SystemExit('%s/agent.json: "model" must be a model\'s name: letters, digits, dots and hyphens.' % os.path.relpath(str(folder), str(ROOT)))
    if not isinstance(config["playbook"], str) or config["playbook"] not in needs:
        raise SystemExit("%s/agent.json: playbook is %r; it must be one of %s." % (
            os.path.relpath(str(folder), str(ROOT)), config["playbook"], ", ".join(needs)))
    for role in ["prompt"] + needs[config["playbook"]]:
        if role not in prompts:
            raise SystemExit("%s/%s.md is missing: this version's settings call for it." % (os.path.relpath(str(folder), str(ROOT)), role))
    behaviour = {k: v for k, v in config.items() if k not in ("title", "what_changed", "made_from")}     # words for people change nothing
    parts = [prompts, files, behaviour]
    deployed_tools = None
    if config.get("tools"):            # a version that works through the tools: their definitions are part of what it is
        from .tools import print_of
        deployed_tools = print_of(config)
        parts.append(deployed_tools)
    blob = json.dumps(parts, sort_keys=True).encode()
    return {"version": version, "config": config, "prompts": prompts, "handbook_dir": handbook, "tools": deployed_tools,
            "handbook": files, "fingerprint": hashlib.sha256(blob).hexdigest()[:12]}


DEFAULT_MODEL = "claude-haiku-5-5"      # what a run uses when neither the command line nor agent.json names a model


def default_effort(model: str):
    """The effort level a model uses when a request names none (prices.json keeps it beside the prices). None if unknown."""
    return read_json(PRICES, {}).get("models", {}).get(model, {}).get("default_effort")


# ── cost ──────────────────────────────────────────────────────────────────
def cost_usd(model: str, usage: dict, active_seconds: float = 0.0) -> float:
    """List price of the tokens, plus session runtime for Managed Agents sessions."""
    prices = read_json(PRICES, {})
    per_mtok = prices.get("models", {}).get(model)
    if not per_mtok:
        return 0.0
    tokens = (usage.get("input_tokens", 0) * per_mtok["input"]
              + usage.get("output_tokens", 0) * per_mtok["output"]
              + usage.get("cache_creation_input_tokens", 0) * per_mtok["cache_write"]
              + usage.get("cache_read_input_tokens", 0) * per_mtok["cache_read"]) / 1e6
    return round(tokens + active_seconds / 3600 * prices.get("session_hour", 0.0), 6)


# ── the run log ───────────────────────────────────────────────────────────
class RunLog:
    """Appends events to a run's events.jsonl. Safe to call from many threads."""

    def __init__(self, name: str, settings: dict):
        self.dir = RUNS / name
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.dir / "run.json", dict(settings, run=name))
        cut_short = (self.dir / "events.jsonl").exists() and not (self.dir / "events.jsonl").read_bytes().endswith(b"\n")
        self.file = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        if cut_short:                  # a runner that was killed in mid line: our first line must not be glued to its fragment
            self.file.write("\n")
        self.lock = threading.Lock()

    def emit(self, type: str, **fields) -> None:
        # Plain ASCII, one line: no character a model may emit (U+2028, say) can break a line in two.
        line = json.dumps(dict(ts=round(time.time(), 3), type=type, **fields), allow_nan=False)
        with self.lock:
            self.file.write(line + "\n")
            self.file.flush()


def read_events(name: str) -> list:
    path = run_dir(name) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            event = json.loads(line)
        except ValueError:
            continue      # the empty end of the file, or a last line cut short when a runner was killed
        if isinstance(event, dict) and "type" in event and isinstance(event.get("id"), (str, type(None))):
            events.append(event)
    if outside(name):      # nobody outside the harness can have read a hidden ticket, and guesses would sound the answer key out through the score
        hidden = set(ids_in(("hidden",)))
        events = [e for e in events if e.get("id") not in hidden]
        events = [e for e in events if e.get("type") != "judged"]
        for event in events:      # and a reply's grade is the harness's to give: a folder made by hand cannot bring its own
            answer = event.get("result").get("answer") if isinstance(event.get("result"), dict) else None
            if isinstance(answer, dict):
                answer.pop("reply", None), answer.pop("reply_grade", None), answer.pop("policy_section", None)
    return events


def finished_results(events: list) -> dict:
    """ticket id -> result, for every ticket that reached a decision. (A line that is not shaped like one of ours, in a folder
    that came from elsewhere or was edited by hand, counts for nothing and harms nothing.)"""
    from .claim import merge
    found = {e["id"]: sound(e["result"]) for e in events
             if e["type"] == "case" and e.get("status") == "done" and isinstance(e.get("id"), str) and sound(e.get("result"))}
    for e in events:      # the judge's verdicts are lines of their own, written after the run; the last one on this very reply holds
        if e["type"] == "judged" and e.get("id") in found:
            found[e["id"]] = dict(found[e["id"]], answer=merge(found[e["id"]]["answer"], e.get("verdict")))
    return found


WORDS = ("reason", "report", "team", "handbook_section", "photo_observation", "policy_section")      # what an answer may say besides its decision and amount


def sound(result):
    """A ticket's result with every field the scorer reads, or None if it is not shaped like one. Required: answer.decision and
    answer.amount. All else is optional and is filled in empty (my-work/FORMAT.md has the list)."""
    number = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool) and 0 <= x < 1e9      # (which also says no to NaN)
    answer = result.get("answer") if isinstance(result, dict) else None
    if (not isinstance(answer, dict) or answer.get("decision") not in DECISIONS or not number(answer.get("amount"))
            or not all(isinstance(answer.get(k, ""), str) for k in WORDS) or not isinstance(answer.get("injection_detected", False), bool)
            or not all(number(result.get(k) or 0) for k in ("cost_usd", "seconds"))
            or not isinstance(result.get("sessions", []), list) or not all(isinstance(s, dict) for s in result.get("sessions", []))):
        return None
    blank = dict(dict.fromkeys(WORDS, ""), injection_detected=False)
    if "reply" in answer and not isinstance(answer["reply"], str):
        return None
    marks = answer.get("reply_grade")
    if marks is not None and not (isinstance(marks, dict) and number(marks.get("cost_usd") or 0) and isinstance(marks.get("checks", {}), dict)
                                  and all(isinstance(v, dict) and isinstance(v.get("pass"), bool) for v in marks.get("checks", {}).values())):
        return None
    return {"sessions": [], **result, "answer": {**blank, **answer}}


def no_answers(events: list) -> int:
    """How many tickets of the main set were given up without a decision. (They count as wrong in the main set's score.)"""
    main = set(ids_in(("main",)))
    last = {e["id"]: e.get("status") for e in events if e["type"] == "case" and e.get("id") in main}
    return sum(status == "failed" for status in last.values())


def run_dir(name: str) -> Path:
    """Where a run lies: yours in my-work/runs/ (or, of your own making, in my-work/experiments/), ours ("ours:<run>") in saved-runs/."""
    if name.startswith("ours:"):
        return OURS / name[5:]
    return EXPERIMENTS / name if (EXPERIMENTS / name).is_dir() and not (RUNS / name).exists() else RUNS / name


def outside(name: str) -> bool:
    """Was this run written by somebody other than the harness?"""
    return run_dir(name).parent == EXPERIMENTS


def list_runs(ours=False) -> list:
    """Your runs; with ours=True the saved ones that ship as well, as "ours:<run>". Runs that were moved aside (.old-) never count."""
    found = lambda folder: sorted(p.name for p in folder.iterdir()
                                  if ".old-" not in p.name and isinstance(read_json(p / "run.json"), dict)) if folder.exists() else []
    own = [n for n in found(EXPERIMENTS) if re.fullmatch(r"[\w.~-]+", n) and real(EXPERIMENTS / n) and not (RUNS / n).exists()] if RUNS.parent == MY else []
    return found(RUNS) + own + (["ours:" + name for name in found(OURS)] if ours and OURS != RUNS else [])
