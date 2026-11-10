# 7. Remedies and amounts

*Owner: Maren Ostrander, with Finance for 7.5.*

You are here because the claim is verified, eligible, in time and supported. This chapter settles **what** the customer gets and **how much it is worth**. Whether the desk may release it on its own is the next step (chapter 9).

## Which rule applies

Take the **first** row that fits.

| # | The valid claim is | Remedy | Section |
|---|---|---|---|
| 1 | A return | Refund | 7.4 |
| 2 | An exchange | Replace with the wanted size or color if that is in stock, else refund | 7.4 |
| 3 | Damaged or wrong item, on a line flagged `final_sale` | Refund only, whatever the customer asks for | 7.3 |
| 4 | Any other damaged or wrong-item claim | The customer's wish decides (7.1). A replacement that is out of stock becomes a refund (7.2) | 7.1, 7.2 |

**"In stock"** means that the `on_hand` figure in `inventory` for the SKU to be sent is **at least the number of units affected** (7.5). A SKU that does not appear in `inventory` counts as 0 on hand. Go by `on_hand`, not by the status word next to it. If 6 units are affected and 4 are on hand, the item is not in stock. We do not split a remedy into part replacement and part refund, and we do not back-order. For damaged and wrong-item claims, the item to be sent is the SKU that was ordered, even if the customer would now rather have another color or size. For an exchange, it is the wanted size or color.

## 7.1 The customer's wish

| The customer asks for | Remedy |
|---|---|
| A refund | **Refund** |
| A replacement | **Replace** if in stock, else 7.2 |
| Nothing in particular, or "either is fine", "whatever is easiest", "please take care of this" | **Replace** if in stock, else 7.2 |

If the message names both without preferring one, treat it as "either".

## 7.2 Out of stock

Whenever 7.1 calls for a replacement and the item is not in stock, the remedy is a **refund**, and you cite 7.2. We do not offer a different color, a similar product or a wait for the next delivery. (An exchange that is out of stock cites 7.4.)

## 7.3 Final sale: refund only

A valid damaged or wrong-item claim on a line flagged `final_sale` is **refunded, whatever the customer asks for**, even if inventory shows units on hand.

## 7.4 Returns and exchanges

- A valid **return** is **refunded**.
- A valid **exchange** is a **replacement** with the wanted size or color if that is in stock. Otherwise it is a **refund**. Both cite 7.4.

## 7.5 What the claim is worth

**Value = price paid per unit × units affected.**

**Price paid per unit** is the line's `unit_price`: after discounts, before tax. It is not the list price (`list_price`), not the order total or the line total when only some units are affected, and not any figure in the customer's message.

**Units affected** starts from what the customer claims: the number of units they say are affected, **capped at the line's quantity**, and if they give **no number, the whole line**. Only a number counts: "three", "both", "two of the four", "a couple" (two). "A few", "some", "several" and "most" are no number.

**In a damage claim, count what you can see.** When the item is visible in the photo, the desk pays for **the damaged units that can be seen**, never more than the customer claims and never more than the line's quantity. Units that are in the picture and intact are not paid, whatever the message says. When the photo shows only a closed package, the customer's number counts.

| Line quantity | The customer says | The photo shows | Units affected |
|---|---|---|---|
| 8 | "three broke" | 8 mugs, 3 of them broken | 3 |
| 8 | "the whole lot is smashed" | 8 mugs, 2 of them broken and 6 intact | 2 |
| 8 | "two are cracked" | 8 mugs, 4 of them cracked | 2, never more than the customer claims |
| 8 | no number | 8 mugs, 5 of them broken | 5 |
| 8 | "six broke" | A closed carton with major damage | 6 |
| 8 | no number | A closed carton with major damage | 8, the whole line |
| 4 | "five broke" | A closed carton with major damage | 4, capped at the quantity |

**A set or kit sold as one unit is one unit.** The Claude Mug Set of 4 and the Claude Welcome Kit (a mug, a tee, a hat and a small plush in a gift box) are each one unit at one price. One broken mug in the set, or in the kit, means one unit affected: the set, or the kit.

**Not part of the amount:** shipping fees (the desk does not refund them) and tax (the payment system adds it back by itself).

**The same value counts for a replacement**, and that is the figure measured against the limit in 9.1. The stock test above uses the same units.

When you report a decision, the **amount** is this value for a refund, and 0 for a replacement, a denial or an escalation.

## 7.6 Worked examples

**A. The wish decides.** A Large Plush at $48.00 arrives with a torn seam, valid, and the customer wants money back. **Refund $48.00 (7.1).**

**B. Final sale.** A Holiday Mug arrives cracked, reported on day 1, "please send another". There are 30 on hand. **Refund $10.00 (7.3).**

**C. Part of a line.** 8 Claude Mugs at a list price of $16.00, bought with 10% off, so `unit_price` is $14.40. "Three arrived broken, I'd like those refunded." The photo shows all eight, three of them broken. **Refund $43.20 (7.1)**, which is 3 × $14.40. It is not $115.20 (the line) and not $48.00 (the list price).

**D. The photo shows fewer.** 12 Claude Mugs at $16.00 for a team. "Every single one is smashed, refund the lot." The photo shows the opened carton with all twelve: two are shattered and ten are whole. **Refund $32.00 (7.1)**, not $192.00, so the auto limit (9.1) is not reached.

**E. Not enough stock.** 10 Thinking Hats at $28.00, "six came stained, please replace them", and the photo shows six stained and four clean. `on_hand` is 4. Not in stock for 6 units. The remedy is a refund of 6 × $28.00 = $168.00 (7.2), which is above the auto limit. **Escalate, Supervisor (9.1).**

**F. A kit.** A Welcome Kit at $94.00, and the mug in it is cracked. The customer asks for a refund. The kit is one unit. **Refund $94.00 (7.1).**

**G. Wrong size.** The line says Claude Hoodie, charcoal, M. The neck label in the photo reads S. "Please send the M." M in charcoal has 5 on hand. **Replace (7.1).** With 0 on hand: **refund $64.00 (7.2).**

**H. Exchange.** A Claude Tee, cream, M, as ordered, and the customer wants an L, day 9, unworn. L in cream has 40 on hand. **Replace (7.4).** With 0 on hand: **refund $25.00 (7.4).**
