"""The shop's systems.

A real refund desk has no "order.json". It has an order system, a payment gateway, a carrier's tracking feed, a warehouse, a customer
system and a helpdesk, each with its own records and its own log. This file deals every ticket's record out over those systems:

  order_view(record)      what lookup_order returns:    order, payment log, parcels with the carrier's scan log, stock, audit log
  customer_view(record)   what lookup_customer returns: profile, membership, flags, claims history, staff notes, account log
  Queue(cases)            what search_tickets searches: the open tickets of one queue
  Shop, Counter           the tables of a whole run, and the counter that one session asks at: three look-ups, a guard, a log

This file needs nothing but Python's standard library, because it runs in two places: here, and (as a copy inside the skill
"refund-desk-systems") in the sandbox of a Managed Agents session, where the agent calls the look-ups as commands.

Two rules keep the answer key true:
  1. Every fact of the record keeps its value and its field name (the handbook's Appendix B names them). One exception: a parcel's
     events become the lines of its scan_log. rebuild() proves it at the start of every run: the two views put together give back
     the record exactly.
  2. The log lines are written from facts that are already in the record, plus harmless noise (times of day, devices, ids).
     A log line may repeat a fact. It never adds one that a handbook rule reads.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import re
import secrets
import sys


def dice(*seed) -> int:
    """A number that looks random and is the same on every machine and every run."""
    return int(hashlib.sha256("|".join(str(s) for s in seed).encode()).hexdigest()[:12], 16)


def at(day: str, *seed, shift_days: int = 0) -> str:
    """A date of the record with a made-up time of day: '2025-12-29' -> '2025-12-29T14:02:11'."""
    date = datetime.date.fromisoformat(day) + datetime.timedelta(days=shift_days)
    n = dice(day, *seed)
    return "%sT%02d:%02d:%02d" % (date.isoformat(), 7 + n % 15, n // 15 % 60, n // 900 % 60)


def later(stamp: str, seconds: int) -> str:
    """A moment some seconds after (or, with a minus, before) another one."""
    return (datetime.datetime.fromisoformat(stamp.replace(" ", "T")) + datetime.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def placed_at(order: dict) -> str:
    """When the order was placed: a made-up time on the day the record gives, before anything was packed."""
    made_up = at(order["placed_on"], order["order_id"], "placed")
    packed = [packed_at(p) for p in order["shipments"] if p.get("shipped_on")]
    return max(order["placed_on"] + "T00:04:00", min([made_up] + [later(t, -1800) for t in packed]))


def packed_at(parcel: dict) -> str:
    """The warehouse packs a parcel a little before the carrier's first scan."""
    first = parcel["events"][0]["time"] if parcel.get("events") else at(parcel["shipped_on"], parcel["shipment_id"])
    return later(first, -600 - dice(parcel["shipment_id"]) % 3000)


# ── lookup_order ──────────────────────────────────────────────────────────
def tidy(order_id) -> str:
    """Customers type order numbers in many ways ("os-4771340", "#4771340", "4771340"). The order system tidies prefix, case and spaces."""
    text = re.sub(r"[^A-Z0-9-]", "", str(order_id or "").upper().replace("\u2013", "-"))[:20]
    text = re.sub(r"^(ORDER)?(OS-?)?", "", text)
    return "OS-" + text if text else ""


def scan_line(parcel: dict, event: dict) -> str:
    """One scan of the carrier, as its tracking feed writes it."""
    return '%s %s %s status=%s loc="%s"' % (event["time"], parcel["carrier"].split()[0].lower(), parcel["tracking_no"],
                                            event["status"], event["location"])


def scan_event(line: str) -> dict:
    """The same scan, read back."""
    time, status, location = re.match(r'(\S+ \S+) \S+ \S+ status=(\S+) loc="(.*)"$', line).groups()
    return {"time": time, "status": status, "location": location}


def payment_log(record: dict) -> list:
    """The payment gateway's lines for this order: authorized when placed, captured when first shipped, and one REFUND line
    for each claim on this order that the claims history shows as refunded."""
    order, pay = record["order"], record["order"]["payment"]
    txn = "TX-%08d" % (dice(order["order_id"], "txn") % 10 ** 8)
    card = "method=%s last4=%s" % (pay["method"], pay["last4"] or "-")
    lines = ["%s pay-gw AUTH ok txn=%s amount=%.2f %s" % (later(placed_at(order), 3), txn, order["totals"]["total"], card)]
    packed = sorted(packed_at(p) for p in order["shipments"] if p.get("shipped_on"))
    if packed:
        lines.append("%s pay-gw CAPTURE ok txn=%s amount=%.2f" % (later(packed[0], 2), txn, order["totals"]["total"]))
    for claim in record["customer"]["claims_history"]:
        if claim["order_id"] == order["order_id"] and claim["outcome"] == "refunded":
            lines.append("%s pay-gw REFUND ok txn=%s amount=%.2f sku=%s to=original_method" % (
                at(claim["date"], txn, claim["sku"]), txn, claim["amount"], claim["sku"]))
    return sorted(lines)


def order_log(record: dict) -> list:
    """The order system's audit log: who or what touched the order, and when."""
    order = record["order"]
    oid, placed = order["order_id"], placed_at(order)
    lines = ["%s orders-svc order.created id=%s by=customer:%s lines=%d" % (placed, oid, record["customer"]["customer_id"], len(order["items"]))]
    if order["totals"].get("promo_code"):
        lines.append("%s orders-svc promo.applied id=%s code=%s" % (later(placed, 1), oid, order["totals"]["promo_code"]))
    if order.get("gift"):
        lines.append("%s orders-svc gift.options_set id=%s by=customer:%s" % (later(placed, 1), oid, record["customer"]["customer_id"]))
    for parcel in order["shipments"]:
        carried = ",".join(str(n) for n in parcel["lines"])
        if parcel.get("shipped_on"):
            lines.append("%s warehouse shipment.created id=%s lines=%s carrier=%s" % (
                packed_at(parcel), parcel["shipment_id"], carried, parcel["carrier"].split()[0].lower()))
        if parcel.get("delivered_on"):
            scan = [e["time"] for e in parcel["events"] if e["status"] == "delivered"][-1]
            lines.append("%s carrier-webhook shipment.delivered id=%s delivered_on=%s" % (
                later(scan, 45 + dice(parcel["shipment_id"], "hook") % 200), parcel["shipment_id"], parcel["delivered_on"]))
    lines = sorted(lines)
    return lines + ["%s orders-svc order.status id=%s status=%s" % (later(lines[-1][:19], 1), oid, order["status"])]


def order_view(record: dict) -> dict:
    """What lookup_order returns for an order that exists."""
    order = copy.deepcopy(record["order"])
    order["customer_id"] = record["customer"]["customer_id"]         # the key to the customer system
    order["payment"]["gateway_log"] = payment_log(record)
    for parcel in order["shipments"]:
        parcel["scan_log"] = [scan_line(parcel, event) for event in parcel.pop("events")]
    order["audit_log"] = order_log(record)
    return {"lookup": dict(record["lookup"]), "order": order, "inventory": copy.deepcopy(record["inventory"])}


# ── lookup_customer ───────────────────────────────────────────────────────
PREFERENCES = ("sms_opt_in", "newsletter", "paperless_invoices", "gift_wrap_default", "survey_panel")


def account_log(record: dict, today: str) -> list:
    """The customer system's log for the account: created, verified, membership, preferences, holds, sign-ins.
    today: the date of the ticket. Nothing in this log is later than that."""
    who, order = record["customer"], record["order"]
    cid, flags = who["customer_id"], who["account_flags"]
    device = ("iphone", "android", "mac-safari", "win-chrome", "ipad")[dice(cid, "device") % 5]
    net = "198.51.100.%d" % (dice(cid, "net") % 250 + 1)              # an address range reserved for examples
    signed_in = max(later(placed_at(order), -120 - dice(cid, "login") % 900), order["placed_on"] + "T00:02:00")     # to place this order
    created = max(min(at(who["customer_since"], cid), later(signed_in, -90)), who["customer_since"] + "T00:00:30")
    lines = ["%s accounts account.created id=%s" % (created, cid)]
    if "email_verified" in flags:
        lines.append("%s accounts email.verified id=%s" % (later(lines[0][:19], 60 + dice(cid, "verify") % 600), cid))
    if who.get("folio_since"):
        lines.append("%s membership folio.joined id=%s" % (max(at(who["folio_since"], cid, "folio"), later(created, 1700)), cid))
    if who.get("folio_lapsed_on"):
        lines.append("%s membership folio.lapsed id=%s tier_now=standard" % (at(who["folio_lapsed_on"], cid, "lapse"), cid))
    for flag in flags:
        if flag in PREFERENCES:
            lines.append("%s accounts preference.set id=%s %s=on by=customer" % (later(lines[0][:19], 700 + dice(cid, flag) % 900), cid, flag))
    if "address_changed_2025" in flags:
        lines.append("%s accounts address_book.updated id=%s by=customer" % (
            at(max("2025-01-02", who["customer_since"]), cid, "moved", shift_days=dice(cid, "moved") % 200), cid))
    if "claims_review_hold" in flags:
        lines.append("%s claims-review flag.set id=%s flag=claims_review_hold by=staff" % (at(today, cid, "hold", shift_days=-2 - dice(cid, "hold") % 20), cid))
    if "chargeback_open" in flags:
        lines.append("%s payments flag.set id=%s flag=chargeback_open by=system reason=bank_notice" % (at(today, cid, "cb", shift_days=-2 - dice(cid, "cb") % 20), cid))
    lines.append("%s auth login.ok id=%s device=%s ip=%s" % (signed_in, cid, device, net))
    for n in range(dice(cid, "visits") % 3):                          # a few earlier visits
        lines.append("%s auth login.ok id=%s device=%s ip=%s" % (later(signed_in, -86400 * (9 + dice(cid, "visit", n) % 80) - dice(cid, n) % 30000), cid, device, net))
    lines = [line for line in lines if line[:19] >= lines[0][:19]]    # nothing happens on an account before it exists
    return sorted(line for line in lines if line[:10] <= today)


def customer_view(record: dict, today: str) -> dict:
    """What lookup_customer returns for an account that exists."""
    return {"lookup": {"queried_customer_id": record["customer"]["customer_id"], "status": "found"},
            "customer": dict(copy.deepcopy(record["customer"]), account_log=account_log(record, today))}


def rebuild(order_answer: dict, customer_answer: dict) -> dict:
    """The record, put together again from the two answers. The test of rule 1: it must equal the ticket's order.json."""
    order = copy.deepcopy(order_answer["order"])
    del order["customer_id"], order["audit_log"], order["payment"]["gateway_log"]
    for parcel in order["shipments"]:
        parcel["events"] = [scan_event(line) for line in parcel.pop("scan_log")]
    customer = copy.deepcopy(customer_answer["customer"])
    del customer["account_log"]
    return {"lookup": order_answer["lookup"], "order": order, "customer": customer, "inventory": order_answer["inventory"]}


# ── search_tickets ────────────────────────────────────────────────────────
SAME_WORD = {"north": "n", "south": "s", "east": "e", "west": "w", "street": "st", "avenue": "ave", "road": "rd", "drive": "dr", "lane": "ln",
             "court": "ct", "circle": "cir", "trail": "trl", "place": "pl", "boulevard": "blvd", "parkway": "pkwy", "terrace": "ter",
             "apartment": "unit", "apt": "unit", "suite": "unit", "ste": "unit", "#": "unit"}


def door(ship_to: dict):
    """(building, unit) of a ship-to address, for comparing. People write one address in many ways, so spelling is evened out:
    '5525 North 20th St #4A' and '5525 N. 20th St., Unit 4-A' in 48640 both give ('5525 n 20th st 48640', '4a').
    A sketch that covers the common spellings. A real shop would use its address service here."""
    text = str(ship_to.get("street") or "").lower().replace("#", " # ")
    words = [SAME_WORD.get(word, word) for word in re.sub(r"[.,]", " ", text).split()]
    cut = words.index("unit") if "unit" in words else len(words)
    return " ".join(words[:cut] + [str(ship_to.get("zip") or "")[:5]]), "".join(word for word in words[cut + 1:] if word != "unit").replace("-", "")


class Queue:
    """The open tickets of one queue, with the few things the helpdesk's search needs of each. Accounts, addresses and order numbers
    are kept masked: the search only ever asks whether two of them are the same."""

    def __init__(self, cases: list = (), secret=None, rows=None):
        self.secret = secret or secrets.token_hex(8)       # made anew for every run, unless one is given
        self.rows = rows if rows is not None else {}
        for case in cases:
            ticket, record = case["ticket"], case["order"] or {}
            order, photo = record.get("order") or {}, next((a for a in ticket.get("attachments") or [] if isinstance(a, dict)), {})
            building, unit = door(order["ship_to"]) if order.get("ship_to") else (None, None)
            self.rows[case["id"]] = {
                "ticket_id": case["id"], "received_at": ticket.get("received_at"), "order_id": self.masked(order.get("order_id")),
                "account": self.masked((record.get("customer") or {}).get("customer_id"), "acct-"),       # none if the order was not found
                "door": [self.masked(building), self.masked("unit " + unit)] if building else None,
                "on_it": bool(order) and plain(ticket.get("from_email")) in people_on(record),            # is the sender on the order?
                "sha256": photo.get("sha256"), "phash": int(photo["phash"], 16) if photo.get("phash") else None,
                "subject": ticket.get("subject"), "first_lines": str(ticket.get("message") or "")[:240]}

    def masked(self, value, prefix=""):
        """A token that tells two accounts (addresses, orders) apart, and says nothing else about them."""
        return prefix + hashlib.sha256((self.secret + str(value)).encode()).hexdigest()[:6 if prefix else 12] if value else None

    def search(self, ticket_id: str, by: str, photo_distance: int = 8, stranger: bool = False) -> dict:
        """Other tickets that may be linked to this one: a ticket number, a time and a masked account each. A ticket that quotes the
        same order also shows its subject and first lines. stranger: search as if this ticket's order did not exist."""
        mine, matches = self.rows[ticket_id], []
        if stranger:
            mine = dict(mine, account=None, door=None, order_id=None)
        for row in self.rows.values():
            if row["ticket_id"] == ticket_id:
                continue
            how, more = None, {}
            if by == "photo" and mine["phash"] is not None and row["phash"] is not None:
                apart = bin(mine["phash"] ^ row["phash"]).count("1")
                how = ("identical file" if mine["sha256"] == row["sha256"] else
                       "same picture, cropped or re-saved (fingerprints differ in %d of 64 bits)" % apart if apart <= photo_distance else None)
            elif by == "address" and mine["door"] and row["door"] and mine["door"][0] == row["door"][0]:
                how = "same street address and unit" if mine["door"][1] == row["door"][1] else "same street address, another unit"
            elif by == "order" and mine["order_id"] and mine["order_id"] == row["order_id"]:
                how, more = "same order", {"subject": row["subject"], "first_lines": row["first_lines"]}     # customer content, like any message
            if how:
                matches.append(dict({"ticket_id": row["ticket_id"], "received_at": row["received_at"], "match": how, "account": row["account"],
                                     "other_account": bool(row["account"]) and row["account"] != mine["account"]}, **more))
        return {"search": {"by": by, "ticket_id": ticket_id, "this_account": mine["account"], "open_tickets_searched": len(self.rows) - 1},
                "matches": sorted(matches, key=lambda m: m["received_at"] or "")}


# ── who may see what ──────────────────────────────────────────────────────
def plain(address) -> str:
    """An email address for comparing: trimmed, lower case. One with a character outside ASCII compares equal to nothing:
    "\u212Aim@x.example" (a Kelvin sign) lower-cases to "kim@x.example", and look-alikes are how a check like this is fooled."""
    text = str(address or "").strip()
    return text.lower() if text.isascii() else ""


def people_on(record: dict) -> set:
    """The two addresses the handbook accepts (3.2): the account that placed the order, and the gift recipient."""
    gift = record["order"].get("gift") or {}
    return {plain(a) for a in ((record.get("customer") or {}).get("email"), gift.get("recipient_email"))} - {""}


def servable(case: dict) -> bool:
    """Do the look-ups' answers, put together again, give back this ticket's record exactly? (An edited record may be misshapen.)"""
    try:
        record, today = case["order"], str(case["ticket"].get("received_at") or "")[:10]
        return rebuild(order_view(record), customer_view(record, today)) == record
    except Exception:
        return False


def as_text(value) -> str:
    """JSON for a model to read. Every "<" is written as \\u003c, so that nothing a customer wrote can close a tag around it."""
    return json.dumps(value, indent=2, ensure_ascii=False).replace("<", "\\u003c")


class Shop:
    """The shop's tables: orders by number, the record behind each customer id, and the queues.
    queue_of: ticket id -> the queue it waits in (each set of tickets is a queue of its own).
    shared: the tickets whose records any session may ask for. The hidden set's are left out: a stray look-up must not carry a
    hidden ticket's record into a session that people read."""

    def __init__(self, cases: list, queue_of: dict, shared, secret=None):
        self.queue_of, self.orders, self.customers = queue_of, {}, {}
        for case in cases:
            if case["id"] in shared and (case["order"] or {}).get("order") and servable(case):
                self.orders[case["order"]["order"]["order_id"]] = case["order"]
                self.customers[case["order"]["customer"]["customer_id"]] = case["order"]
        self.queues = {which: Queue([c for c in cases if queue_of.get(c["id"]) == which], secret) for which in set(queue_of.values())}

    def queue(self, case_id: str) -> Queue:
        return self.queues[self.queue_of[case_id]]


LOOKUPS = ("lookup_order", "lookup_customer", "search_tickets")


class Counter:
    """The counter that one session asks at. It knows who wrote the ticket, answers the look-ups, applies the guard on reading
    and writes down every call."""

    def __init__(self, shop: Shop, case: dict, config: dict):
        self.shop, self.case, self.config, self.log = shop, case, config, []
        self.today = str(case["ticket"].get("received_at") or "")[:10]
        # The guard rests on this address. We take it that the helpdesk has authenticated it (a signed-in session, or checked mail).
        # It comes from the ticket's header. Nothing in the message can change it.
        self.sender = plain(case["ticket"].get("from_email"))
        self.named = tidy(case["ticket"].get("claimed_order_id"))
        # The ticket's own order, as it stood for this ticket, lies on top of the shared tables (two tickets can share an order).
        self.orders, self.customers = dict(shop.orders), dict(shop.customers)
        self.orders.pop(self.named, None)
        mine = case.get("order") or {}
        if mine.get("order"):
            self.orders[mine["order"]["order_id"]] = self.customers[mine["customer"]["customer_id"]] = mine
        self.exists, self.on_it = bool(mine.get("order")), bool(mine.get("order")) and self.sender in people_on(mine)
        self.set_guard(config.get("tool_guard"))

    def set_guard(self, on) -> None:
        """With the guard on, a sender who is not on the order is served as if the order did not exist, by every tool alike."""
        self.guard = bool(on)
        self.stranger, self.held_back = self.guard and not self.on_it, self.guard and self.exists and not self.on_it

    def names(self) -> list:
        return [name for name in LOOKUPS if name in self.config["tools"]]

    def answer(self, name: str, given) -> tuple:
        """(the text that goes back to the agent as the tool's result, whether it reports an error)."""
        given = given if isinstance(given, dict) else {}
        call = {"tool": str(name)[:40], "asked": "", "found": False, "senders_own": False, "refused_by_guard": False, "error": None}
        self.log.append(call)
        if name not in self.names():      # only these names ever reach getattr
            call["error"] = "no such tool"
            return "There is no such tool.", True
        return getattr(self, name)(given, call)

    def hand_out(self, call: dict, record, view, nothing: dict) -> tuple:
        """The guard on reading: a record goes out only if the ticket's sender is on it.
        A refusal is the very answer of "not found", so that it does not confirm that the record exists."""
        own = bool(record) and self.sender in people_on(record)
        call.update(found=bool(record), senders_own=own, refused_by_guard=self.guard and bool(record) and not own)
        return as_text(view(record) if record and not call["refused_by_guard"] else nothing), False

    def lookup_order(self, given: dict, call: dict) -> tuple:
        call["asked"] = asked = tidy(given.get("order_id"))
        return self.hand_out(call, self.orders.get(asked),
                             lambda record: order_view(dict(record, lookup={"queried_order_id": asked, "status": "found"})),
                             {"lookup": {"queried_order_id": asked, "status": "not_found"}, "order": None, "inventory": None})

    def lookup_customer(self, given: dict, call: dict) -> tuple:
        call["asked"] = asked = str(given.get("customer_id") or "").strip().upper()[:20]
        return self.hand_out(call, self.customers.get(asked), lambda record: customer_view(record, self.today),
                             {"lookup": {"queried_customer_id": asked, "status": "not_found"}, "customer": None})

    def search_tickets(self, given: dict, call: dict) -> tuple:
        """The search starts from the ticket itself, never from a value the model typed."""
        call["asked"] = by = str(given.get("by") or "")[:20]
        if by not in ("photo", "address", "order"):
            call["error"] = "bad input"
            return "by must be photo, address or order.", True
        found = self.shop.queue(self.case["id"]).search(self.case["id"], by, self.config.get("photo_distance", 8), self.stranger)
        call.update(found=bool(found["matches"]), senders_own=not self.stranger, refused_by_guard=self.held_back and by != "photo")
        return as_text(found), False


# ── the look-ups as commands, in a session's sandbox ──────────────────────
INPUT_OF = {"lookup_order": "order_id", "lookup_customer": "customer_id", "search_tickets": "by"}


def command(tool: str, argv: list) -> None:
    """python3 scripts/<tool>.py <ticket id> <value> [--guard]. The data lies beside the scripts, in ../data.json.
    What is printed starts with a line DESK-CALL {...} and ends with a line DESK-END, so that the session's record can be read by code."""
    guard, argv = "--guard" in argv, [a for a in argv if a != "--guard"]
    ticket_id, value = (argv + ["", ""])[:2]
    print("DESK-CALL " + json.dumps({"tool": tool, "ticket": ticket_id[:20], "input": {INPUT_OF[tool]: value[:60]}, "guard": guard}))
    with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data.json"), encoding="utf-8") as file:
        data = json.load(file)
    config = {"tools": [tool], "tool_guard": guard}
    if ticket_id not in data:
        print("There is no ticket %s. Usage: python3 %s.py <ticket id> <%s>" % (ticket_id[:20], tool, INPUT_OF[tool]))
    elif tool == "search_tickets":       # data: the masked rows of the queues
        shop = Shop([], {name: row["queue"] for name, row in data.items()}, ())
        shop.queues = {which: Queue(rows={n: r for n, r in data.items() if r["queue"] == which}) for which in set(shop.queue_of.values())}
        counter = Counter(shop, {"id": ticket_id, "ticket": {}, "order": None}, config)
        counter.stranger = guard and not data[ticket_id]["on_it"]
        print(counter.answer(tool, {"by": value})[0])
    else:                                # data: the tickets' headers and records
        cases = [dict(entry, id=name) for name, entry in data.items()]
        shop = Shop(cases, {}, {c["id"] for c in cases if c["shared"]})
        print(Counter(shop, dict(data[ticket_id], id=ticket_id), config).answer(tool, {INPUT_OF[tool]: value})[0])
    print("DESK-END")
    sys.stdout.flush()
