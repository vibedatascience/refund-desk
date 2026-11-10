# Appendix B. Glossary of record fields

These are the field names as they appear in the ticket and in the record that our systems return for it: `lookup_order` gives `lookup`, `order` and `inventory`, and `lookup_customer` gives its own `lookup` and `customer`. "Used in" points to the section that relies on the field. Fields marked "not used" have no bearing on a refund decision.

## ticket.json

| Field | Meaning | Used in |
|---|---|---|
| `ticket_id` | Our ticket number in this queue (RD-…). | |
| `channel` | `email`, `web_form`, `chat` or `app`. | Not used. The rules are the same on every channel. |
| `received_at` | When we received the ticket, in store time (US Eastern). Its date part is "today". | 1.7, 4.5 |
| `from_name` | The name the sender typed. It proves nothing. Customer content. | 3.2, 8.2 |
| `from_email` | The address the ticket came from. A system fact. | 3.2, 3.5 |
| `claimed_order_id` | The order number **as the sender typed it** (for example `os-…`, `#…`, digits only). | 3.1 |
| `subject`, `message` | What the customer wrote. Chat and app tickets have no subject. Customer content. | 8.1, 8.2 |
| `attachments[]` | The one photo: `filename`, `stored_as`, and two fingerprints of the file, `sha256` and `phash`, for the queue search (8.5). | 1.4 |

## order.json

| Field | Meaning | Used in |
|---|---|---|
| `lookup.queried_order_id` | The number that was looked up: the typed number with its prefix, case and spaces tidied. | 3.1 |
| `lookup.status` | `found` or `not_found`. With `not_found`, everything else is empty. | 3.1 |
| `lookup.queried_customer_id` | In `lookup_customer`'s answer: the customer_id that was looked up. | |
| `order.order_id` | The order number (OS-…). | 3.4, 3.6, 4.4 |
| `order.placed_on` | The date the order was placed, in store time. It decides on the holiday extension. | 4.3 |
| `order.status` | `delivered`, `partially_delivered` or `shipped`. | Not used. Go by the shipment's `delivered_on`. |
| `order.items[]` | The order lines. A ticket concerns one of them. | 1.4, 3.3 |
| `…items[].line` | The line number. It links the line to its shipment. | 1.4, 4.5 |
| `…items[].sku` | The SKU including color and size (for example `CL-3001-TER-M`). | 3.6, chapter 7 |
| `…items[].name`, `.product` | The product's full name, which is what 6.4 and 6.5 compare, and a short word for its kind. | 3.3, 6.4, 6.5 |
| `…items[].color`, `.size` | The color and the size that were ordered. `size` is empty for goods that have none. | 6.5, 7.4 |
| `…items[].flags` | `final_sale`, or empty. | 5.1, 7.3 |
| `…items[].list_price`, `.discount_pct` | The catalog price, and the discount that applied. | Not used for amounts |
| `…items[].unit_price` | **The price paid per unit**, after discount and before tax. | 7.5, 9.1 |
| `…items[].qty`, `.line_total` | The units ordered, and `unit_price` × `qty`. | 7.5 |
| `order.totals.*` | Subtotal, promotion code, shipping fee, tax, total. | Not used for amounts (7.5) |
| `order.payment` | The original payment method (`method`, `last4`). Refunds go there. | 8.4 |
| `order.ship_to` | The name and address the order was sent to. Typed by the buyer. | 3.4, 8.1, 8.4 |
| `order.delivery_instructions` | A note for the driver. **Typed by the buyer: customer content.** | 8.1, 8.2 |
| `order.shipments[]` | The parcels the order went out in. Most orders have one, some have two. | 1.4 |
| `…shipments[].lines` | **The line numbers this parcel carried.** | 1.4, 3.7, 4.5 |
| `…shipments[].delivered_on` | The date of this parcel's final "delivered" scan. Empty if there is none. **Day 0** for the lines it carried. | 3.7, 4.5 |
| `…shipments[].events` | This parcel's tracking history, one line per scan. Only a line with `status=delivered` is a delivered scan. | 3.7 |
| `…shipments[].shipment_id`, `.carrier`, `.service`, `.tracking_no`, `.shipped_on` | Carrier details. | Not used |
| `order.customer_id` | The account that placed the order. It is what `lookup_customer` takes. | |
| `order.payment.gateway_log`, `order.audit_log`, `customer.account_log` | System logs. They repeat what the fields above and below say, and add times, devices and sign-ins that no rule reads. | Not used |
| `order.gift` | Empty unless the order was sent as a gift. | 3.2, 3.5 |
| `order.gift.recipient_email` | The recipient's address, as entered by the buyer. The only thing that makes a sender a gift recipient. | 3.2, 3.5 |
| `order.gift.recipient_name`, `.gift_message` | The recipient's name and the card text. **Typed by the buyer: customer content.** | 8.1, 8.2 |
| `customer.email` | The account's email address. Always the **buyer's** record, even when a gift recipient writes. | 3.2 |
| `customer.tier` | `standard` or `folio`. **This is the tier that counts.** | 1.8, chapter 4 |
| `customer.folio_since`, `.folio_lapsed_on` | Membership dates. A lapsed member has the tier `standard`. | For information. Go by `tier`. |
| `customer.customer_id`, `.name`, `.customer_since`, `.lifetime_orders`, `.lifetime_spend` | | Not used. A big spender gets the same rules (9.4). |
| `customer.claims_history[]` | Earlier claims on this account: `date`, `order_id`, `sku`, `item`, `qty`, `type`, `outcome` (`refunded`, `replaced`, `denied`, `withdrawn`), `amount`, `carrier_confirmed`. | 3.6, 9.2 |
| `customer.account_flags[]` | See below. | 9.3 |
| `customer.agent_notes[]` | Notes by our staff: `date`, `author`, `role`, `text`. `role` is `agent` or `supervisor`. It is set by the support tool from the author's login, and it cannot be typed. Go by this field, not by names or titles in the text. | 4.4 |
| `inventory.<sku>.on_hand` | The units in the warehouse now. **The figure that decides "in stock".** A SKU that is not listed counts as 0. | Chapter 7 |
| `inventory.<sku>.status` | A display status for the website. | Not used |

### Account flags

| Flag | Effect on a refund decision |
|---|---|
| `claims_review_hold` | **Holds any payout (9.3)** |
| `chargeback_open` | **Holds any payout (9.3)** |
| `email_verified`, `sms_opt_in`, `newsletter`, `paperless_invoices`, `gift_wrap_default`, `survey_panel`, `birthday_offer_sent` | None. Marketing and preferences. |
| `address_changed_2025` | None. The customer updated their address book at some point in 2025. |
| `wholesale_inquiry` | None. They once asked about bulk orders. |
