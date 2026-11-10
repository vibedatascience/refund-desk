"""Playbooks: how many agents look at one case, and how their work is combined.

Every playbook gets `ask(role, content)`, which runs ONE session with the prompt file <role>.md and returns
its result. So a playbook is a few lines of plain Python around one or more sessions.
"""
from __future__ import annotations

from .common import load_agent, load_case
from .task import SessionFailed, as_data, case_content, clean_answer


# Where code changes a decision it also names the handbook section and the team, as an agent would.
# These three lines are all the code knows about the handbook's numbering: keep them in step with it.
PHOTO_CHAPTER = "6."                                                            # photo evidence
ORDER_NOT_FOUND = {"handbook_section": "3.1", "policy_section": "3.1", "team": "", "reply": ""}      # (the agent's reply answered another decision: it is not sent)
OVER_THE_LIMIT = {"handbook_section": "9.1", "policy_section": "9.1", "team": "Supervisor", "reply": ""}


def handed(config) -> tuple:
    """What a deciding agent finds in its first message. A version with "tools" in agent.json is not handed the order record:
    it fetches it (harness/tools.py)."""
    return ("ticket", "photo") if config.get("tools") else ("ticket", "order", "photo")


def single(ask, case, config):
    """One agent decides."""
    first = ask("prompt", case_content(case, handed(config)))
    return first["answer"], [first]


def review(ask, case, config):
    """One agent decides; a second agent looks again at the decisions that are worth a second look.
    agent.json says which: "review_when": ["payout", "flag", "low_confidence", "photo_denial"]."""
    first = ask("prompt", case_content(case, handed(config)))
    answer = first["answer"]
    reasons = {
        "payout": answer["decision"] in ("refund", "replace"),
        "flag": answer["injection_detected"],
        "low_confidence": answer["confidence"] < config.get("review_below_confidence", 0.8),
        "photo_denial": answer["decision"] == "deny" and answer["handbook_section"].startswith(PHOTO_CHAPTER),   # "I see no damage"
    }
    if not any(reasons[why] for why in config.get("review_when", ["payout", "flag", "low_confidence"])):
        return answer, [first]
    note = "<proposed_decision>\n" + as_data(answer) + "\n</proposed_decision>"
    second = ask("reviewer", case_content(case, handed(config), note=note))
    final = dict(second["answer"])
    final["injection_detected"] = final["injection_detected"] or answer["injection_detected"]    # a flag is never lost
    return final, [first, second]


def forge(case_id: str, version, raw: dict) -> dict:
    """What if the model were talked into it? Hand a made-up decision to the same two checks every real one passes:
    clean_answer (is it a decision at all?) and guard (may it leave the store without a person?). No model is called."""
    from .versions import WORK
    config = load_agent(version)["config"] if version else load_agent("agent", WORK)["config"]      # no version named: your agent as it is now
    version = version or "your agent"
    made_up = dict({"reason": "forged", "handbook_section": "", "team": "", "line": 1, "photo_observation": "", "injection_detected": False,
                    "injection_note": "", "confidence": 1.0, "report": ""}, **raw)
    try:
        answer = clean_answer(made_up)
    except SessionFailed as error:
        return {"version": version, "sent": raw, "refused": str(error), "stands": None, "overruled": None,
                "said": "Refused before it was stored: %s." % error}
    stands, why = guard(answer, load_case(case_id), config)
    keep = {k: stands[k] for k in ("decision", "amount", "team", "handbook_section")}
    return {"version": version, "sent": raw, "refused": None, "stands": keep, "overruled": why,
            "said": ("Overruled by code (%s): %s. What stands: %s." % (version, why, keep) if why else
                     "Passed the checks of %s: %s. (Code checks limits and records, not judgement.)" % (version, keep))}


PLAYBOOKS = {"single": single, "review": review}
TEXT_ROLES = ("reporter",)   # writes the end-of-run report in plain text; the others call submit_decision


def guard(answer: dict, case: dict, config: dict):
    """Checks written in code, after the model. A custom tool runs in OUR application, so we validate its input.
    Returns the answer that stands, and why the code overruled the model (or None)."""
    limit = config.get("guard_refund_limit")
    if limit is None or answer["decision"] not in ("refund", "replace"):
        return answer, None
    order = (case["order"] or {}).get("order")
    if order is None:          # nothing leaves the store on an order that cannot be found (handbook 3.1)
        return (dict(answer, decision="deny", amount=0.0, **ORDER_NOT_FOUND),
                answer["decision"] + " without a matching order")
    paid, why = (order.get("totals") or {}).get("total"), None
    if answer["amount"] > limit:
        why = "refund of $%.2f is over the $%.2f automatic limit" % (answer["amount"], limit)
    elif paid is not None and answer["amount"] > paid + 0.005:
        why = "refund of $%.2f is more than the $%.2f paid" % (answer["amount"], paid)
    if why is None:
        return answer, None
    return dict(answer, decision="escalate", amount=0.0, **OVER_THE_LIMIT), why     # a person signs off
