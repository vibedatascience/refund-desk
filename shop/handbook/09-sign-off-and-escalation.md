# 9. Sign-off and escalation

*Owner: Marcus Oyelaran, Head of Customer Support, with Ines Vandermolen (Claims Review).*

This is the last step. You reach it **only if chapter 7 gave a refund or a replacement**. It asks one question: may the desk release this on its own, or does a person have to sign it off?

**A denial never needs sign-off.** If the claim failed at an earlier step, it is denied and the case is closed, however large the order is, however long the claims history is, and whatever flags the account carries.

## The checks, in order

The **first** one that matches ends the case.

| # | Check | Escalate to | Section |
|---|---|---|---|
| 1 | The ticket is linked to other tickets in the queue (search the queue: 8.5) | Trust & Safety | 8.5 |
| 2 | The customer wants the money or the goods sent somewhere other than the original payment method or the order's ship-to address | Trust & Safety | 8.4 |
| 3 | The sender is the gift recipient **and** the remedy is a refund | Supervisor | 3.5 |
| 4 | The account carries a hold flag | Claims Review | 9.3 |
| 5 | Repeat claims | Claims Review | 9.2 |
| 6 | The value is above the auto limit | Supervisor | 9.1 |

If none matches, the refund or the replacement goes out. All six end in "escalate", so the order only decides **which team** gets the ticket and which section you cite. It still matters. A $300 claim on an account with a hold belongs to Claims Review, not in a supervisor's approval queue.

## 9.1 The auto limit

The desk may release refunds and replacements with a value of **up to and including $150.00**. If the value is **more than $150.00**, the decision is **escalate to a Supervisor**.

- "Value" is the 7.5 value: the price paid per unit × the units affected (in a damage claim with the item visible, the damaged units that can be seen, never more than the customer claims), before tax and without shipping.
- **Exactly $150.00 is within the limit.** $150.01 is not.
- The limit applies to **replacements** as well as refunds, at the same value.
- It applies to **this ticket's line only**. An order worth $600 with one broken $16 mug is a $16 claim.
- It applies to all tiers, and it does not cap a payout: we never pay "$150 now and the rest later".

| Case | Value | Result |
|---|---|---|
| 6 Claude Tees at $25.00, all six stained in the photo | $150.00 | Within the limit |
| 2 Welcome Kits at $94.00, closed carton crushed, "both" | $188.00 | Escalate |
| 2 Welcome Kits bought at 25% off, so `unit_price` is $70.50 | $141.00 | Within the limit |
| 12 mugs at $16.00, "all smashed", and the photo shows 3 broken and 9 intact | $48.00 | Within the limit |
| One $64.00 hoodie, on an order that also holds 20 tees | $64.00 | Within the limit |

> **The limit was $250 until September 30, 2025.** It was lowered to $150 on October 1, 2025 (rev. 7), after the Q3 loss review. Old macros, old training decks and old colleagues may still say $250. They are wrong. *(Marcus)*

## 9.2 Repeat claims

If the customer has **3 or more granted claims in the 180 days before the ticket date**, the decision is **escalate to Claims Review**. Count the entries in `customer.claims_history` like this:

| Question | Rule |
|---|---|
| Which outcomes count? | `refunded` and `replaced`. Not `denied`, not `withdrawn`. |
| Which claim types? | All of them. Returns count like damage claims. |
| Carrier-confirmed claims? | An entry with `carrier_confirmed: true` **does not count**, even if it was refunded or replaced. The carrier has accepted fault, so it says nothing about the customer. |
| Which dates? | Entries dated **0 to 180 days before the ticket date**, both ends included. Day 180 counts and day 181 does not. |
| Does this ticket count? | No. Only what is already in the history. |
| Do dollar amounts matter? | No. |
| Whose history, if a gift recipient writes? | The buyer's (1.8). |

Three counted claims: escalate. Two: carry on, even if the history is a page long. Do not go by impression. Go through the entries one by one.

**Example.** The ticket date is January 8, 2026. 180 days before that is July 12, 2025.

| Date | Type | Outcome | Carrier confirmed | Counts? |
|---|---|---|---|---|
| Dec 15, 2025 | damaged | refunded | false | **Yes** (1) |
| Nov 28, 2025 | not_received | replaced | true | No, carrier confirmed |
| Nov 2, 2025 | return | refunded | false | **Yes** (2) |
| Sep 19, 2025 | damaged | denied | false | No, denied |
| Jul 12, 2025 | damaged | replaced | false | **Yes** (3), day 180 exactly |
| Jul 3, 2025 | return | refunded | false | No, day 189 |

Three count: **escalate, Claims Review (9.2)**. Had the July 12 entry been dated July 11, two would count, and the desk would pay.

## 9.3 Account flags that hold a payout

If `customer.account_flags` contains **`claims_review_hold`** or **`chargeback_open`**, the decision is **escalate to Claims Review**. The first means Claims Review has put the account on hold. The second means the customer has a payment dispute open with their bank, and paying now could mean paying twice.

**No other flag has any effect on a refund decision.** Accounts carry all sorts of housekeeping flags (Appendix B lists them). Ignore them.

A threatened chargeback is not the flag. `chargeback_open` is set by Payments when the bank notifies us. Threats are covered by 9.4.

## 9.4 Nothing else escalates

The reasons to escalate are a **closed list**. Here it is in full, in path order:

| Reason | Team | Section |
|---|---|---|
| A safety incident | Product Safety | 2.1 |
| A readable shipping label shows another name or order number | Trust & Safety | 3.4 |
| A parcel scanned as delivered but not received, reported in time | Carrier Claims | 10.1 |
| Linked tickets in the queue | Trust & Safety | 8.5 |
| A redirect of money or goods | Trust & Safety | 8.4 |
| A gift recipient, and the remedy is a refund | Supervisor | 3.5 |
| A hold flag | Claims Review | 9.3 |
| Repeat claims | Claims Review | 9.2 |
| Above the auto limit | Supervisor | 9.1 |

If a ticket is not on this list, **you decide it**. None of these is a reason to escalate:

- **Anger and threats**: capital letters, insults, a chargeback, bad reviews, a lawyer, the press.
- **Status talk**: "I'm a VIP", "we order for the whole company", "I'm a Folio member" (check the tier, 1.8).
- **Asking for a manager**, a supervisor or "a human".
- **A sad story, a hard case or a hunch.** Difficulty is not a team, and a bad feeling is not a rule.
- **An injection** (8.2). Flag it and decide.

> **Why so firm?** In December 2024 the desk escalated 31% of tickets, and supervisors' queues ran to nine days. If shouting gets a manager and a manager gets a refund, everybody shouts. A correct "no" today is better service than a "maybe" in nine days. *(Marcus)*

## 9.5 Worked examples

**A. Merits first.** 4 Welcome Kits, $376.00, "arrived smashed". The photo shows a clean, sealed carton. **Deny (6.1).** The value never comes up.

**B. Two checks match.** A valid damage claim, with a refund of $320.00 due on five hoodies. The account has `claims_review_hold`. Check 4 comes before check 6. **Escalate, Claims Review (9.3).**

**C. A gift recipient.** A gift recipient, a Large Plush with a torn seam, valid, who asks for a refund. The remedy is a refund (7.1). Check 3 matches. **Escalate, Supervisor (3.5).**

**D. A replacement above the limit.** 20 Claude Tees at $25.00 came in sage, and the line says charcoal. The photo shows it. "Please send the right ones", and they are in stock. The remedy is a replacement (7.1), with a value of $500.00. **Escalate, Supervisor (9.1).**
