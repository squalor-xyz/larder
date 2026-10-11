# Requirements

Tags: **MVP** · **P2** (Phase 2) · **Later**. IDs are stable; slices and ADRs
reference them.

## Functional requirements

### R. Recipes

| ID | Requirement | Tag |
|---|---|---|
| R1 | Recipes live in a dedicated git repo ([ADR 0005](decisions/0005-recipes-own-repo-plus-importers.md)). | MVP |
| R2 | Import recipes from other sources through pluggable importers. | MVP |
| R3 | Hugo site importer, run once ([ADR 0011](decisions/0011-blog-one-time-import.md)): TOML front matter (`+++`), an ingredients list, and a directions/steps section. Heading variants must be tolerated (e.g., `## Ingredients:`, `## Steps:`). | MVP |
| R4 | Edit recipe metadata in the UI, stored in larder's DB: meal type, servings, in rotation, favorite, prep effort ([ADR 0006](decisions/0006-metadata-in-larder-db.md)). | MVP |
| R5 | Re-indexing is idempotent and keeps edited metadata and confirmed ingredient parses. | MVP |
| R6 | Additional importers (e.g., a URL with schema.org Recipe markup). | Later |
| R7 | Publish recipes from the recipe repo to a Hugo site. | Later |

### I. Ingredient parsing

| ID | Requirement | Tag |
|---|---|---|
| I1 | A rule-based parser splits each line into quantity, unit, item and notes ([ADR 0007](decisions/0007-rule-based-ingredient-parsing.md)). | MVP |
| I2 | Normalize unit synonyms (tbsp/tablespoon, tsp/teaspoon, lb/pound, oz) and package forms ("1 can (15 oz)"). | MVP |
| I3 | Handle optional ingredients, alternatives ("honey, maple syrup, or agave") and ranges ("2-3"). | MVP |
| I4 | Flag non-food lines (e.g., "1 small mason jar") so they're excluded from lists. | MVP |
| I5 | Low-confidence lines go to a review queue; one confirmation is cached permanently. | MVP |
| I6 | Map parsed items to canonical grocery items with aliases. | MVP |
| I7 | Optional local-LLM parse suggestions for items in the review queue. | Later |

### P. Planner

| ID | Requirement | Tag |
|---|---|---|
| P0 | Weekly planning session: a pre-filled suggested plan, editable in a few minutes ([ADR 0013](decisions/0013-weekly-planning.md)). | MVP |
| P1 | Show tonight's dinner and a simple week view. | MVP |
| P2 | Suggest meals from the rotation, ranked by how recently each was made. | MVP |
| P3 | One tap marks a meal as made, which updates its recency. | MVP |
| P4 | Swap or skip a suggestion without friction. | MVP |
| P5 | Rank by likely on-hand and expiring ingredients. | P2 |
| P6 | Optional local-LLM suggestions for new meals using what's on hand. | Later |

### L. Shopping list

| ID | Requirement | Tag |
|---|---|---|
| L1 | One shared list; every paired device can add, check off and remove items. | MVP |
| L2 | Generate list items from the week's plan, aggregating quantities across recipes. | MVP |
| L3 | A staples list that can be added in one tap. | MVP |
| L4 | Order items by a configurable category order, for quick manual entry into a store app. | MVP |
| L5 | Leave out items that are probably already on hand. | P2 |
| L6 | Remember the store product name for each item. | Later |

### O. Orders and receipts

| ID | Requirement | Tag |
|---|---|---|
| O1 | Poll a dedicated mailbox over IMAP for order emails ([ADR 0003](decisions/0003-dedicated-mailbox.md)). | P2 |
| O2 | Pluggable per-store order email parsers. | P2 |
| O3 | Archive raw emails so they can be re-parsed. | P2 |
| O4 | Match order lines to canonical items, with review. | P2 |

### N. Pantry estimates

| ID | Requirement | Tag |
|---|---|---|
| N1 | Estimate on-hand items from purchases, recipe use and shelf-life decay. | P2 |
| N2 | Optional "used it" / "tossed it" taps correct the estimates. | P2 |
| N3 | "Use it up" nudges for items nearing their estimated shelf life. | P2 |
| N4 | Runout prediction for recurring staples (e.g., pet food, household supplies). | P2 |

### X. Notifications

| ID | Requirement | Tag |
|---|---|---|
| X1 | Pluggable channels (ntfy, email), configurable per person. | P2 |
| X2 | Scheduled pings (e.g., tonight's dinner, weekly planning reminder). | P2 |

### U. UI and access

| ID | Requirement | Tag |
|---|---|---|
| U1 | Installable PWA that works well on Android ([ADR 0004](decisions/0004-pwa-first.md)). | MVP |
| U2 | Minimal default view: tonight's dinner, the list, an add-item box. | MVP |
| U3 | Separate admin views: recipe metadata, parse review, imports, devices. | MVP |
| U4 | Reachable on the LAN (and optionally over a VPN); never exposed publicly. | MVP |
| U5 | Kitchen tablet mode. | Later |
| U6 | Native Android app. | Later |
| U7 | Each device is paired once; no login screen after that; devices can be revoked ([ADR 0010](decisions/0010-device-cookie-auth.md)). | MVP |

## Non-functional requirements

| ID | Requirement |
|---|---|
| NF1 | **Usability:** a non-technical user can use the everyday views without help after their device is paired. |
| NF2 | **Upkeep:** no required daily input; one short weekly planning session at most. |
| NF3 | **Privacy:** household data stays in the private DB; only recipe content goes to the recipe repo. |
| NF4 | **Least privilege:** credentials are limited to a dedicated mailbox, a repo-scoped deploy key and notification tokens. |
| NF5 | **Portability:** no household-specific values in code; stores, importers and channels are plugins or config. |
| NF6 | **Dependencies:** stdlib-first; every new dependency needs approval with a stated reason. |
| NF7 | **Operations:** runs as one container with one data volume; backup and restore are documented and tested. |
| NF8 | **Offline tolerance:** pages load from cache when the network is flaky; editing requires the server. |

## Non-requirements

Explicitly out of scope (see [goals](goals.md) non-goals):

- Automated ordering or store-account integration.
- Nutrition or calorie tracking.
- Public internet exposure or public sign-up.
- Multi-household tenancy in the MVP.
- Exact inventory counts.
- A login/password system for everyday users.
