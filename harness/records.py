"""Session records: what one session did, event by event, saved beside the run and drawn by the dashboard.

A Managed Agents session keeps its whole history on the platform. cma.pull() fetches it; here it is put into one plain shape,
saved as runs/<run>/sessions/<session id>.json, and read back. The fleet() of a run is every session's start and end."""
from __future__ import annotations

import datetime
import re

from .common import RUNS, ids_in, read_events, read_json, run_dir, write_json


def seconds_of(stamp) -> float:
    """'2026-01-05T10:00:00.123Z' -> seconds since 1970."""
    whole, _, rest = str(stamp).replace("Z", "+00:00").partition(".")
    if rest:                # Python 3.9 reads only 3 or 6 digits after the point: make it 6, whatever came
        digits = re.match(r"\d*", rest).group(0)
        whole += "." + (digits + "000000")[:6] + rest[len(digits):]
    return datetime.datetime.fromisoformat(whole).timestamp()


def without_images(value):
    """The same event with the photo's bytes cut out: they lie beside the ticket already."""
    if isinstance(value, dict):
        if value.get("type") == "base64" and "data" in value:
            return dict(value, data="[image data left out]")
        return {k: without_images(v) for k, v in value.items()}
    if isinstance(value, list):
        return [without_images(v) for v in value]
    return value


def text_of(event: dict) -> str:
    blocks = event.get("content") if isinstance(event.get("content"), list) else []
    return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")[:400]


def line(event: dict, began: float) -> dict:
    """One event, as the timeline wants it: when, what kind, a few words, and the event itself."""
    kind, given = event.get("type", ""), event.get("input") if isinstance(event.get("input"), dict) else {}
    out = {"t": round(seconds_of(event["processed_at"]) - began, 2) if event.get("processed_at") else None, "type": kind, "label": kind}
    if kind in ("agent.tool_use", "agent.custom_tool_use"):
        out["tool"] = event.get("name")
        out["file"] = given.get("file_path") or given.get("path") or given.get("pattern")
        first = next((v for v in given.values() if isinstance(v, (str, int, float))), None)       # lookup_order OS-3812325, submit_decision refund
        out["label"] = " ".join(str(x)[:60] for x in (out["tool"], out["file"] or given.get("decision") or first) if x)
    elif kind == "user.custom_tool_result":
        out["label"] = "error" if event.get("is_error") else "result"
    elif kind == "span.model_request_end":
        usage = event.get("model_usage") or {}
        out["tokens"] = {"in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0),
                         "cache_read": usage.get("cache_read_input_tokens", 0)}
        out["label"] = "model answered"
    elif kind == "session.status_idle":
        out["label"] = "idle: " + str((event.get("stop_reason") or {}).get("type"))
    elif kind == "session.error":
        out["label"] = "error: " + str((event.get("error") or {}).get("message"))[:120]
    if text_of(event):
        out["text"] = text_of(event)
    out["raw"] = without_images(event)
    return out


def shape(about: dict, events: list) -> dict:
    """about: session_id, case, role, model, effort, backend, started_ts, ended_ts, usage, cost_usd. events: as the API returned them."""
    stamps = [seconds_of(e["processed_at"]) for e in events if e.get("processed_at")]
    began = min(stamps) if stamps else about.get("started_ts") or 0.0
    idle = [e for e in events if e.get("type") == "session.status_idle"]
    return dict(about, seconds=round((max(stamps) - began) if stamps else 0.0, 2),
                stop_reason=(idle[-1].get("stop_reason") or {}).get("type") if idle else None,
                events=[line(e, began) for e in events])


def save(run: str, record: dict) -> None:
    folder = RUNS / run / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / (record["session_id"] + ".json"), record)


def save_platform(run: str, sessions: list, gone: list) -> None:
    """What the platform said about a run's sessions when it was last asked: runs/<run>/platform.json."""
    write_json(RUNS / run / "platform.json", {"pulled_ts": round(datetime.datetime.now().timestamp(), 1), "sessions": sessions, "gone": gone})


def platform(run: str) -> dict:
    """The run's sessions as the harness logged them, with what the platform said of each when it was last asked."""
    told = read_json(run_dir(run) / "platform.json", {})
    told = told if isinstance(told, dict) else {}
    by_id = {s.get("session_id"): s for s in told.get("sessions") or [] if isinstance(s, dict)}
    last = {}
    for e in read_events(run):
        if e["type"] == "case" and e.get("status") == "done" and isinstance(e.get("result"), dict):
            last[e["id"]] = e["result"]
    rows = []
    for case_id, result in sorted(last.items()):
        for s in result.get("sessions") or []:
            if s.get("session_id"):
                use = s.get("usage") or {}
                rows.append({"case": case_id, "role": s.get("role"), "session_id": s["session_id"], "model": s.get("model"), "effort": s.get("effort"),
                             "started_ts": s.get("started_ts"), "seconds": s.get("seconds"), "cost_usd": s.get("cost_usd"), "tokens_in": use.get("input_tokens"),
                             "tokens_out": use.get("output_tokens"), "cache_read": use.get("cache_read_input_tokens"), "tool_calls": len(s.get("tool_calls") or []),
                             "decision_event": next((c.get("event_id") for c in s.get("tool_calls") or [] if str(c.get("name")).startswith("submit_")), None),
                             "record": (run_dir(run) / "sessions" / (str(s["session_id"]) + ".json")).exists(),
                             "platform": by_id.get(s["session_id"]), "gone": s["session_id"] in (told.get("gone") or [])})
    settings = read_json(run_dir(run) / "run.json", {})
    return {"run": run, "backend": settings.get("backend"), "agent": settings.get("platform"), "pulled_ts": told.get("pulled_ts"), "sessions": rows}


def load(run: str, session_id: str):
    return read_json(run_dir(run) / "sessions" / (session_id + ".json"))


def fleet(run: str, grades=None, wall=None) -> dict:
    """The picture of the parallel run: every session of the main set over time, with the holds and retries in between.
    A run may have grown in several goes (stopped and resumed, tickets decided again after a photo changed). 
    each ticket counts with the sessions of its last decision, and the pauses between the goes are cut to 3 s. Times are seconds since 1970 with those pauses taken out."""
    events, main, last = read_events(run), set(ids_in(("main",))) if wall is None else set(wall), {}
    for e in events:
        if e["type"] == "case" and e.get("status") == "done" and e["id"] in main:
            last[e["id"]] = e
    sessions = [{"case": e["id"], "role": s["role"], "session_id": s.get("session_id"), "model": s.get("model"),
                 "start": s.get("started_ts"), "end": s.get("ended_ts"), "attempt": e["result"].get("attempts", 1),
                 "decision": (s.get("answer") or {}).get("decision"), "correct": (grades or {}).get(e["id"])}
                for e in last.values() for s in e["result"]["sessions"]]
    timed = sorted((s for s in sessions if s["start"] and s["end"]), key=lambda s: s["start"])
    pauses, busy_until = [], None                      # (when a pause ends, how much of it is cut)
    for s in timed:
        if busy_until is not None and s["start"] - busy_until > 3:
            pauses.append((busy_until, s["start"], s["start"] - busy_until - 3))
        busy_until = max(busy_until or 0, s["end"])
    squeeze = lambda t: round(t - sum(cut if t >= end else max(0.0, min(t - begin - 3, cut)) for begin, end, cut in pauses if t > begin), 3)
    at_work = lambda t: any(s["start"] - 5 <= t <= s["end"] + 5 for s in timed)
    holds = [{"ts": squeeze(e["ts"]), "wait_s": e.get("wait_s"), "what": e.get("what")}
             for e in events if e["type"] == "notice" and e.get("kind") == "hold" and at_work(e["ts"])]
    retries = [{"ts": squeeze(e["ts"]), "case": e["id"], "why": e.get("why")}
               for e in events if e["type"] == "case" and e.get("status") == "retrying" and e["id"] in main and at_work(e["ts"])]
    for s in timed:
        s["start"], s["end"] = squeeze(s["start"]), squeeze(s["end"])
    return {"run": run, "started_ts": timed[0]["start"] if timed else None, "ended_ts": max(s["end"] for s in timed) if timed else None,
            "concurrency": read_json(run_dir(run) / "run.json", {}).get("concurrency"),
            "sessions": sessions, "holds": holds, "retries": retries}
