"""The refund desk's command line.  ./rd <command>

  setup                     what the dashboard's first page does: key, environment, agents, the checks, one live ticket
  run [--note "..."]        run your agent (my-work/agent/) over the tickets, live on Claude Managed Agents. If its files have changed,
                            the harness logs a new version by itself: my-work/versions/LOG.md
  score [<run>]             your runs, newest first, one line each; or one run's scorecard
  failures                  the tickets the last run got wrong: expected, decided, and the agent's own reason
  case RD-0002              one ticket, and the records the agent's tools would serve
  diff                      the last two runs: what the change fixed and what it broke, and whether that is more than chance
  restore 2                 my-work/agent/ becomes version 2 again
  check [<run>]             do the runs and the log keep to the format (my-work/FORMAT.md)? For experiments of your own making
  grade --test | --all      is your judge any good?  |  judge every run again with it (a run judges its replies by itself)
  new-tests on | off        turn the 8 hidden claims into new tests that everybody can read, run (--set new) and see
  rarely:                   prove-tools (one live ticket, call by call), pull <run> (fetch the sessions' records again),
                            trial-log (pack your live runs for whoever helps you), cleanup (remove what your key created)

Runs are named last, previous, or by their version's number: ./rd failures 1, ./rd diff 1 3.
--json gives machine-readable output.  --set hidden takes the hidden claims, --set new the new tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys

from .common import (DEFAULT_MODEL, load_agent, ROOT, RUNS, agent_versions, default_concurrency, default_effort, finished_results, ids_in, list_runs, load_env,
                     load_labels, no_answers, read_events, read_json, run_dir, still_valid)


OURS_ONLY = False       # --ours on the command line: every run named there is looked up among our saved runs


def grade_command(argv) -> int:
    """./rd grade [run] [--again]   ./rd grade --all   ./rd grade --test"""
    if set(argv) & {"--help", "-h"}:
        print(grade_command.__doc__ + "\n--test: is your judge any good?   --test --unseen: the same on replies nobody has read   --all: judge every run again")
        return 0
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return through_the_dashboard(["grade"] + argv)
    from . import claim
    flags, names = [a for a in argv if a.startswith("--")], [a for a in argv if not a.startswith("--")]
    if set(flags) - {"--test", "--again", "--all", "--unseen"} or len(names) > 1:
        raise SystemExit("./rd grade --test tests your judge (--test --unseen: on replies nobody has read). ./rd grade --all judges every run again with it, so that they compare; ./rd grade [run] one run. --again: even what it has judged.")
    say = lambda words: print(words, flush=True)
    if "--test" in flags:
        claim.test(say, unseen="--unseen" in flags)
    elif "--all" in flags:
        for name in list_runs():
            try:
                claim.grade_run(name, say, again="--again" in flags)
            except SystemExit as error:          # a run without replies; anything worse is said and stops us
                if "holds no replies" not in str(error):
                    raise
    else:
        claim.grade_run(find_run(names[0] if names else "last"), say, again="--again" in flags)
    return 0


def own_effort(version):
    """The effort a version's agent.json names, else the model's own default: the run that "1" stands for is the one made at it."""
    try:
        return load_agent(version)["config"].get("effort") or default_effort(DEFAULT_MODEL)
    except (SystemExit, KeyError, TypeError, ValueError):
        return default_effort(DEFAULT_MODEL)


def find_run(name: str, backend=None) -> str:
    """The run that a name stands for. A bare name means one of YOUR runs, "ours:..." (or --ours) one of our saved runs.
    A version's name stands for that version's run with the most tickets of the main set decided; then the default model,
    the first take, the newest."""
    ours = OURS_ONLY or name.startswith("ours:")
    bare = name[5:] if name.startswith("ours:") else name
    known = [r for r in list_runs(ours=True) if r.startswith("ours:") == ours]
    if ours and bare.isdigit():              # ours:1 is ours:v1
        bare = "v" + bare
    if not ours and bare in ("last", "previous"):
        started = lambda r: [read_json(run_dir(r) / "run.json", {}).get(k) or blank for k, blank in (("started", ""), ("started_ts", 0))]
        newest = sorted(known, key=started, reverse=True)
        if len(newest) <= (bare == "previous"):
            raise SystemExit("You have no run yet. Make one: ./rd run." if not newest else
                             "You have one run so far, so there is nothing to compare it with. Change my-work/agent/, then: ./rd run.")
        return newest[bare == "previous"]
    if ("ours:" + bare if ours else bare) in known:
        return "ours:" + bare if ours else bare
    take = 1
    if re.fullmatch(r".+-take\d+", bare) and bare.rsplit("-take", 1)[0] in agent_versions():      # 2-take3: the third take of version 2
        bare, take = bare.rsplit("-take", 1)[0], int(bare.rsplit("-take", 1)[1])
    main = set(ids_in(("main",)))
    mine = [(len(main & set(finished_results(read_events(r)))), s.get("model") == DEFAULT_MODEL and s.get("effort") == own_effort(s.get("agent")),
             s.get("take", 1) == take, s.get("started", ""), r)
            for r in known for s in [read_json(run_dir(r) / "run.json", {})]
            if s.get("agent") == bare and backend in (None, s.get("backend")) and (take == 1 or s.get("take", 1) == take)]
    if mine:
        return max(mine)[-1]
    kind = backend.replace("-", " ") + " " if backend else ""
    if ours and not known:
        raise SystemExit("This kit holds no saved runs of ours: every run here is your own. ./rd score lists them.")
    if ours and bare in ("last", "previous"):
        raise SystemExit("Among our saved runs, name the runs: ./rd diff v1 v4 --ours. ./rd score --ours lists them.")
    if ours:
        raise SystemExit("There is no %srun %r among our saved runs. ./rd score --ours lists them." % (kind, bare))
    if bare.isdigit():
        raise SystemExit("You have no %srun of version %s yet. ./rd run %s makes one." % (kind, bare, bare) if bare in agent_versions() else
                         "There is no version %s. my-work/versions/LOG.md lists your versions." % bare)
    try:
        find_run("ours:" + bare, backend)
    except SystemExit:
        raise SystemExit("You have no %srun named %r, nor of a version of that name. ./rd score lists your runs." % (kind, bare))
    raise SystemExit("You have no %srun of %s. Our saved run of it is named ours:%s." % (kind, bare, bare))


def pick_cases(only: str):
    """--only accepts: case ids (RD-0001,RD-0002), a number (first N), starter (the small set for a full room), or failures:<run>."""
    from .common import case_ids, ids_in, starter_ids
    from .score import failures, load_run
    if not only:
        return None
    if only == "starter":
        return starter_ids()
    if only == "hidden16":           # the room's fixed share of the hidden set (never fewer: a single hidden ticket would give its answer away)
        labels = load_labels()
        return [c for c in ids_in(("hidden",)) if labels[c].get("hidden16")] or ids_in(("hidden",))
    if only.isdigit():
        return ids_in(("main",))[:int(only)]
    if only.startswith("failures:"):
        return [row[1] for row in failures(load_run(find_run(only.split(":", 1)[1]))["results"], load_labels())]
    unknown = [c for c in only.split(",") if c not in case_ids() or c in ids_in(("hidden",))]
    if unknown:      # a hidden ticket run alone would give its answer away: "1 of 1" or "0 of 1"
        raise SystemExit("No such ticket (hidden tickets run only as a whole set, --set hidden): " + ", ".join(unknown))
    return only.split(",")


def through_the_dashboard(argv: list) -> int:
    """No key in this terminal. If the dashboard is running and has one (typed into its first page), it runs the command for us
    and we print what it says. The key never leaves the dashboard's process."""
    import urllib.error
    import urllib.request
    door = read_json(ROOT / ".desk-server.json")
    no_key = "The dashboard has no API key yet. Type it into the dashboard's first page, then run this again."
    not_running = "The dashboard is not running. Start it with ./start.sh, type your API key into its first page, then run this again."
    if not door:
        raise SystemExit(not_running)
    address = "http://127.0.0.1:%d" % door["port"]
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))       # this laptop only: never through a proxy, whatever the environment says
    try:
        if not json.load(direct.open(address + "/api/setup", timeout=5))["key"]["present"]:
            raise SystemExit(no_key)
        request = urllib.request.Request(address + "/api/command", data=json.dumps({"argv": argv}).encode(),
                                         headers={"Content-Type": "application/json", "X-Desk-Token": door["token"]})
        for line in direct.open(request):
            text = line.decode("utf-8", "replace")
            if text.startswith("::exit "):
                return int(text.split()[1])
            print(text, end="", flush=True)
    except KeyboardInterrupt:
        raise SystemExit("Stopped here. The dashboard stops the command it was running for this terminal.")
    except urllib.error.HTTPError as error:
        raise SystemExit("The dashboard refused the command: " + error.read().decode("utf-8", "replace")[:200])
    except OSError:
        raise SystemExit(not_running)
    return 1


def pack_log() -> str:
    """trial-log.zip: all there is to know about your live runs, for whoever helps you. No key is in it."""
    import zipfile
    wanted = [p for r in sorted(RUNS.glob("*managed-agents*")) for p in sorted(r.rglob("*")) if p.is_file()]
    wanted += [p for p in sorted(ROOT.glob(".desk-*")) if p.name != ".desk-server.json"]      # (that one holds the page's token)
    wanted += sorted(ROOT.glob("trial*.txt"))
    with zipfile.ZipFile(ROOT / "trial-log.zip", "w", zipfile.ZIP_DEFLATED) as out:
        for path in wanted:
            out.write(path, path.relative_to(ROOT))
    return ("trial-log.zip: %d files: the Managed Agents runs with their session records, the Setup log and the ids of what was created. "
            "No key is in it." % len(wanted))


def the_set(sets: str) -> str:
    """"new" needs the switch; once it is on, "hidden" means "new"."""
    from .common import new_tests_on
    asked = sets.split(",")
    if "new" in asked and not new_tests_on():
        raise SystemExit("The new tests are off. Turn them on first: ./rd new-tests on")
    return ",".join("new" if s == "hidden" and new_tests_on() else s for s in asked)


def show(value) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def one_line(name: str, labels: dict) -> dict:
    """What the table of ./rd score says about one run."""
    from .score import extra_cost, load_run, run_timing, summarize
    run = load_run(name)
    s = summarize(run["results"], labels, extra_cost(run["events"]), no_answers(run["events"]))
    keep = ("accuracy", "plus_minus", "strict_accuracy", "n", "no_answer", "cost_usd", "cost_per_case", "cost_per_success",
            "cost_of_mistakes", "desk_cost_per_case", "overpaid", "refused", "caught", "injections", "sets", "hidden_so_far")
    changed = [labels.get(c, {}).get("set") for c in set(run["results"]) - set(still_valid(run["results"]))]
    for which, row in s["sets"].items():
        row["stale"] = changed.count(which)       # results decided on a ticket, order or photo that has changed since
    stale = changed.count("main")
    return dict({"run": name}, **{k: run["settings"].get(k) for k in ("agent", "title", "note", "started", "platform", "model", "effort", "backend", "take", "fingerprint")},
                **{k: s.get(k) for k in keep}, stale=stale, timing=run_timing(run))


class Parser(argparse.ArgumentParser):
    def error(self, message):      # one sentence and exit code 1, like every other refusal here
        if "invalid choice" in message and "argument :" in message:
            message = "there is no command %s" % message.split("invalid choice: ")[1].split(" (")[0]
        raise SystemExit("%s: %s. See ./rd --help." % (self.prog, message))


def main():
    if hasattr(signal, "SIGPIPE") and sys.argv[1:2] and sys.argv[1] in ("score", "failures", "case", "diff", "judge"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)       # "./rd score | head" ends quietly. Only for commands that merely print: the dashboard
                                                            # must outlive a browser that hangs up in mid-answer, and a run its terminal
    for i, word in enumerate(sys.argv):      # a note is one line, whatever was typed: line breaks and tabs become spaces
        if sys.argv[i - 1] == "--note" or word.startswith("--note="):
            sys.argv[i] = " ".join(word.split())[:300]
    load_env()
    if "DESK_MODE" not in os.environ:      # a terminal follows the running dashboard: room or presenter
        os.environ["DESK_MODE"] = (read_json(ROOT / ".desk-server.json") or {}).get("mode", "room")
    if sys.argv[1:2] == ["grade"]:                   # the judge reads a run's replies; --test: is the judge any good?
        return grade_command(sys.argv[2:])
    if sys.argv[1:2] == ["prove-tools"]:             # one ticket, live: the tools as the platform holds them, call by call
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return through_the_dashboard(sys.argv[1:])
        from .tools import prove
        return prove(sys.argv[2:])
    parser = Parser(prog="./rd", description=__doc__, usage="./rd <command> ...", formatter_class=argparse.RawDescriptionHelpFormatter)
    if not sys.argv[1:]:
        parser.print_help()
        return 0
    sub = parser.add_subparsers(dest="command", required=True, metavar="", help=argparse.SUPPRESS, parser_class=Parser, prog="./rd")

    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=8321)
    p.add_argument("--open", action="store_true", help="open the browser")

    sub.add_parser("setup")

    p = sub.add_parser("restore")
    p.add_argument("version")

    p = sub.add_parser("run")
    p.add_argument("version", nargs="?", help="rarely: an earlier version's number, to run that one again as it was")
    p.add_argument("--note", default="", help="one line for the log: what you changed, and why")
    p.add_argument("--model")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--concurrency", type=int, default=None)
    p.add_argument("--only", help="RD-0001,RD-0002 | 20 (the first 20) | starter | failures:<run>")
    p.add_argument("--set", dest="sets", default="main", help="main (default), hidden, or new once ./rd new-tests on was run")
    p.add_argument("--all", action="store_true", help="every ticket of the main set (a run takes them all anyway)")
    p.add_argument("--fresh", action="store_true", help="start over instead of skipping finished cases")
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--repeat", type=int, default=1, help="so many runs, one after the other: each is kept as a run of its own, and ./rd score gives their mean")
    p.add_argument("--take", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("score")
    p.add_argument("run", nargs="?")
    p.add_argument("--ours", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("failures")
    p.add_argument("run", nargs="?", default="last")
    p.add_argument("--ours", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--set", dest="sets", default="main", choices=["main", "new", "harder"], help="new: the new tests, once they are on. Hidden tickets are never listed")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("case")
    p.add_argument("id")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("diff")
    p.add_argument("a", nargs="?", default="previous")
    p.add_argument("b", nargs="?", default="last")
    p.add_argument("--ours", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--set", dest="sets", default="main", choices=["main", "hidden", "new", "harder"], help="of hidden tickets only the counts are shown; new: the new tests, once they are on")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("forge")
    p.add_argument("id")
    p.add_argument("--version", default=None)
    p.add_argument("--decision", default="refund")
    p.add_argument("--amount", default="9999")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("pull")
    p.add_argument("run")

    p = sub.add_parser("export")
    p.add_argument("--out", default="refund-desk.html")
    p.add_argument("--photos", choices=["embed", "small", "link"], default="embed",
                   help="embed the full photos, embed only the thumbnails, or link to the files")
    p.add_argument("--plain", action="store_true", help="do not zip the data inside the file (larger; for browsers from before 2023)")

    p = sub.add_parser("check")
    p.add_argument("run", nargs="?")

    sub.add_parser("cleanup")
    p = sub.add_parser("new-tests")
    p.add_argument("turn", nargs="?", choices=["on", "off"])
    sub.add_parser("trial-log")
    args = parser.parse_args()
    global OURS_ONLY
    OURS_ONLY = bool(getattr(args, "ours", False))
    if args.command in ("run", "cleanup", "setup", "pull") and not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit(through_the_dashboard(sys.argv[1:]))

    if args.command == "serve":
        from .server import serve
        serve(args.port, args.open)

    elif args.command == "setup":
        from . import setup
        told = set()

        def tell():
            for step in setup.status()["steps"]:
                if step["state"] in ("done", "failed") and step["id"] not in told:
                    told.add(step["id"])
                    print("%-6s %s\n         %s" % ("ok" if step["state"] == "done" else "FAILED", step["label"], step["detail"]), flush=True)
        sys.exit(0 if setup.run("all", tell) else 1)

    elif args.command == "restore":
        from .versions import restore
        try:
            done = restore(args.version)
        except ValueError as error:
            raise SystemExit(str(error))
        print("my-work/agent/ is version %s again.%s" % (
            done["restored"], " What was there is kept as version %s." % done["kept_as"] if done["kept_as"] else
            " What was there did not load as an agent; it is kept in %s/." % done["set_aside"] if done["set_aside"] else ""))

    elif args.command == "run":
        from .runner import run
        from .score import print_score
        if not set(args.sets.split(",")) <= {"main", "hidden", "new", "harder"}:
            raise SystemExit("There is no set %r. The sets are main, hidden, new and harder." % args.sets)
        args.sets = the_set(args.sets)
        quiet = (lambda text: None) if args.json else print
        room = os.environ.get("DESK_MODE") != "presenter" and not (args.all or args.only)
        if room and args.sets in ("main", "hidden"):          # (a kit may mark a smaller set for a full room; this one does not)
            args.only = "starter" if args.sets == "main" else "hidden16"
        cases = pick_cases(args.only)          # (before the version is logged: "failures:last" means the run before this one)
        if not args.version:
            from .versions import current
            args.version = current(args.note, quiet)
        rows, any_wrong = [], False
        for go in range(max(1, min(10, args.repeat))):
            name, stopped, wrong = run(args.version, "managed-agents", args.model, args.effort, args.concurrency or default_concurrency(),
                                       cases, args.fresh and go == 0, report=not args.no_report, say=quiet,
                                       take=args.take, sets=tuple(args.sets.split(",")), note=args.note)
            rows.append(one_line(name, load_labels())) if args.json else print_score(name, tuple(args.sets.split(",")))
            any_wrong = any_wrong or wrong
            if stopped:
                break
        if args.json:
            show(rows[0] if len(rows) == 1 else rows)
        sys.stdout.flush()
        if stopped:
            os._exit(1)      # not finished. (_exit: a worker stuck on a dead connection must not keep a stopped run alive)
        sys.exit(1 if any_wrong else 0)

    elif args.command == "score":
        from .score import extra_cost, load_run, print_score, summarize, takes
        labels = load_labels()
        if args.run and args.json:
            run = load_run(find_run(args.run))
            whole = summarize(run["results"], labels, extra_cost(run["events"]), no_answers(run["events"]))
            show(dict({k: v for k, v in whole.items() if k not in ("families", "themes")},      # the key's own grouping: finding the groups is the reader's work
                      stale=len(run["results"]) - len(still_valid(run["results"]))))
        elif args.run:
            print_score(find_run(args.run))
        else:
            rows = [row for row in (one_line(name, labels) for name in list_runs(ours=True) if name.startswith("ours:") == OURS_ONLY)
                    if row["n"] or row["sets"]]
            if not OURS_ONLY:
                rows.sort(key=lambda row: row["started"] or "", reverse=True)       # the log of your runs: newest first
            whole = sum(1 for label in labels.values() if label["set"] == "main")
            repeats = {}
            for row in rows:      # only runs of as many tickets on the very same agent, cases and photos are takes of one experiment
                if row["n"] and not row["stale"]:
                    key = tuple(str(row[k]) for k in ("agent", "model", "effort", "backend", "fingerprint")) + (row["n"] + row["no_answer"],)
                    repeats.setdefault(key, []).append(row["accuracy"])
            means = [dict(takes(scores), settings=("ours:" if OURS_ONLY else "") + "-".join(key[:4])) for key, scores in repeats.items() if len(scores) > 1]
            if args.json:
                return show({"runs": rows, "means_of_takes": means})
            if not rows:
                return print("No runs yet. ./rd run makes the first.")
            print("%-50s %8s %5s %5s %10s %12s %10s" % ("run", "right", "+-", "n", "$ per case", "$ per success", "mistakes $"))
            for row in rows:
                others = "".join("   %s %.1f%% of %d%s" % (k, v["accuracy"], v["n"], " (%d changed since)" % v["stale"] if v["stale"] else "")
                                 for k, v in sorted(row["sets"].items()) if k != "main")
                part = "   (%d of the %d tickets)" % (row["n"], whole) if row["n"] + row["no_answer"] < whole else ""
                part += "   %d tickets have changed since: run it again" % row["stale"] if row["stale"] else ""
                if not row["n"]:
                    print("%-50s %s" % (row["run"], "(no ticket of the main set yet)" + others))
                    continue
                print("%-50s %7.1f%% %5.0f %5d %10.4f %12.4f %10.0f%s%s" % (
                    row["run"], row["accuracy"], row["plus_minus"], row["n"], row["cost_per_case"], row["cost_per_success"] or 0,
                    row["cost_of_mistakes"], others, part + ("   " + row["note"][:80] if row.get("note") and not OURS_ONLY else "")))
            for t in means:
                print("%-50s %7.1f%% mean of %d takes, from %.1f to %.1f" % (t["settings"], t["mean"], t["n"], t["low"], t["high"]))

    elif args.command == "new-tests":
        from .common import new_tests_on, turn_new_tests
        if args.turn:
            n = turn_new_tests(args.turn == "on")
        if not new_tests_on():
            print("The new tests are off. Turn them on: ./rd new-tests on")
        else:
            ids = ids_in(("new",))
            print("%d new tests are on: %s" % (len(ids), ", ".join(ids)))
            print("They lie in shop/new-tests/. The dashboard shows them: 24 claims | %d new tests, at the top." % len(ids))
            print("Run them:    ./rd run --set new --note \"new tests\"      (your agent as it is)")
            print("             ./rd run 1 --set new                       (the first draft, to compare)")
            print("Read them:   ./rd failures --set new      ./rd case %s      ./rd diff 1 last --set new" % ids[0])

    elif args.command == "failures":
        from .score import grade, load_run
        the_set(args.sets)
        labels, run = load_labels(), load_run(find_run(args.run))
        rows = []
        for case_id, result in sorted(run["results"].items()):
            label, answer = labels[case_id], result["answer"]
            if label["set"] == args.sets and not grade(answer, label)["correct"]:      # one set at a time; hidden tickets stay hidden
                g, marks = grade(answer, label), answer.get("reply_grade") or {}
                rows.append({"id": case_id,
                             "failed_at": g["failed_at"],       # which of the three parts: decision, policy, reply
                             "expected": dict({"decision": label["decision"], "amount": label["amount"]}, **({"policy_section": label.get("handbook_section")} if g["whole"] else {})),
                             "decided": {k: answer[k] for k in ("decision", "amount", "reason", "handbook_section", "photo_observation") + (("policy_section",) if g["whole"] else ())},
                             **({"reply": answer["reply"], "reply_failed_checks": dict({k: v["why"] for k, v in (marks.get("checks") or {}).items() if not v.get("pass")},
                                                                                      **({"judge": (marks.get("verdict") or {}).get("why", "")} if not (marks.get("verdict") or {}).get("says_yes", True) else {}))}
                                if "reply" in g["failed_at"] else {})})
        if args.json:
            return show({"run": run["settings"].get("run"), "set": args.sets, "wrong": len(rows), "failures": rows})
        for row in rows:
            if row["failed_at"]:
                print("%s  failed at %s%s" % (row["id"], ", ".join(row["failed_at"]), "   policy %s, expected %s" % (
                    row["decided"].get("policy_section") or "none", row["expected"].get("policy_section")) if "policy" in row["failed_at"] else ""))
            for check, why in (row.get("reply_failed_checks") or {}).items():
                print("         reply, %s: %s" % (check, why[:200]))
            print("%s  expected %-8s %7.2f   decided %-8s %7.2f   %s" % (
                row["id"], row["expected"]["decision"], row["expected"]["amount"],
                row["decided"]["decision"], row["decided"]["amount"], row["decided"]["reason"][:400]))
        groups = {}
        for row in rows:
            groups.setdefault(row["expected"].get("policy_section") or "none", []).append(row["id"])
        if rows:
            print("By the policy that decides the claim: " + "   ".join(
                "%s %d" % (name, len(ids)) for name, ids in sorted(groups.items(), key=lambda item: -len(item[1]))))
        for part in ("decision", "policy", "reply"):
            got = [row["id"] for row in rows if part in row["failed_at"]]
            if got:
                print("Failed at %-8s %d" % (part, len(got)))
        print("%d not handled in the %s set. ./rd case <id> shows a ticket and the records the tools would serve." % (len(rows), args.sets))

    elif args.command == "case":
        from .common import case_ids, load_case
        if args.id not in case_ids() or load_labels().get(args.id, {}).get("set") == "hidden":
            raise SystemExit("No such ticket (hidden tickets cannot be read).")
        case = load_case(args.id)
        show({"id": args.id, "ticket": case["ticket"], "order_lookup": case["order"], "photo": os.path.relpath(str(case["photo"]))})

    elif args.command == "diff":
        from .score import by_chance, flips, one_way, print_flips
        a, b = find_run(args.a), find_run(args.b)
        args.sets = the_set(args.sets)
        both = flips(a, b, args.sets)
        if not (both["fixed"] or both["broken"] or both["both_wrong"] or both["both_right"]):
            raise SystemExit("There is nothing to compare in the %s set: both runs must have decided %s. Run both on it first "
                             "(./rd run --set %s)." % (
                                 args.sets, "the whole of it" if args.sets == "hidden" else "some of the same tickets",
                                 args.sets))
        if args.json:
            f = flips(a, b, args.sets)
            chance = None if one_way(f) else round(by_chance(len(f["fixed"]), len(f["broken"])), 4)      # None: no test of chance applies
            if args.sets == "hidden":      # counts, not ids
                f = {k: len(v) if isinstance(v, list) else v for k, v in f.items()}
            return show(dict({"a": a, "b": b, "set": args.sets}, **f, by_chance=chance))
        print_flips(a, b, args.sets)

    elif args.command == "forge":
        from .playbooks import forge
        if args.id not in ids_in(("main", "new", "harder")):
            raise SystemExit("No such ticket (hidden tickets cannot be read).")
        found = forge(args.id, args.version, {"decision": args.decision, "amount": args.amount})
        show(found) if args.json else print(found["said"])

    elif args.command == "pull":
        from . import cma, records
        if OURS_ONLY or args.run.startswith("ours:"):
            raise SystemExit("Our saved runs were not made on the platform, so there is nothing to fetch for them.")
        from .api import APIError
        name = find_run(args.run, "managed-agents")       # only Managed Agents sessions live on the platform
        known = {p.stem: read_json(p) for p in sorted((RUNS / name / "sessions").glob("sesn_*.json"))}
        rows = [r for r in records.platform(name)["sessions"] if str(r["session_id"]).startswith("sesn_")]
        about = {r["session_id"]: r for r in rows}
        told, gone, failed, saved = [], [], 0, 0
        ids = sorted(set(known) | set(about))
        print("%s: asking the platform about %d sessions" % (name, len(ids)), flush=True)
        for n, sid in enumerate(ids, 1):
            try:
                told.append(cma._patiently(lambda: cma.state_of(sid), tries=3))
                events = cma._patiently(lambda: cma.pull(sid), tries=3)
                record = known.get(sid) or {"session_id": sid, "case": about[sid]["case"], "role": about[sid]["role"], "model": about[sid]["model"], "effort": about[sid]["effort"],
                                            "backend": "managed-agents", "started_ts": about[sid]["started_ts"], "ended_ts": None, "usage": {}, "cost_usd": about[sid]["cost_usd"]}
                records.save(name, records.shape({k: v for k, v in record.items() if k not in ("events", "seconds", "stop_reason")}, events))
                saved += 1
            except APIError as error:
                if getattr(error, "status_code", None) == 404:
                    gone.append(sid)
                elif getattr(error, "status_code", None) in (401, 403):
                    raise SystemExit("The API refused the key (HTTP %d): these sessions belong to another key's workspace, perhaps." % error.status_code)
                else:
                    failed += 1
            if n % 8 == 0 and n < len(ids):
                print("  %d of %d" % (n, len(ids)), flush=True)
        records.save_platform(name, told, gone)
        print("%d session records of %s fetched and saved in my-work/runs/%s/sessions/%s%s" % (saved, name, name,
              ". %d are no longer on the platform" % len(gone) if gone else "", ". %d could not be fetched: run it again" % failed if failed else ""))

    elif args.command == "export":
        from .export import export
        export(args.out, args.photos, args.plain)

    elif args.command == "check":
        from .check import main as check
        from .common import EXPERIMENTS
        known = args.run and ((RUNS / args.run).is_dir() or (EXPERIMENTS / args.run).is_dir())      # (a folder that is not yet a valid run must be checkable too)
        sys.exit(check(args.run if known or not args.run else find_run(args.run)))

    elif args.command == "trial-log":
        print(pack_log())

    elif args.command == "cleanup":
        from . import cma
        sys.exit(1 if cma.cleanup() else 0)


if __name__ == "__main__":
    sys.exit(main())
