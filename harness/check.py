"""./rd check: does what lies in my-work/ keep to the format of my-work/FORMAT.md? Says in plain words what is wrong, and where."""
from __future__ import annotations

import json

from .common import DECISIONS, case_ids, ids_in, list_runs, outside, read_json, run_dir, sound
from .versions import KEPT, files_in, print_of, read_log

STATES = ("queued", "running", "retrying", "done", "failed")


def check_run(name: str) -> tuple:
    """(what is wrong, how many tickets have a decision)"""
    wrong, folder, known, hidden, decided = [], run_dir(name), set(case_ids()), set(ids_in(("hidden",))), set()
    settings = read_json(folder / "run.json")
    if not isinstance(settings, dict):
        return ["run.json is missing, or is not a JSON object"], 0
    for key in ("agent", "model"):
        if not isinstance(settings.get(key), str) or not settings[key]:
            wrong.append('run.json: "%s" is required, as text' % key)
    if not (folder / "events.jsonl").is_file():
        return wrong + ["events.jsonl is missing"], 0
    for number, line in enumerate((folder / "events.jsonl").read_text(encoding="utf-8", errors="replace").split("\n"), 1):
        if not line.strip():
            continue
        say = lambda words: wrong.append("events.jsonl, line %d: %s" % (number, words))
        try:
            event = json.loads(line)
        except ValueError:
            say("not JSON (each line is one whole JSON object)")
            continue
        if not isinstance(event, dict) or not isinstance(event.get("type"), str) or not isinstance(event.get("ts"), (int, float)):
            say('needs "type" (text) and "ts" (seconds since 1970, a number)')
        elif event["type"] == "case":
            if event.get("id") not in known:
                say("there is no ticket %r" % event.get("id"))
            elif outside(name) and event["id"] in hidden:
                say("%s is a hidden ticket: only the harness runs those, so this line is passed over" % event["id"])
            elif event.get("status") not in STATES:
                say('"status" must be one of %s' % ", ".join(STATES))
            elif event["status"] == "done" and not sound(event.get("result")):
                answer = (event.get("result") or {}).get("answer") if isinstance(event.get("result"), dict) else None
                say('"result" needs "answer": {"decision": ..., "amount": ...}' if not isinstance(answer, dict) else
                    '"decision" must be one of %s' % ", ".join(DECISIONS) if answer.get("decision") not in DECISIONS else
                    '"amount" must be a number of dollars, 0 or more' if not isinstance(answer.get("amount"), (int, float)) or isinstance(answer.get("amount"), bool) or not 0 <= answer["amount"] < 1e9 else
                    "a field has the wrong type: texts are texts, injection_detected is true or false, cost_usd and seconds are numbers, sessions is a list of objects")
            elif event["status"] == "done":
                decided.add(event["id"])
    return wrong, len(decided)


def check_versions() -> list:
    wrong = []
    for v in read_log()["versions"]:
        if not (KEPT / v["version"]).is_dir():
            wrong.append("version %s is in the log, but my-work/versions/%s/ is gone" % (v["version"], v["version"]))
        elif print_of(files_in(KEPT / v["version"])) != v["print"]:
            wrong.append("my-work/versions/%s/ is no longer what was logged: somebody edited it" % v["version"])
    return wrong


def main(name=None) -> int:
    """One run, or everything of yours. Returns the exit code: 0 when all is well."""
    bad = 0
    for run in [name] if name else list_runs():
        wrong, decided = check_run(run)
        bad += bool(wrong)
        print("%-6s %s: %d ticket%s decided%s" % ("WRONG" if wrong else "ok", run, decided, "" if decided == 1 else "s", "   (of your own making: my-work/experiments/)" if outside(run) else ""))
        for words in wrong[:10]:
            print("         " + words)
        if len(wrong) > 10:
            print("         ... and %d more" % (len(wrong) - 10))
    if not name:
        wrong = check_versions()
        bad += bool(wrong)
        print("%-6s the log of versions: %d versions" % ("WRONG" if wrong else "ok", len(read_log()["versions"])))
        for words in wrong:
            print("         " + words)
    return 1 if bad else 0
