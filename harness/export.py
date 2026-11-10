"""Write the reading views (cases, labels, runs, compare) as ONE html file that needs no server and no network."""
from __future__ import annotations

import base64
import gzip
import json

import re

from . import claim, records, server, versions
from .common import HARNESS, ROOT, case_dir, case_ids, dataset_fingerprint, list_runs, load_labels, ticket_print


def export(path="refund-desk.html", photos="embed", plain=False):
    """photos: "embed" puts the full photos into the file, "small" the thumbnails only,
    "link" points to the photo files by address (small file; whoever serves it must serve them too)."""
    server.KEY.update(load_labels())
    server.DATASET["fingerprint"] = dataset_fingerprint()
    server.PRINTS.update({c: ticket_print(c) for c in case_ids()})
    runs, filesets, current = [], {}, {a["fingerprint"] for a in server.agents()}
    for name in list_runs(ours=True):          # read them all first: a run's summary also speaks of its sister takes
        server.views[name] = server.RunView(name)
        server.views[name].read_new_lines()
    for view in server.views.values():
        if view.results:
            run = view.whole()
            fingerprint = run["settings"].get("fingerprint")
            if fingerprint in current:           # the page already carries these very files once
                run["files"] = None
            elif run["files"]:                   # several runs of one agent version share one copy
                filesets[fingerprint] = run["files"]
                run["files"] = fingerprint
            for case in run["cases"]:                              # the page shows only this much of each session
                for session in (case.get("result") or {}).get("sessions", []):
                    if session.get("answer"):
                        session["answer"] = {k: session["answer"].get(k) for k in ("decision", "amount")}
                        session["text"] = ""
            grades = {c["id"]: c["grade"]["correct"] for c in run["cases"] if c.get("grade")}
            fleet = records.fleet(view.name, grades)          # the picture of the parallel run; of hidden tickets only when they ran
            fleet["sessions"] = [s if server.shown(s["case"]) else dict(s, case="hidden", decision=None, correct=None, session_id=None)
                                 for s in fleet["sessions"]]
            fleet["retries"] = [r if server.shown(r["case"]) else dict(r, case="hidden", why="") for r in fleet["retries"]]
            run["fleet"] = fleet
            runs.append(run)

    def picture(case_id):
        full, thumb = case_dir(case_id) / "photo.jpg", case_dir(case_id) / "thumb.jpg"
        thumb = thumb if thumb.exists() else full
        if photos == "link":
            return {"photo": "cases/%s/%s" % (case_id, full.name), "thumb": "cases/%s/%s" % (case_id, thumb.name)}
        return "data:image/jpeg;base64," + base64.b64encode((thumb if photos == "small" else full).read_bytes()).decode()

    data = {
        "dataset": {"cases": server.dataset(), "agents": server.agents(), "live": False, "models": [], "concurrency": 0,
                    **server.course()},
        "runs": runs,
        "filesets": filesets,
        "sessions": session_records(runs),
        "photos": {c: picture(c) for c in case_ids() if (case_dir(c) / "photo.jpg").exists() and server.KEY[c]["set"] != "hidden"},
    }
    data["versions"], data["my_judge"] = versions.view(), claim.view()
    photos_kept = data.pop("photos")
    data = dict(nameless(data), photos=photos_kept)
    # Cases and runs are text and the bulk of the file, so they travel zipped, at about a seventh of their size.
    # The page's own script is parked as plain text until the browser has unpacked them; then it is started.
    photos = data.pop("photos")
    photos_json = json.dumps(photos).replace("<", "\\u003c")
    packed = base64.b64encode(gzip.compress(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8"))).decode()
    page = (HARNESS / "dashboard.html").read_text(encoding="utf-8")
    head, mark, rest = page.partition("<script>/*STATIC*/</script>\n<script>")
    script, end, tail = rest.rpartition("</script>")
    if not mark or not end:
        raise SystemExit("dashboard.html no longer has the place where the export puts its data.")
    if plain:      # unzipped: every "<" is written as \\u003c, so that no customer's or agent's text can open or close a tag in the page
        blob = json.dumps(dict(data, photos=photos), ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
        page = (head + "<script>window.STATIC = " + blob + ";\n"
                "STATIC.runs.forEach(r => { if (typeof r.files === 'string') r.files = STATIC.filesets[r.files]; });</script>\n<script>"
                + script + end + tail)
    else:
        page = head + '<script type="text/plain" id="page-script">' + script + end + UNPACK % (packed, photos_json) + tail
    (ROOT / path).write_text(page, encoding="utf-8")
    print("Wrote %s (%.1f MB): %d cases, %d with a photo, %d runs" % (
        path, len(page.encode()) / 1e6, len(data["dataset"]["cases"]), len(photos), len(runs)))


def session_records(runs: list) -> dict:
    """Event by event, what the sessions did on the shown claims (without the raw events)."""
    out = {}
    for run in runs:
        for case in run["cases"]:
            if server.KEY.get(case["id"], {}).get("set") != "main":
                continue
            for session in (case.get("result") or {}).get("sessions", []):
                record = records.load(run["name"], session.get("session_id") or "-")
                if record:
                    out[record["session_id"]] = dict(record, events=[{k: v for k, v in e.items() if k != "raw"} for e in record["events"]])
    return out


def nameless(data):
    """The same data without any id that points into somebody's workspace: sessions, agents, events, skills, requests. Each gets a short name of its own,
    the same one everywhere, so that a session still finds its record."""
    seen, text = {}, json.dumps(data, ensure_ascii=False, allow_nan=False)
    text = re.sub(r"\b(sesn|agent|env|envs|sevt|sthr|skill|skillver|req|toolu|wrkspc|org|memver|file|vlt|cred)_[A-Za-z0-9]{8,}", lambda m: seen.setdefault(m[0], "x%d" % (len(seen) + 1)), text)
    out = json.loads(text)
    for run in out["runs"]:
        for key in ("made_with", "platform"):
            run.get("settings", {}).pop(key, None)
    return out



UNPACK = """
<script>
(async () => {
  try {
    const bytes = Uint8Array.from(atob("%s"), c => c.charCodeAt(0));
    const text = await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"))).text();
    window.STATIC = JSON.parse(text);
  } catch (error) {
    document.body.innerHTML = "<p style='font:16px sans-serif;padding:40px'>This page could not unpack its data (" + String(error).replace(/</g, "&lt;") +
      "). It needs a browser from 2023 or later. './rd export --plain' writes a larger file that needs no unpacking.</p>";
    return;
  }
  STATIC.photos = %s;
  STATIC.runs.forEach(r => { if (typeof r.files === "string") r.files = STATIC.filesets[r.files]; });
  const script = document.createElement("script");
  script.textContent = document.getElementById("page-script").textContent;
  document.body.appendChild(script);
})();
</script>
"""
