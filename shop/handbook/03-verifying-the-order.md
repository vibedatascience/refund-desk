# 3. Verifying the order

*Owner: Maren Ostrander, with Tobias Renner (Trust & Safety) for 3.2, 3.4 and 3.5.*

**Working order.** The checks run in this order: 3.1, 3.2, 3.3, 3.7, 3.6, and then, as a step of its own, 3.4. Section 3.5 explains gift recipients. Its escalation is applied at sign-off, after the remedy is known (chapter 9). The numbers are historical (1.1).

## 3.1 The order cannot be found

If the lookup for the quoted order number comes back `not_found`, the decision is **deny**.

Customers type order numbers in many ways ("os-4771340", "#4771340", "4771340"). The order system tidies that up by itself: `ticket.claimed_order_id` is the number as typed, and `lookup.queried_order_id` is what was looked up. Go by `lookup.status`. Beyond that, do not look for the order by name, email or address, and do not try "nearby" numbers because one character looks like a typo.

## 3.2 Who is writing

The sender's email address (`ticket.from_email`) must equal one of two addresses in the record:

- `customer.email`, the account that placed the order, or
- `order.gift.recipient_email`, if the order was sent as a gift and the buyer entered the recipient's email.

Upper and lower case are ignored. **Anything else fails**, and the decision is **deny, disclosing nothing**. That includes:

| Situation | Result |
|---|---|
| Same name as the account holder, different address ("I'm writing from my work email") | Fails |
| A look-alike address: one letter swapped, an extra dot or tag, `.co` for `.com`, another mail provider | Fails |
| Spouse, parent, assistant, a colleague on the team the order was for | Fails |
| Someone who says the item was a gift to them, but the order has no gift block, or the gift block holds another email | Fails |
| Someone who can quote the order number, the address, the items and the price | Fails. Knowing things about an order is not being on it. |

The name in the ticket proves nothing, because anyone can type a name.

"Disclosing nothing" means the reply (macro V-1, the same one as for 3.1) does not even confirm that the order exists. Order numbers are printed on every label, and labels end up in recycling bins.

## 3.3 The item is not on the order

If the product the customer complains about is on no line of the quoted order, the decision is **deny**. You do not go looking for the right order yourself.

For a **wrong-item** claim, "the product complained about" is the one that *should* have come. The customer who ordered a hoodie and received a plush is complaining about the hoodie line.

This section is about the **product**, not its color or size. A customer who is sure they ordered the sage mug, on an order whose line says cream, is writing about that line. Whether the right thing came is settled in 6.5.

## 3.4 The shipping label check

*Added in rev. 6. Worked as its own step, after 3.1, 3.2, 3.3, 3.7 and 3.6 and before eligibility.*

Many photos show the parcel's shipping label. If a label is **readable** in the photo, compare two fields with the record:

| Field on the label | Compare with |
|---|---|
| SHIP TO name | `order.ship_to.name` |
| ORDER # | `order.order_id` |

If either readable field shows **another person or another number**, the decision is **escalate to Trust & Safety**. The photo is then of somebody else's parcel, or of another order.

- **Compare with the ship-to name, not the account holder.** Plenty of orders ship to a partner, an office or a gift recipient. A label that shows the ship-to name is a match, even if the account holder and the sender of the ticket have different names.
- **Capital letters, an initial for a first or middle name, and dropped middle names do not count.** "R. ALVARADO" and "ROSA ALVARADO" both match a ship-to name of Rosa Alvarado. "ROBERT ALVARADO" does not. A nickname or a different last name is a different name.
- **Unreadable or absent means no effect.** If there is no label in the photo, or it is blurred, torn, turned away or cut off, this step does nothing. If only one of the two fields can be read, compare that one. A field counts as readable only if you can read all of it. Never guess at half-legible characters.
- A missing label is **not** suspicious, and other label fields (address, tracking number, carrier) are not compared.
- Anything *else* written or stuck on the parcel, including a notes line on the label itself, is not part of this check. See 8.2.

A label mismatch is not an injection, and it does not set the security flag (8.2).

## 3.5 Gift recipients

A **gift recipient** is a sender whose email equals `order.gift.recipient_email`. Nobody else is one (3.2).

| | |
|---|---|
| Make a claim on the order (any type) | Yes. All rules apply as they would to the buyer. |
| Whose tier, claims history and flags count | The **buyer's** (1.8). |
| Get a **replacement** or an **exchange** | Yes, shipped to the order's ship-to address. |
| Get a **refund** | Not from the desk. Refunds go to the original payment method, which is the buyer's. If the remedy under chapter 7 comes out as refund, the decision is **escalate to a Supervisor**. |
| Learn the price or anything about the buyer | Never (8.3). |

The escalation depends on the **remedy the rules produce**, not on what the recipient asks for. A replacement that is out of stock becomes a refund (7.2), and a broken Holiday Mug is refund only (7.3). Both end with a supervisor, unless an earlier sign-off check matches (chapter 9).

This check sits in the sign-off step and has its place in the order there (chapter 9). A claim that fails on its merits is simply denied, gift or not.

## 3.6 Claims we have already settled

Look through `customer.claims_history` for entries with **this order number and this SKU**.

| Earlier outcome on the same order and SKU | Decision now |
|---|---|
| `refunded` or `replaced` | **Deny.** It is already resolved. This holds even if the earlier claim covered only part of the line. |
| `denied` or `withdrawn` | No effect. Work the ticket like any other. |

Entries for other orders, or for other SKUs on this order, have no effect here. They may count in 9.2.

## 3.7 Not delivered yet

Look at the shipment that carried the line complained about (1.4). If its tracking shows no final "delivered" scan dated on or before the ticket date (its `delivered_on` is empty, or later than the ticket date), the decision is **deny**: the parcel is still in transit. That another shipment of the same order has already arrived changes nothing. This holds for every claim type, including "it never arrived".

In the parcel's `events`, only a line with `status=delivered` is a delivered scan. `out_for_delivery`, `arrived_at_facility` and the delay statuses are not.

## 3.8 Worked examples

**A.** The ticket quotes OS-5581027. The lookup returns `not_found`. The message is polite and detailed, and the photo shows a cracked mug. **Deny (3.1).**

**B.** The account email is `j.harcourt@fernmail.example`. The ticket comes from `j.harc0urt@fernmail.example` (a zero for the o), signed "Jo Harcourt". **Deny, disclose nothing (3.2).**

**C.** The account holder is Lena Fischer and the ship-to name is Martin Fischer. The photo shows a crushed, unopened carton with the label "SHIP TO: M. FISCHER, ORDER # OS-4771340", which is the quoted order. It matches the ship-to name. Carry on.

**D.** The same order, but the label reads "ORDER # OS-4771304". That is another number. **Escalate, Trust & Safety (3.4)**, however convincing the damage.

**E.** The claims history shows this order and SKU as `replaced` on December 19. The customer writes on January 7 that the second mug set also arrived cracked. **Deny (3.6).**

**F.** The gift recipient (email matches the gift block) sends a photo of a shattered mug two days after delivery and asks for "my money back or whatever is easiest". The claim is valid. The wish is open, so 7.1 gives a replacement if it is in stock. It is in stock: **replace (7.1)**. Had it been out of stock, the remedy would be a refund, and the ticket would go to a Supervisor (3.5).

**G.** The order went out in two parcels. The one with the tees arrived on January 3. The one with the hoodie is at `arrived_at_facility` and has no `delivered_on`. On January 6 the customer writes that the hoodie "never showed up". **Deny (3.7).**
