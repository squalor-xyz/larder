# 0022: Receipts mailbox

**Status:** Proposed (2026-11-04, owner interview S31). It becomes Accepted when the sample emails are in and the per-store field inventory is written.

## Context

Store parsers cannot be written without real samples, and the mailbox's
provider and routing decide how IMAP access works
([ADR 0003](0003-dedicated-mailbox.md)). Whether the emails list items decides
whether Phase 2 is worth building.

## Decision

| Topic | Choice |
|---|---|
| Mailbox | A dedicated mailbox at a standard IMAP provider (Fastmail, mailbox.org or Gmail with an app password). The provider is not named yet. A provider that needs a bridge (Proton) is not the choice |
| Routing | Each store account's email is changed to the dedicated address. There is no forwarding filter |
| Samples | The owner collects 2-3 recent order emails per store as redacted `.eml` files in `tests/fixtures/orders/<store>/` |
| Item detail, substitutions, refunds | Answered from the samples |

## Open

- The provider, and the stores the household orders from.
- The per-store field inventory: whether each email lists items with quantities, and whether substitutions or refunds arrive as separate emails. A store whose emails lack item detail is parked.

## Consequences

- S32 (IMAP poller) can assume a plain IMAP login with credentials read from a secret file, not an OAuth flow.
- No store parser (S33) starts before its samples exist.
- Raw order emails are never committed; only redacted fixtures are.
