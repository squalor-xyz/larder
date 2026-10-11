# 0001: Self-hosted, FLOSS, purpose-built

**Status:** Accepted (2026-10-11)

## Context

larder holds household data: meal plans, shopping habits and, later, grocery
orders. The target users run always-on home hardware. Existing FLOSS tools
cover parts of the problem (e.g., Mealie, Tandoor, Grocy, KitchenOwl), but none
is built around low-effort estimates from data a household already produces.

## Decision

- Self-hosted on the household's own hardware; FLOSS ([AGPL-3.0](../../LICENSE)); data stays local.
- Built as a new, purpose-built application rather than a fork.
- Local AI is preferred. Hosted APIs are optional and never required.

## Consequences

- No dependency on third-party SaaS availability or pricing.
- The project carries its own maintenance, so scope must stay small.
- Existing tools remain sources of ideas and import formats.
