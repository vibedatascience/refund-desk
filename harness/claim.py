"""The third part of a claim: is the reply fit to send?

Two graders, in this order:
  - code, for nothing, as soon as the agent hands the reply in: four checks (the money named, the language, the length, internal words);
  - a model judge, right after, in every run, on Claude Haiku 5.5 through the Messages API unless judge.json names another model (./rd grade judges a run again, with a changed judge). Its prompt is my-work/judge/judge.md, its settings judge.json.
    The first version is brute force: the whole rule book in its prompt, one Messages API call per reply.

A reply is fit to send when the four checks pass AND the judge finds every one of its rules kept, with a quality of at least pass_quality.
Its rules are the lines of judge.md that begin with a dash: it answers them one by one, with the words of the reply that decide.
Should a reply not be judged, its claim is scored on decision and policy alone, and every page says so.
The judge's verdicts are lines in the run's own events.jsonl ("judged"), so the pages follow it live. Its price is not in the agent's cost."""
from __future__ import annotations

import hashlib
import json
import random
import re
import time

from .common import HARNESS, KEY_DIR, MY, RUNS, ids_in, SHOP, load_case, load_labels, read_events, read_json, write_json

JUDGE = MY / "judge"
FILES = ("judge.md", "judge.json")
SEES = ("message", "record", "decision", "policy", "rulebook")
FORM = 2             # the shape of the judge's answer. Another shape is another judge


def rules_of(prompt: str) -> list:
    """The judge's rules: the lines of its prompt that begin with a dash, each with a check of its own in its form."""
    found = [re.sub(r"[*`_]", "", m).strip() for m in re.findall(r"^\s*[-*] +(.+)$", prompt, re.M)]
    return found[:12] or ["Does what the prompt asks"]


def form(judge: dict) -> dict:
    """The form the judge fills in: rule by rule, the words first and the verdict last, so that it looks before it decides."""
    one = lambda rule: {"type": "object", "description": rule, "required": ["words", "why", "kept"], "properties": {
        "words": {"type": "string", "description": "The exact words of the reply that decide this rule, at most 12 words. Empty if something is missing from the reply."},
        "why": {"type": "string", "description": "Why, in at most 12 plain words."},
        "kept": {"type": "string", "enum": ["yes", "no"]}}}
    rules = judge["rules"]
    return {"name": "submit_verdict", "description": "Record the verdict on this reply, rule by rule.", "input_schema": {
        "type": "object", "required": ["rules", "quality_why", "quality"], "properties": {
            "rules": {"type": "object", "required": ["rule_%d" % n for n in range(1, len(rules) + 1)], "properties": {"rule_%d" % n: one(rule) for n, rule in enumerate(rules, 1)}},
            "quality_why": {"type": "string", "description": "How good is the reply as a whole, in at most 15 plain words."},
            "quality": {"type": "integer", "description": "1 (bad) to 5 (excellent)"}}}}


def code_checks(case: dict, answer: dict) -> dict:
    """The four checks that code does, for a reply just handed in. Never raises."""
    from . import judge
    out = {"checks": {}, "ready": False, "judged": False, "cost_usd": 0.0}
    label = load_labels().get(case["id"])
    if not label or not answer.get("reply"):
        return dict(out, why="there is no reply")
    try:
        out["checks"] = judge.code_checks(case, label, answer, answer["reply"], read_json(HARNESS / "internal_words.json"))
    except Exception as error:
        return dict(out, why="the code checks failed: %s" % str(error)[:160])
    return out


# ── the evidence: what the agent says its decision rests on, checked by code ──
def plain_words(text: str) -> str:
    """Text as its words: no markdown, no quote marks, one space. What is compared is the wording, not the typography."""
    text = re.sub(r"[*_`#>|\u2018\u2019\u201c\u201d\"']", "", text.lower()).replace("\u2013", "-").replace("\u2014", "-")
    return re.sub(r"\s+", " ", text).strip(" .-")


def evidence(case: dict, answer: dict, guide: str) -> dict:
    """check id -> {"pass", "want", "got"}. Truths come from the records and the label; a fact that this claim does not have is left out."""
    label, record, ticket = load_labels().get(case["id"]) or {}, case["order"] or {}, case["ticket"]
    if not label or not isinstance(answer.get("facts"), dict):
        return {}
    facts, out, order, customer = answer["facts"], {}, record.get("order"), record.get("customer") or {}
    quote = plain_words(answer.get("policy_quote") or "")
    real = len(quote.split()) >= 4 and quote in plain_words(guide)
    out["quote"] = {"pass": real, "want": "in the guide, word for word", "got": answer.get("policy_quote") or ""}
    add = lambda key, want, got: out.__setitem__(key, {"pass": got == want, "want": want, "got": got})
    if order:
        email, gift = (ticket.get("from_email") or "").lower(), order.get("gift") or {}
        add("sender", "account_holder" if email == (customer.get("email") or "").lower() else "gift_recipient" if email and email == (gift.get("recipient_email") or "").lower() else "neither", facts.get("sender"))
        ship = next((s for s in order.get("shipments") or [] if label.get("line") in (s.get("lines") or [])), (order.get("shipments") or [{}])[0])
        day = lambda text: time.mktime(time.strptime(text[:10], "%Y-%m-%d"))
        if ticket.get("received_at"):
            add("days_since_delivery", int(round((day(ticket["received_at"]) - day(ship["delivered_on"])) / 86400)) if ship.get("delivered_on") else -1, facts.get("days_since_delivery"))
            lapsed = customer.get("folio_lapsed_on")
            add("member", customer.get("tier") not in (None, "standard") and not (lapsed and lapsed <= ticket["received_at"][:10]), facts.get("member"))
        if label.get("line"):
            add("line", label["line"], answer.get("line"))
    damage = label.get("damage") or {}
    if damage.get("item_damage"):
        add("photo_damage", "item" if damage["item_damage"] != "none" else "parcel_only" if damage.get("package_damage") not in (None, "none") else "none", facts.get("photo_damage"))
    if label.get("team") and answer.get("decision") == "escalate":
        add("team", label["team"], answer.get("team") or "")
    return out


# ── the judge ─────────────────────────────────────────────────────────────
def texts() -> dict:
    return {name: (JUDGE / name).read_text(encoding="utf-8") if (JUDGE / name).exists() else "" for name in FILES}


def load_judge() -> dict:
    """Your judge: its prompt and settings, checked, with the number of its version."""
    said = texts()
    try:
        config = json.loads(said["judge.json"])
        assert isinstance(config, dict)
    except (ValueError, AssertionError):
        raise SystemExit("my-work/judge/judge.json is missing or is not valid JSON.")
    if not said["judge.md"].strip():
        raise SystemExit("my-work/judge/judge.md is empty or missing: that is the judge's prompt.")
    model, bar = config.get("model", "claude-haiku-5-5"), config.get("pass_quality", 4)
    if not isinstance(model, str) or not model.startswith("claude-"):
        raise SystemExit("my-work/judge/judge.json: \"model\" must name a Claude model.")
    if not isinstance(bar, int) or isinstance(bar, bool) or not 1 <= bar <= 5:
        raise SystemExit("my-work/judge/judge.json: \"pass_quality\" must be a whole number from 1 to 5.")
    sees = [x for x in config.get("sees", list(SEES)) if x in SEES]
    print_ = hashlib.sha256(json.dumps([said["judge.md"], model, bar, sees, FORM]).encode("utf-8")).hexdigest()[:12]
    return {"prompt": said["judge.md"], "rules": rules_of(said["judge.md"]), "model": model, "pass_quality": bar, "sees": sees, "print": print_, "version": version_of(print_, said)}


def version_of(print_: str, said=None) -> int:
    """Judges log themselves like agents: a changed judge that is used gets the next number, and its files are kept."""
    path = JUDGE / "versions" / "log.json"
    log = read_json(path, [])
    log = log if isinstance(log, list) else []
    for n, entry in enumerate(log, 1):
        if entry.get("print") == print_:
            return n
    if said is None:
        return 0
    log.append({"print": print_, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
    folder = JUDGE / "versions" / str(len(log))
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in said.items():
        (folder / name).write_text(text, encoding="utf-8")
    write_json(path, log)
    return len(log)


def rulebook() -> str:
    return "\n\n".join("<file name=\"%s\">\n%s\n</file>" % (p.name, p.read_text(encoding="utf-8")) for p in sorted((SHOP / "handbook").glob("*.md")))


def system_of(judge: dict) -> str:
    return judge["prompt"] + ("\n\n<rulebook>\n" + rulebook() + "\n</rulebook>" if "rulebook" in judge["sees"] else "")


def ask(judge: dict, case: dict, answer: dict, system: str, call) -> tuple:
    """One reply, one call. (verdict, dollars). The judge never learns which model wrote the reply, nor the right answer."""
    from .task import POLICIES, as_data
    ticket, seen = case["ticket"], {}
    if "message" in judge["sees"]:
        seen["customer"] = {k: ticket.get(k) for k in ("from_name", "from_email", "subject", "message", "received_at")}
    if "record" in judge["sees"]:
        seen["record"] = case["order"]
    if "decision" in judge["sees"]:
        seen["decision_filed"] = {k: answer.get(k) for k in ("decision", "amount", "team", "reason")}
    if "policy" in judge["sees"] and answer.get("policy_section"):
        seen["policy_cited"] = "%s %s" % (answer["policy_section"], POLICIES.get(answer["policy_section"], ""))
    seen["reply"] = answer["reply"]
    cost, tool, names = 0.0, form(judge), ["rule_%d" % n for n in range(1, len(judge["rules"]) + 1)]
    for _ in range(3):
        fields, spent = call(judge["model"], system, "<claim>\n" + as_data(seen) + "\n</claim>", tool)
        cost += spent
        given = (fields or {}).get("rules")
        if isinstance(given, dict) and all(isinstance(given.get(n), dict) and given[n].get("kept") in ("yes", "no") for n in names) \
                and isinstance(fields.get("quality"), int) and not isinstance(fields["quality"], bool) and 1 <= fields["quality"] <= 5:
            break
    else:
        raise RuntimeError("the judge did not fill in its form, three times")
    rules = [{"rule": rule, "kept": given[n]["kept"] == "yes", "words": str(given[n].get("words") or "")[:160], "why": str(given[n].get("why") or "")[:200]}
             for n, rule in zip(names, judge["rules"])]
    broken = [r for r in rules if not r["kept"]]
    return {"rules": rules, "follows_policy": not broken, "quality": fields["quality"], "quality_why": str(fields.get("quality_why") or "")[:240], "bar": judge["pass_quality"],
            "why": "; ".join("%s: %s" % (r["rule"], r["why"]) for r in broken) or str(fields.get("quality_why") or "")[:240],
            "says_yes": not broken and fields["quality"] >= judge["pass_quality"]}, cost


def verdict_on(judge: dict, case_id: str, answer: dict, system: str, call) -> dict:
    """One verdict, ready to be written into the run."""
    verdict, cost = ask(judge, load_case(case_id), answer, system, call)
    return dict(verdict, judge=judge["version"], judge_print=judge["print"], model=judge["model"], cost_usd=round(cost, 6), reply_print=reply_print(answer["reply"]))


def line(case_id: str, v: dict) -> str:
    broken = [r["rule"] for r in v["rules"] if not r["kept"]]
    return "  %s  judge: %-11s quality %d%s" % (case_id, "fit to send" if v["says_yes"] else "not fit", v["quality"], "   broken: " + "; ".join(broken)[:90] if broken else "")


def grade_run(name: str, say=print, again: bool = False, call=None) -> dict:
    """Judge every reply of a run that this judge has not judged yet, and write each verdict into the run as it comes."""
    from . import judge as calls
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .score import load_run
    judge, call = load_judge(), call or calls.call
    results, system = load_run(name)["results"], system_of(judge)
    todo = {c: r["answer"] for c, r in results.items() if r["answer"].get("reply") and (again or (r["answer"].get("reply_grade") or {}).get("judge_print") != judge["print"])}
    if not any(r["answer"].get("reply") for r in results.values()):
        raise SystemExit("Run %s holds no replies to judge." % name)
    say("judge %d on %s reads %d replies of %s" % (judge["version"], judge["model"], len(todo), name))
    done, failed, spent, started = 0, 0, 0.0, time.time()
    path, unseen = RUNS / name / "events.jsonl", set(ids_in(("hidden",)))
    with ThreadPoolExecutor(max_workers=8) as pool, open(path, "a", encoding="utf-8") as out:
        work = {pool.submit(verdict_on, judge, c, a, system, call): c for c, a in todo.items()}
        for future in as_completed(work):
            case_id = work[future]
            try:
                verdict = future.result()
            except calls.Hopeless as error:
                for f in work:
                    f.cancel()
                raise SystemExit(str(error))
            except Exception as error:
                failed += 1
                say("  %s  not judged: %s" % ("a hidden claim" if case_id in unseen else case_id, str(error)[:120]))
                continue
            spent += verdict["cost_usd"]
            done += 1
            out.write(json.dumps({"ts": round(time.time(), 3), "type": "judged", "id": case_id, "verdict": verdict}) + "\n")
            out.flush()
            if case_id not in unseen:
                say(line(case_id, verdict))
    say("%d judged, %d could not be, $%.3f, %d s" % (done, failed, spent, time.time() - started))
    return {"run": name, "judge": judge["version"], "judged": done, "failed": failed, "cost_usd": round(spent, 4)}


def reply_print(reply: str) -> str:
    return hashlib.sha256(reply.encode("utf-8")).hexdigest()[:12]


def merge(answer: dict, verdict) -> dict:
    """An answer with the judge's verdict on its reply. A verdict on another reply (the claim was decided again) counts for nothing, and neither does one
    that is not shaped like a verdict: run files can be edited by hand."""
    whole = lambda x: isinstance(x, int) and not isinstance(x, bool)
    if (not isinstance(verdict, dict) or verdict.get("reply_print") != reply_print(answer.get("reply") or "") or not isinstance(verdict.get("says_yes"), bool)
            or not whole(verdict.get("quality")) or not 1 <= verdict["quality"] <= 5 or not whole(verdict.get("judge"))):
        return answer
    text = lambda key: str(verdict.get(key) or "")[:700]
    rules = [{"rule": str(r["rule"])[:300], "kept": r["kept"], "words": str(r.get("words") or "")[:200], "why": str(r.get("why") or "")[:300]}
             for r in verdict.get("rules") or [] if isinstance(r, dict) and isinstance(r.get("kept"), bool) and r.get("rule")][:12] if isinstance(verdict.get("rules"), list) else None
    cost = verdict.get("cost_usd")
    clean = {"follows_policy": verdict.get("follows_policy") is True, "quality": verdict["quality"], "quality_why": text("quality_why"), "why": text("why"), "says_yes": verdict["says_yes"],
             "judge": verdict["judge"], "model": text("model"), "bar": verdict["bar"] if whole(verdict.get("bar")) and 1 <= verdict["bar"] <= 5 else 3}
    if rules is not None:
        clean["rules"] = rules
    checks = (answer.get("reply_grade") or {}).get("checks") or {}
    by_code = {k: v for k, v in checks.items() if v.get("by") == "code"}
    return dict(answer, reply_grade={"checks": by_code, "judged": True, "ready": all(v["pass"] for v in by_code.values()) and verdict["says_yes"], "verdict": clean,
                                     "judge_print": text("judge_print"), "cost_usd": cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) and 0 <= cost < 1e6 else 0.0})


# ── is the judge any good? replies whose truth is known ───────────────────
def test(say=print, size: int = 32, call=None, unseen: bool = False) -> dict:
    """The judge on replies with a known truth: good ones, and ones with a fault planted, the same ones every time. unseen: the other replies, which
    nobody has read. Of those only the two counts are told: they show whether a better judge is better, or only fitted to the 32."""
    from . import judge as calls
    from concurrent.futures import ThreadPoolExecutor, as_completed
    judge, call = load_judge(), call or calls.call
    data = read_json(KEY_DIR / "judge" / "known-replies.json", {})
    good = sorted((r for r in data.get("replies", []) if r.get("kind") != "fault"), key=lambda r: r["id"])
    bad = sorted((r for r in data.get("replies", []) if r.get("kind") == "fault"), key=lambda r: r["id"])
    pick = random.Random(7)
    pick.shuffle(good), pick.shuffle(bad)
    items, system = (good[size // 2:] + bad[size // 2:]) if unseen else (good[:size // 2] + bad[:size // 2]), system_of(judge)
    if not items:
        raise SystemExit("The judge's test set is not in this kit.")
    say("judge %d on %s reads %d %sreplies" % (judge["version"], judge["model"], len(items), "unseen " if unseen else ""))
    rows, spent = [], 0.0

    def one(item):
        held = data["tickets"][item["case"]]
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        return ask(judge, {"id": item["case"], "ticket": held["ticket"], "order": held["order"]}, dict(decision, reply=item["reply"]), system, call)
    with ThreadPoolExecutor(max_workers=8) as pool:
        work = {pool.submit(one, item): item for item in items}
        for future in as_completed(work):
            item = work[future]
            try:
                verdict, cost = future.result()
            except calls.Hopeless as error:
                raise SystemExit(str(error))
            except Exception as error:
                say("  %s  not judged: %s" % ("one" if unseen else item["id"], str(error)[:100]))
                continue
            spent += cost
            rows.append({"id": item["id"], "truth": "bad" if item.get("kind") == "fault" else "good", "fault": str(item.get("fault_title") or item.get("fault") or "")[:120], "says_yes": verdict["says_yes"],
                         "quality": verdict["quality"], "follows_policy": verdict["follows_policy"], "why": verdict["why"], "rules": verdict["rules"], "reply": item["reply"][:1500]})
    n_bad, n_good = sum(r["truth"] == "bad" for r in rows), sum(r["truth"] == "good" for r in rows)
    out = {"judge": judge["version"], "model": judge["model"], "print": judge["print"], "ts": round(time.time(), 1), "bad": n_bad, "bad_stopped": sum(r["truth"] == "bad" and not r["says_yes"] for r in rows),
           "good": n_good, "good_stopped": sum(r["truth"] == "good" and not r["says_yes"] for r in rows), "cost_usd": round(spent, 4), "unseen": unseen, "rows": [] if unseen else sorted(rows, key=lambda r: r["id"])}
    folder = JUDGE / "tests"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / ("judge%d-%s%s.json" % (judge["version"], judge["model"], "-unseen" if unseen else "")), out)
    say("faulty replies stopped %d of %d   good replies wrongly stopped %d of %d   $%.3f" % (out["bad_stopped"], n_bad, out["good_stopped"], n_good, spent))
    missed = [r for r in out["rows"] if r["truth"] == "bad" and r["says_yes"]]
    for r in missed[:6]:
        say("  let through: %s  (%s)" % (r["id"], r["fault"]))
    return out


def view() -> dict:
    """Everything the judge's page shows: its files, its versions, its tests."""
    tests = []
    for path in sorted((JUDGE / "tests").glob("judge*.json")) if (JUDGE / "tests").exists() else []:
        found = read_json(path)
        if isinstance(found, dict) and isinstance(found.get("rows"), list):
            tests.append(found)
    log = read_json(JUDGE / "versions" / "log.json", [])
    now = {"version": 0, "error": None}
    try:
        said = texts(); config = json.loads(said["judge.json"])
        sees = [x for x in config.get("sees", list(SEES)) if x in SEES]
        now["version"] = version_of(hashlib.sha256(json.dumps([said["judge.md"], config.get("model", "claude-haiku-5-5"), config.get("pass_quality", 4), sees, FORM]).encode("utf-8")).hexdigest()[:12])
    except Exception as error:
        now["error"] = str(error)[:200]
    return {"texts": texts(), "rules": rules_of(texts()["judge.md"]), "versions": len(log) if isinstance(log, list) else 0, "now": now, "tests": sorted(tests, key=lambda t: t.get("ts") or 0)}


def save(file: str, text: str) -> None:
    if file not in FILES or not isinstance(text, str) or len(text) > 200_000:
        raise ValueError("There is no such file in my-work/judge/.")
    before = texts()[file]
    (JUDGE / file).write_text(text, encoding="utf-8")
    if file == "judge.json":
        try:
            config = json.loads(text)
            assert isinstance(config, dict) and str(config.get("model", "claude-")).startswith("claude-") and isinstance(config.get("pass_quality", 4), int) and 1 <= config.get("pass_quality", 4) <= 5
        except (ValueError, AssertionError):
            (JUDGE / file).write_text(before, encoding="utf-8")
            raise ValueError("Not saved: it must be valid JSON with a Claude \"model\" and a \"pass_quality\" from 1 to 5.")
