# Open questions

The authoritative tracking is in slicer: **Interview** slices and each slice's
**Owner input** section (`slicer find Interview`). This page is an index of
the larger open topics.

| Topic | Tracked in | Notes |
|---|---|---|
| Dev tooling and conventions | Interview: dev tooling and conventions | Test runner, lint, pinning, layout |
| Device pairing flow | Interview: device pairing and admin bootstrap | Bootstrap, QR/link, lifetimes, roles |
| Recipe repo conventions | Interview: recipe repo conventions | Paths, slugs, headings, commit identity |
| Items, categories, staples, units | Interview: grocery items, categories, staples and units | |
| Planning rules | Interview: planning rules | Nights, recency, skips |
| Shopping list behavior | Interview: shopping list behavior | Aggregation, clearing, concurrency |
| Deployment host and HTTPS | Interview: deployment host and network; Spike: HTTPS on the deployment network | See [research](research/https-lan.md) |
| WSGI server | Container image and deployment | New dependency; needs approval |
| Receipts mailbox and email formats | Interview: receipts mailbox and samples | Item-level detail per store is **unverified** |
| Pantry model | Interview: pantry model | Shelf lives, taps, nudges |
| Notifications | Interview: notifications | Channels, schedule |
| Conversation channels | Interview: notifications | Conversation mailbox, Matrix homeserver and client library (new dependency), commands, autonomy levels |
| Automated store ordering | Not planned | Order placement is out of scope; cart building is Later ([ADR 0026](decisions/0026-autonomy-levels.md)). Cart APIs **unverified** |
