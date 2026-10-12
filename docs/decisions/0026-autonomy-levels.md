# 0026: Autonomy levels per person

**Status:** Accepted (2026-10-12). L2 is a Later option; L3 is out of scope.

## Context

Household members want to limit how far larder may act on their behalf, from
just telling them things up to building a store cart or placing an order.
Acting on a store account means holding store (and possibly payment)
credentials, which [ADR 0003](0003-dedicated-mailbox.md) and the
least-privilege principle currently rule out. Store cart APIs are unverified.

## Decision

Each person has an autonomy level in config. The command core
([ADR 0025](0025-conversational-channels.md)) checks it in code before every
intent; no prompt or message can raise it.

| Level | larder may | Status |
|---|---|---|
| L0 | Send notifications and answer questions (read-only) | Phase 2 |
| L1 | Also change the shopping list and pantry estimates | Phase 2 |
| L2 | Also build a cart on a store account, without checking out | Later |
| L3 | Also place an order | Out of scope |

- The default for a new person is L0. The admin raises it.
- **L2 prerequisites** (each decided in its own ADR when L2 is picked up): a
  per-store adapter verified against that store, store credentials stored
  as secrets, a spending cap, a confirmation step before anything is added,
  and an amendment to ADR 0003.
- **L3** stays a non-goal (NG1). Revisiting it needs a new ADR.

## Consequences

- Phase 2 needs no store or payment credentials.
- Each intent declares its required level, which is easy to test.
- Building carts later stays possible without redesigning the core.
