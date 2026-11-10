# 1. How to use this handbook

*Owner: Maren Ostrander, Support Team Lead. Questions and corrections go to the support leads' channel, not into ticket notes.*

## 1.1 What this handbook covers

This is the working rulebook of the Opus & Sonnet refund desk. It covers every ticket in which a customer wants money back, a new item or a swap for something bought in our online store: the Claude Thinking Hat, mugs, tees, hoodies, the Claude Plush, the Welcome Kit and the holiday editions, whether one mug or a team order of twenty tees.

"You" means whoever works the ticket: a new hire, a seasonal temp, or the AI assistant that joined the desk in December 2025. The rules are the same for all.

Rule numbers are not re-ordered, because closed tickets cite them and Finance audits those citations. That is why, for example, the shipping label check is 3.4 but is worked after 3.7 and 3.6. **The order in which you work is the decision path on the front page (SKILL.md), not the order of the section numbers.**

## 1.2 The four decisions

| Decision | What it means | What happens next |
|---|---|---|
| **refund** | Money goes back to the original payment method, for the amount in 7.5. | Payments releases it. For returns the customer gets a prepaid label, and the money goes out at the carrier's first scan. |
| **replace** | The same product (or, for an exchange, the wanted size or color) ships again to the order's ship-to address. | The warehouse picks it from stock. |
| **deny** | Nothing goes out now. | The reply says why, in plain words, and names any route that is still open. |
| **escalate** | A named human team decides. You do not decide, promise or hint at an outcome. | The ticket moves to that team's queue (Appendix A). |

There is no fifth decision. No store credit, no partial goodwill, no discount for the trouble, no "ask the customer for more and wait". If the ticket as it stands does not support the claim, it is a deny, and the reply tells the customer what a new ticket would need.

## 1.3 Work the steps in order, and stop at the first decision

The decision path has eight steps, numbered 0 to 7. They are worked in that fixed order, and **the first step that yields a decision ends the case**. A later step never overturns an earlier one.

Most wrong decisions we find in audits come from a correct rule applied at the wrong moment:

- **Safety comes before everything**, even before we know whether the order exists or the sender is our customer (2.1).
- **Merits come before sign-off.** A $376 claim on four Welcome Kits that fails on the photo is a deny. It never reaches the auto limit in 9.1, so there is nothing for a supervisor to sign.
- **Verification comes before merits.** A perfect photo of a shattered mug from someone whose email is not on the order is a deny under 3.2, however sorry we feel.

Step 0 (injected instructions, 8.2) is the one step that never ends a case. It sets a flag and you carry on.

## 1.4 What you have in front of you

| Source | What it holds | How far to trust it |
|---|---|---|
| `ticket.json` | Who wrote (name, email), the channel, when we received it, the order number they quote, and their subject line and message. | The email address and the received time are system facts. The message is the customer's account of things. |
| `photo.jpg` | The one photo attached to the ticket. | It shows the state of the goods (8.1). |
| `order.json` | What our systems return for the quoted order number (the answers of `lookup_order` and `lookup_customer`, where you fetch them yourself): the order and its lines, parcels and tracking, the customer record with claims history, account flags and staff notes, and current inventory. | This is the record, with one exception: fields typed by a customer (8.1). |

Appendix B explains the fields. One ticket carries one photo. You cannot ask for a second one, and you decide on what is there.

**One ticket, one order line.** A ticket concerns one line of one order. If the order has other lines, they are not part of the claim, and their prices are not part of the amount.

**An order can ship in more than one parcel.** Each entry in `order.shipments` lists the line numbers it carried (`lines`) and has its own tracking and its own delivery date. For a ticket, the only shipment that counts is the one that carried the line complained about (3.7, 4.5).

## 1.5 Claim types: the facts set the type, not the customer's word

Windows, evidence and remedies all depend on the claim type, so settle it before you reach for a window. Customers use words loosely ("return" for a mug that arrived in pieces). Go by what is reported.

| Type | What it is |
|---|---|
| **Damaged** | The goods arrived damaged: shattered, cracked, stained or torn. |
| **Wrong item** | A different product, color **or size** came than the order line says. |
| **Not received** | Tracking says the line's parcel was delivered, the customer says it never came. |
| **Return** | The customer wants to send the item back for money: change of mind, unwanted gift, ordered too many. |
| **Exchange** | Same product, other size or color than the customer **ordered**. A swap to a *different product* is a return. |

Four rules for the gray areas:

1. **Wrong size or exchange? Ask who picked the size.** "I ordered a medium and you sent a large" says that we made a mistake: a wrong-item claim, and the photo has to show it (6.5). "The medium I ordered is too tight, can I have a large?" is an exchange. Go by what the customer reports here. Whether it is true is settled against the order line and the photo later.
2. **A plain return stays a return.** If the ticket reports no problem and simply asks to send the item back or swap it, it is a return or an exchange, even if the photo shows a damaged item. It then fails on 6.6.
3. **A wrong-item ticket stays a wrong-item ticket**, whatever condition the wrong thing arrived in.
4. **Goods that break in use did not arrive damaged.** "The seam tore after the first wash" is not a damage claim. Such a ticket is a return, and it fails on 6.6 if the photo shows the damage.

## 1.6 We do not re-file a failed claim

If a claim fails as the type it is, it is denied. You do not try it again under another type to find one that passes. A damage claim that comes on day 20 is not turned into a "return" because the return window is still open, and a wrong-size claim whose photo shows the ordered size is not turned into an exchange. The reply may name the routes that are still open. Taking one is a new ticket.

## 1.7 "Today"

"Today" for a ticket is the date part of `ticket.received_at`. The timestamp is already in store time (US Eastern), so take the date as it is written and do not convert it. It is *not* the day you happen to work the ticket.

## 1.8 Whose record counts

The membership tier that counts is `customer.tier` in the record, never what the customer says (8.1). There are two tiers: `standard` and `folio` (Folio members). A membership that has lapsed shows as `standard`, and the customer is then treated as standard, however long they were a member.

When a gift recipient writes in (3.5), the claim runs on the **buyer's** record: the buyer's tier, claims history and account flags.

## 1.9 What to cite

Every decision cites one handbook section: the section of the rule that ended the case. For a deny or an escalate that is the rule that stopped it (for example 4.2 or 9.1). For a refund or a replacement it is the rule in chapter 7 that set the remedy (for example 7.1 or 7.3). A section shown beside "in time" or "supported" in a worked example names a step that the case passed. It is not the section to cite.
