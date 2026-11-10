# 10. Missing parcels

*Owner: Lorna Ashdown, Carrier Claims.*

## 10.1 Scanned as delivered, but the customer has nothing

If tracking for the shipment that carried the line (1.4) shows a final "delivered" scan, the customer says that it never came, and the ticket is **in time**, the decision is **escalate to Carrier Claims**, who open a trace with the carrier.

- **In time** means the 4.2 window: 14 days from the delivered scan for standard customers and 30 days for Folio members, with no holiday extension. After the window the decision is **deny**, citing 4.2. A supervisor's exception (4.4) can lift the window.
- **No proof is needed, and the photo is not assessed as evidence.** Customers attach a picture of an empty porch or something random because the form insists on a file. Two things come before this step and still apply: a readable label is checked under 3.4, and planted text is flagged under 8.2.
- The desk **never refunds or replaces a missing parcel itself**, however small the value and whatever the customer asks for.
- Everything **before** the evidence step applies as usual. Nothing **after** it applies: the case ends here, so there is no remedy to set and no sign-off.

## 10.2 Not scanned as delivered

If that shipment has no delivered scan on or before the ticket date, the parcel is not missing. It is late. That is a **deny under 3.7**, and 3.7 is the section you cite.

## 10.3 What Carrier Claims does with it

The carrier has up to 8 business days to answer a trace. If it accepts the loss, Carrier Claims refunds or reships, and the entry in the claims history is marked `carrier_confirmed: true`, which is why such entries do not count against the customer in 9.2. Do not promise an outcome or a date beyond "about two weeks".

## 10.4 Worked examples

**A.** Standard. The delivered scan is dated December 29, and the ticket is received on January 6 (day 8): "never got it, checked with neighbors". The photo shows an empty doorstep. **Escalate, Carrier Claims (10.1).**

**B.** The same, but the scan is dated December 18 (day 19). **Deny (4.2).** If the record says `folio`, day 19 is within 30: **escalate, Carrier Claims (10.1).**

**C.** A $16.00 mug, day 3: "just refund it, it's not worth a trace". **Escalate, Carrier Claims (10.1).**
