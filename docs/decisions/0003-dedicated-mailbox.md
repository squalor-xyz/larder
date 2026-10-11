# 0003: Dedicated least-privilege mailbox for receipts

**Status:** Accepted (2026-10-11). Provider and routing are deployment-specific.

## Context

Order confirmation emails are the cheapest source of "what came into the
house". Giving a tool access to a personal inbox, payment methods or store
accounts is an unacceptable risk.

## Decision

larder reads only from a dedicated mailbox that exists solely to receive
grocery order emails. It never holds credentials for personal email, payment
methods or store accounts.

## Consequences

- If larder's credentials leak, only order emails are exposed.
- Order emails must be routed to that mailbox, by changing the store account's
  address or by forwarding rules.
- Parsers depend on what each store's emails contain, which must be verified
  per store from real samples.
