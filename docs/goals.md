# Goals

## Problem

Household food management has three recurring friction points:

1. **Deciding what to eat.** The daily "what's for dinner?" question, which
   tends to fall back on a short rotation.
2. **The shopping list.** Forgotten items, duplicate purchases, and several
   people adding to the list in different places.
3. **Inventory and waste.** Not knowing what's on hand, food spoiling, and
   rebuying things already in the house.

Households already produce useful data without trying: grocery orders
(pickup/delivery confirmation emails) and recipes. larder turns that data into
decisions.

## Goals

- **G1:** Answer "what's for dinner?" in seconds, from meals the household actually makes.
- **G2:** One shared shopping list that every household member trusts.
- **G3:** Reduce waste and duplicate purchases using order data instead of manual logging (Phase 2).
- **G4:** Near-zero required upkeep. Optional taps improve accuracy; none are required.
- **G5:** Usable by non-technical household members without help: a home-screen icon, tonight's dinner, the list.
- **G7:** Reachable by message from anywhere: email or Matrix to add to the list, ask what's low, and answer larder's pantry questions (Phase 2; [ADR 0025](decisions/0025-conversational-channels.md)).
- **G6:** Portable: any household can run it with its own stores, sources and network.

## Non-goals

- **NG1:** Placing store orders automatically. Store cart APIs are unverified; manual entry is assumed. Building a store cart without checking out is a Later option ([ADR 0026](decisions/0026-autonomy-levels.md)).
- **NG2:** Nutrition or calorie tracking.
- **NG3:** Access from the public internet.
- **NG4:** Multi-household or hosted operation in the MVP. That's a future direction ([ADR 0016](decisions/0016-future-multi-household.md)).
- **NG5:** Exact, item-by-item inventory tracking.

## Principles

- **Self-hosted and FLOSS; data stays on the household's hardware** ([ADR 0001](decisions/0001-self-hosted-floss.md)).
- **Least privilege.** larder never holds personal email, payment or store
  credentials ([ADR 0003](decisions/0003-dedicated-mailbox.md)).
- **Good estimates beat perfect records.** A good guess with zero effort beats a perfect record nobody keeps.
- **Simple by default.** The everyday view is minimal; admin and review
  screens are separate.
- **Stdlib-first, few dependencies** ([ADR 0002](decisions/0002-python.md)).
- **Portable.** No hardcoded household data; stores, importers and
  notification channels are pluggable; deployment concerns such as TLS stay
  outside the app ([ADR 0015](decisions/0015-app-tls-agnostic.md), [ADR 0016](decisions/0016-future-multi-household.md)).
- **Local AI by preference.** Local models (e.g., Ollama) first; hosted APIs
  are optional and never required.
