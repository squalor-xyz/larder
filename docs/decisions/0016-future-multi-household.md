# 0016: Single household first, portable for others later

**Status:** Accepted direction (2026-10-11)

## Context

The first deployment serves one household. Longer term, other households
should be able to self-host their own instance, and a hosted multi-household
instance is possible.

## Decision

- The MVP targets a single household per instance.
- **Portability principle** from the start:
  - No household-specific data (names, stores, sources, schedules) in code;
    it lives in config or the DB.
  - Stores, recipe importers and notification channels are plugins.
  - Deployment concerns stay outside the app (e.g., TLS, [ADR 0015](0015-app-tls-agnostic.md)).
  - Keep data access in one layer, so a household boundary can be added later.

## Consequences

- Slightly more indirection now (config, plugin interfaces).
- Self-host packaging and hosted multi-household operation are Later items,
  each needing its own decisions (e.g., auth beyond device pairing, data isolation).
