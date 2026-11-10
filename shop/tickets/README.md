# The tickets

Refund tickets of **Opus & Sonnet**, a made-up US online store for Claude-themed merchandise. Store, people, addresses, carriers and orders are invented; emails end in `.example`, phone numbers are 555-01xx. The queue is the post-holiday backlog of January 2026.

One folder per ticket, `shop/tickets/RD-xxxx/` (the numbers are those of the store's queue, so they have gaps):

| file | what it is |
|---|---|
| `ticket.json` | the help desk ticket: sender, channel, time received, order number as typed, subject, message, and the photo's file hash and perceptual hash |
| `photo.jpg` | the one photo the customer attached (a 3D render, so what it shows is known exactly) |
| `order.json` | what the store's systems know about that order number: order, shipments, customer record, stock. The agents fetch it through their tools |
| `thumb.jpg` | a small copy of the photo for the dashboard |

`index.json` lists the tickets and the set each belongs to. The right answers are not in this folder.
