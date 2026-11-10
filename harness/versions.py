"""Versions that log themselves.

You have one agent: the files in my-work/agent/. Nobody names or numbers its versions by hand. Whenever the agent is deployed or run,
the harness looks whether those files have changed. If so, it keeps a copy in my-work/versions/<n>/ and adds an entry to the log:
my-work/versions/LOG.md for people, log.json for programs. A run always runs such a copy, never the working files, so the log says
exactly what ran. (Managed Agents works the same way: every update makes a new agent version, and every session pins one.)
"""
from __future__ import annotations

import contextlib
import difflib
import fcntl
import hashlib
import os
import shutil
import time

from .common import MY, ROOT, load_agent, load_labels, no_answers, read_json, real, write_json

WORK = MY / "agent"             # the one working copy: you and Claude Code edit here
KEPT = MY / "versions"          # the copies and the log: only this file writes here


def files_in(folder) -> dict:
    """What makes an agent: agent.json, the prompts beside it, and the handbook's pages. File name -> text."""
    paths = [folder / "agent.json"] + sorted(folder.glob("*.md")) + sorted((folder / "handbook").rglob("*.md"))
    return {p.relative_to(folder).as_posix(): p.read_text(encoding="utf-8", errors="replace") for p in paths
            if p.is_file() and real(p) and not any(part.startswith(".") for part in p.relative_to(folder).parts)}      # no links, no .DS_Store and the like


def print_of(files: dict) -> str:
    return hashlib.sha256(repr(sorted(files.items())).encode("utf-8")).hexdigest()[:12]


def changes(before: dict, after: dict) -> list:
    """Which files differ, and by how many lines."""
    found = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            lines = list(difflib.unified_diff(before.get(name, "").splitlines(), after.get(name, "").splitlines(), lineterm="", n=0))[2:]
            found.append({"file": name, "added": sum(x.startswith("+") for x in lines), "removed": sum(x.startswith("-") for x in lines)})
    return found


@contextlib.contextmanager
def one_at_a_time():
    """Two runs started in the same instant must not both log 'version 3'."""
    try:
        KEPT.mkdir(parents=True, exist_ok=True)
        lock = open(KEPT / ".lock", "w")
    except OSError as error:
        raise SystemExit("my-work/versions/ cannot be written (%s). Move the kit to a folder of your own, your home folder for one." % error)
    with lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def read_log() -> dict:
    log = read_json(KEPT / "log.json")
    if isinstance(log, dict) and isinstance(log.get("versions"), list):
        return log
    # No log, or one that cannot be read: what can still be known is read off the kept copies, so that no version is lost with it.
    kept = sorted((p for p in KEPT.iterdir() if p.name.isdigit() and p.is_dir()), key=lambda p: int(p.name)) if KEPT.exists() else []
    return {"runs": [], "versions": [
        {"version": p.name, "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)), "ts": round(p.stat().st_mtime, 1), "print": print_of(files_in(p)),
         "note": "(the log was lost: this entry was read off the kept copy)", "from": None, "files": [], "platform": None} for p in kept]}


def entry(log: dict, version):
    return next((v for v in log["versions"] if v["version"] == str(version)), None)


def one_line(text) -> str:
    return " ".join(str(text or "").split())[:300]


# ── the heart of it ───────────────────────────────────────────────────────
def current(note: str = "", say=lambda text: None, ours=False) -> str:
    """Which version is the working copy? Returns its number as text ("1", "2", ...), the name by which load_agent() finds it.
    Logs a new version if the files match none so far. ours: the note is the harness's own words, not the person's."""
    with one_at_a_time():
        log, files, note = read_log(), files_in(WORK), one_line(note)
        mark = print_of(files)
        same = next((v for v in reversed(log["versions"]) if v["print"] == mark), None)
        if same:
            if print_of(files_in(KEPT / same["version"])) != mark:      # somebody touched the copy: it is what the log says again
                keep(same["version"], files)
            if note and not ours and (not same["note"] or same.get("note_is_ours")):      # a note of yours takes the place of none, or of the harness's own words
                same.update(note=note, note_is_ours=False)
            log["working_from"] = same["version"]
            write_log(log)
            say("Version %s, unchanged since %s." % (same["version"], same["time"]))
            return same["version"]
        number = str(1 + max([int(v["version"]) for v in log["versions"]] + [int(p.name) for p in KEPT.iterdir() if p.name.isdigit()] + [0]))
        keep(number, files)
        parent = log.get("working_from") if entry(log, log.get("working_from")) else (log["versions"][-1]["version"] if log["versions"] else None)
        changed = changes(files_in(KEPT / parent), files) if parent else []
        log["versions"].append({"version": number, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "ts": round(time.time(), 1), "print": mark,
                                "note": note or ("" if parent else "the first version"), "note_is_ours": ours or not (note or parent), "from": parent, "files": changed, "platform": None})
        log["working_from"] = number
        write_log(log)
        say("Version %s logged in my-work/versions/%s%s" % (number, number, ": " + in_words(changed) if changed else "."))
        return number


def keep(number: str, files: dict) -> None:
    """Write the copy beside its place, check that it loads as an agent, then move it in: a version is whole or not there."""
    not_empty(files)
    fresh = KEPT / (".new-%d" % os.getpid())
    shutil.rmtree(fresh, ignore_errors=True)
    try:
        for name, text in files.items():
            (fresh / name).parent.mkdir(parents=True, exist_ok=True)
            (fresh / name).write_text(text, encoding="utf-8")
        try:
            load_agent(number, fresh)          # the same checks a run makes: valid JSON, playbook, roles, tools
        except SystemExit as error:
            raise SystemExit(str(error).replace(os.path.relpath(str(fresh), str(ROOT)), "my-work/agent").rstrip(".") + ". Nothing was logged and nothing runs.")
        shutil.rmtree(KEPT / number, ignore_errors=True)
        fresh.rename(KEPT / number)
    finally:
        shutil.rmtree(fresh, ignore_errors=True)


def not_empty(files: dict) -> None:
    for name in ("prompt.md", "handbook/SKILL.md"):
        if not files.get(name, "").strip():
            raise SystemExit("my-work/agent/%s is %s. Nothing was logged and nothing runs.%s" % (
                name, "empty" if name in files else "missing", " ./rd restore <n> brings a version back." if read_log()["versions"] else ""))


def restore(version: str) -> dict:
    """The working copy becomes that version again. What no version holds yet is kept first, so nothing is ever lost:
    as a version of its own or, if it does not load as an agent, set aside as it is."""
    version, before = str(version), read_log()["versions"]
    if not entry({"versions": before}, version):
        raise ValueError("There is no version %s in my-work/versions/. There are: %s." % (version, ", ".join(v["version"] for v in before) or "none yet"))
    files = files_in(KEPT / version)
    if print_of(files) != entry({"versions": before}, version)["print"]:
        raise ValueError("my-work/versions/%s/ is no longer what was logged: somebody changed or removed it. Nothing was restored." % version)
    if WORK.is_symlink() or any(p.is_symlink() for p in WORK.rglob("*")):
        raise ValueError("There is a link in my-work/agent/. Links are not followed: remove it, then restore.")
    was = aside = None
    try:
        was = current("kept before going back to version %s" % version, ours=True)
    except SystemExit:                         # it does not load as an agent, so it cannot be a version: it is kept all the same
        if files_in(WORK):
            aside = KEPT / time.strftime("set-aside-%H%M%S")
            shutil.copytree(WORK, aside)
    with one_at_a_time():
        log = read_log()
        for name in set(files_in(WORK)) - set(files):
            (WORK / name).unlink()
        for name, text in files.items():
            (WORK / name).parent.mkdir(parents=True, exist_ok=True)
            (WORK / name).write_text(text, encoding="utf-8")
        log["working_from"] = version
        write_log(log)
    return {"restored": version, "kept_as": was if len(log["versions"]) > len(before) else None, "set_aside": aside and os.path.relpath(str(aside), str(ROOT))}


def save_file(file: str, text: str) -> None:
    """The dashboard's editor saves one file of the working copy."""
    if not isinstance(file, str) or file not in files_in(WORK):      # one of the files that are there, by its exact name: no path of the sender's making
        raise ValueError("There is no such file in my-work/agent/.")
    if len(text) > 400_000:
        raise ValueError("That text is too long.")
    before = (WORK / file).read_text(encoding="utf-8")
    (WORK / file).write_text(text, encoding="utf-8")
    if file == "agent.json":
        try:
            load_agent("agent", WORK)
        except (SystemExit, Exception) as error:
            (WORK / file).write_text(before, encoding="utf-8")
            raise ValueError("Not saved: %s" % error)


# ── what the harness adds to the log as things happen ─────────────────────
def deployed(version: str, platform: dict) -> None:
    """The platform's own name for this version: the agent's id, and its version number there."""
    with one_at_a_time():
        log = read_log()
        if entry(log, version):
            entry(log, version)["platform"] = {"agent_id": platform["id"], "agent_version": platform["version"]}
            write_log(log)


def record_run(name: str, state: str) -> None:
    """A run has ended: its score, cost and time go into the log. (Worked out from the run's own files, never typed.)"""
    from .score import extra_cost, load_run, run_timing, summarize
    run = load_run(name)
    s, about = summarize(run["results"], load_labels(), extra_cost(run["events"]), no_answers(run["events"])), run["settings"]
    row = dict({k: about.get(k) for k in ("model", "effort", "backend", "take", "started", "note")}, run=name, version=about.get("agent"), state=state,
               ended=time.strftime("%Y-%m-%d %H:%M:%S"), n=s["n"] + s["no_answer"], right=s["correct"], judged=bool(s.get("judged")), parts=[s["decision_right"], s["policy_right"]] if s["whole"] else None, accuracy=s["accuracy"] or 0.0,
               plus_minus=s["plus_minus"], cost_usd=round(s["cost_usd"] or 0, 4), seconds=run_timing(run).get("wall_s"),
               sets={k: {"accuracy": v["accuracy"], "n": v["n"]} for k, v in s["sets"].items() if k != "main"})
    with one_at_a_time():
        log = read_log()
        log["runs"] = [r for r in log.get("runs", []) if r["run"] != name] + [row]      # a run that was carried on has one line, its latest
        write_log(log)


# ── reading the log ───────────────────────────────────────────────────────
def words(version: str) -> dict:
    """Title, what changed and parent of one of your versions. Ours keep these in agent.json; yours come from the log, so nobody types them."""
    v = entry(read_log(), version) or {}
    return {"title": "version %s" % version, "what_changed": v.get("note") or in_words(v.get("files") or []), "made_from": v.get("from")}


def in_words(changed: list) -> str:
    return ", ".join("%s +%d -%d" % (c["file"], c["added"], c["removed"]) for c in changed)


def view() -> dict:
    """Everything the page shows about your versions: the log, oldest first, and how the working copy stands to it."""
    log, working = read_log(), {"same_as": None, "from": None, "files": [], "texts": {}, "error": None}
    try:
        working["texts"] = files = files_in(WORK)
        same = next((v for v in reversed(log["versions"]) if v["print"] == print_of(files)), None)
        parent = same or entry(log, log.get("working_from")) or (log["versions"] or [None])[-1]
        working["same_as"], working["from"] = same and same["version"], parent and parent["version"]
        working["files"] = changes(files_in(KEPT / parent["version"]), files) if parent and not same else []
        not_empty(files)                       # the same checks a run makes before anything is logged
        load_agent("agent", WORK)
    except (SystemExit, Exception) as error:
        working["error"] = str(error)[:300]
    return {"versions": [dict(v, runs=[r for r in log.get("runs", []) if r.get("version") == v["version"]]) for v in log["versions"]],
            "other_runs": [r for r in log.get("runs", []) if not entry(log, r.get("version"))], "working": working}


def stamp() -> tuple:
    """Changes whenever the log or a file of the working copy changes on disk. Cheap: the dashboard looks every quarter second."""
    paths = [KEPT / "log.json"] + ([p for p in WORK.rglob("*") if p.is_file()] if WORK.exists() else [])
    return tuple((str(p), p.stat().st_mtime_ns) for p in paths if p.exists())


def write_log(log: dict) -> None:
    """log.json is the log; LOG.md is drawn from it anew each time."""
    write_json(KEPT / "log.json", log)
    try:
        temporary = KEPT / (".LOG.%d.tmp" % os.getpid())
        temporary.write_text(in_markdown(log), encoding="utf-8")
        temporary.replace(KEPT / "LOG.md")
    except Exception:       # an entry from elsewhere that cannot be drawn must never stop a run
        pass


def in_markdown(log: dict) -> str:
    spent = lambda s: "%d min %02d s" % (s // 60, s % 60) if s else "-"

    def lines_of(runs):
        for r in sorted(runs, key=lambda r: r.get("started") or "", reverse=True):
            others = "".join(" · %s %.0f%% of %d" % (k, v["accuracy"], v["n"]) for k, v in sorted(r["sets"].items()))
            parts = r.get("parts")
            score = "**%d of %d %s** (%.0f%% ±%.0f)%s" % (r["right"], r["n"], ("handled" if r.get("judged") else "with decision and policy right") if parts else "right", r["accuracy"] or 0, r["plus_minus"] or 0,
                                                          " · decision %d, policy %d" % tuple(parts[:2]) if parts else "") if r["n"] else "no ticket of the main set"
            note = r.get("note") if r.get("note") != (entry(log, r.get("version")) or {}).get("note") else ""      # the run's own note, if it has one
            yield "- run %s · %s %s · %s · $%.2f · %s%s%s · `%s`%s" % (
                r.get("started") or "", r["model"], r["effort"] or "", score, r["cost_usd"], spent(r["seconds"]),
                others, "" if r["state"] == "finished" else " · " + r["state"], r["run"], " · " + note if note else "")      # (`...`: the run's folder in my-work/runs/)
    out = ["# The log of my agent", "",
           "The harness writes this file. Each time the agent in `my-work/agent/` is run after a change, its files are kept in `my-work/versions/<n>/`",
           "and an entry is added here, newest first. `./rd restore <n>` brings a version back. Do not edit.", ""]
    for v in reversed(log["versions"]):
        out += ["## %s · %s%s" % (v["version"], v["time"][:16], " · " + v["note"] if v["note"] else ""), ""]
        if v["from"]:
            out.append("- changed since %s: %s" % (v["from"], in_words(v["files"]) or "nothing"))
        if v["platform"]:
            out.append("- on the platform: agent `%s`, version %s" % (v["platform"]["agent_id"], v["platform"]["agent_version"]))
        out += list(lines_of(r for r in log.get("runs", []) if r.get("version") == v["version"])) + [""]
    others = [r for r in log.get("runs", []) if not entry(log, r.get("version"))]
    if others:
        out += ["## Runs of other versions", ""] + list(lines_of(others)) + [""]
    return "\n".join(out)
