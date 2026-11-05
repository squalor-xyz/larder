# Data model

The MVP tables below are the schema in
[`0001_init.sql`](../../src/larder/migrations/0001_init.sql), plus
`pairing_token` from
[`0002_pairing.sql`](../../src/larder/migrations/0002_pairing.sql), ranges and
alternatives from migration 0003, and `category` from
[`0004_categories.sql`](../../src/larder/migrations/0004_categories.sql), plus
`parse_override.optional` from migration 0005 and `meal_plan.confirmed_at` from
migration 0006. Phase 2 tables are indicative only; each is added by its own slice as a new migration.

Conventions:

- Every table is `STRICT`. Primary keys are `id INTEGER PRIMARY KEY`, except
  `recipe_meta`, which is keyed by `recipe_id`.
- Times are ISO-8601 UTC text. Dates (`week_start`, `date`) are ISO dates.
- Booleans are `INTEGER` 0 or 1 with a `CHECK`. Enums are `TEXT` with a `CHECK`.
- Every foreign key names its `ON DELETE` action. Every foreign key column has
  an index.
- Larder opens SQLite with foreign keys on and WAL journal mode. Schema changes
  are numbered migrations in `src/larder/migrations/`, tracked in
  `schema_migrations`.

## MVP

| Table | Columns | Notes |
|---|---|---|
| **person** | id, name (unique), active | Mirror of the people in config; see below |
| **device** | id, person_id, label, token_hash (unique), paired_at, last_seen, revoked_at | [ADR 0010](../decisions/0010-device-cookie-auth.md). `revoked_at` null means active |
| **pairing_token** | id, token_hash (unique), kind (`bootstrap`/`pair`), person_id, created_at, expires_at, used_at | [ADR 0010](../decisions/0010-device-cookie-auth.md). Only the hash is stored. A `bootstrap` token has no person and no expiry; a `pair` token has both. `used_at` null means unused |
| **recipe_source** | id, kind (`recipe-repo`, `hugo`, …), location | Where a recipe came from. Unique on (kind, location) |
| **recipe** | id, source_id, slug, title, source_ref, content_hash, body, archived | Mirrored from the recipe repo. Unique on (source_id, slug) |
| **recipe_meta** | recipe_id, meal_type, servings, in_rotation, favorite, effort | Edited in the UI; survives re-indexing ([ADR 0006](../decisions/0006-metadata-in-larder-db.md)). `servings` is positive when set |
| **unit** | id, name (unique), dimension (mass/volume/count/package) | |
| **unit_alias** | id, unit_id, alias (unique) | Spellings that map to a unit (`tbsp`, `tablespoon`) |
| **category** | id, name (unique), sort_order | Seeded order; `item.category` remains text, without a foreign key |
| **item** | id, canonical_name (unique), category | Canonical grocery item |
| **item_alias** | id, item_id, alias (unique) | Learned from review |
| **ingredient_line** | id, recipe_id, position, raw_text, qty, qty_max, unit_id, item_id, notes, optional, is_food, confidence (0–1), confirmed | One per Markdown list item. Unique on (recipe_id, position). A range ("2–3") keeps its lower bound in `qty` and its upper bound in `qty_max`; `qty_max` is null for a plain quantity, needs `qty` and is not below it. For an alternatives line the first choice stays here |
| **ingredient_line_alt** | id, line_id, position, alt_text, item_id | The other choices of an alternatives line ("honey, maple syrup, or agave"), in order. `position` starts at 1 and is unique per line. The choices share the line's `qty` and `unit_id`. `alt_text` is the alternative as written; `item_id` stays null until it is matched to a canonical item |
| **parse_override** | id, normalized_text (unique), qty, qty_max, unit_id, item_id, notes, optional, is_food | One confirmation applies everywhere ([ADR 0007](../decisions/0007-rule-based-ingredient-parsing.md)). `qty_max` works as on `ingredient_line` |
| **parse_override_alt** | id, override_id, position, alt_text, item_id | Alternatives of an override, the same shape as `ingredient_line_alt`. Unique on (override_id, position) |
| **meal_plan** | id, week_start (unique), confirmed_at | One row per Sunday-starting week; null means draft, otherwise UTC ISO text |
| **planned_meal** | id, meal_plan_id, date, recipe_id, status (planned/made/skipped), locked | |
| **shopping_list** | id, created_at, archived_at | At most one row has `archived_at` null: the one active shared list |
| **list_item** | id, list_id, item_id, free_text, qty, unit_id, source (plan/staple/manual/runout), checked, added_by, added_at | `item_id` or `free_text` must be set |
| **staple** | id, item_id (unique), default_qty, unit_id | One-tap adds |

`schema_migrations` (version, name, applied_at) records applied migrations. It
is not part of the domain model.

The canonical unit vocabulary in `larder.ingredients.units` includes
`UNIT_DIMENSIONS` for database seeding: tbsp, tsp, cup, ml and l are volume;
lb, oz, g and kg are mass; clove is a count; can and jar are packages.
This classification does not convert quantities.

### Item seed and matching

After migration, `create_app` calls `larder.ingredients.match.seed_items` before
syncing people. The packaged `data/seed_items.toml` holds category order and a
small generic item/alias catalog drawn from the recipe fixtures and matching
examples. No staples are seeded. Units and their aliases come from the Python
vocabulary above. The seed is one explicit transaction that inserts missing
categories by name, items by canonical name, units by name and aliases by alias.
It never updates or deletes existing rows, including edited category order or
alias targets. Editing TOML changes future insertions; it does not overwrite
existing database values or remove old rows.

`match_item(conn, item_text)` normalizes case, preparation descriptors and simple
plurals. It tries canonical names before stored aliases. Either exact normalized
match scores 1. Otherwise, only canonical names sharing a normalized token are
candidates; `difflib.SequenceMatcher` scores them, with canonical-name/id order
breaking ties. Fuzzy confidence is capped at 0.79 so it always needs review.
Without token overlap there is no candidate and confidence is 0.

`parse_and_match_recipe(conn, recipe_id)` parses that recipe's unconfirmed raw
ingredient lines and replaces all parsed fields and ordered alternatives in one
transaction. It preserves raw text and confirmed parses. The first choice keeps
the best item candidate even at low confidence; each alternative stores its
original text and its own candidate (or null). Line confidence is the minimum of
the parse score and all choice match scores. A line below `REVIEW_THRESHOLD`
(0.8) needs review. This callable requires the unit seed; a missing recognized
unit raises instead of silently dropping the unit. Indexing calls it within
the mirror transaction. Cached overrides take priority over rule-based parsing
and matching; applying one sets confidence to 1 and marks the line confirmed.

### Deletes

| Foreign key | On delete |
|---|---|
| `recipe_meta.recipe_id`, `ingredient_line.recipe_id` | CASCADE |
| `ingredient_line_alt.line_id`, `parse_override_alt.override_id` | CASCADE |
| `unit_alias.unit_id`, `item_alias.item_id`, `staple.item_id` | CASCADE |
| `planned_meal.meal_plan_id`, `list_item.list_id` | CASCADE |
| `list_item.added_by` | SET NULL |
| All others (`device.person_id`, `pairing_token.person_id`, `recipe.source_id`, `planned_meal.recipe_id`, every `unit_id` and `item_id` on a line, line alternative, override, override alternative or list item, and `staple.unit_id`) | RESTRICT |

### People

Config is the source of truth for people: `config.Person` holds the role,
autonomy level and contact details (email, Matrix id, notify channel). The
`person` table is a thin mirror (id, name, active) so other tables can
reference a person. At every start larder syncs it from config: new names are
inserted, names in config are set active, and names no longer in config are set
inactive. Rows are never deleted. An inactive person's devices stop working and
their pairing links stop being valid.

### Deviations from the earlier draft

- `unit_alias` replaces `Unit.synonyms`, so each alias is unique across units.
- `device.revoked` became `revoked_at`, a timestamp.
- `person` keeps only id, name and active. Role, autonomy and contacts stay in
  config.
- `staple` gains `unit_id`, matching `default_qty`.
- Ranges and alternatives in ingredient lines ("1–2 onions", "butter or oil")
  are added by migration 0003 (slice S59): `qty_max` on `ingredient_line` and
  `parse_override`, plus the child tables `ingredient_line_alt` and
  `parse_override_alt`. S16 stays pure; S17 writes parsed recipe lines and their
  alternatives. Override application belongs to S18. How list generation treats
  a range or an alternative is decided separately.

## Phase 2

| Entity | Key fields | Notes |
|---|---|---|
| **RawEmail** | id, message_id, received_at, store, path | Archive for re-parsing |
| **Order** | id, store, ordered_at, raw_email_id | |
| **OrderLine** | order_id, raw_name, qty, size, item_id, confidence | Matched with review |
| **PantryEvent** | item_id, kind (purchase/consumed/used/tossed/have), qty, at, person_id | Event-sourced input |
| **PantryEstimate** | item_id, est_qty, est_expiry, confidence | Derived from events |
| **ShelfLife** | category or item_id, days | Defaults by category |
| **NotificationLog** | kind, person_id, period, sent_at | Dedupes pings |

## Relationships

```
Person 1─* Device
RecipeSource 1─* Recipe 1─1 RecipeMeta
Recipe 1─* IngredientLine *─1 Item 1─* ItemAlias
IngredientLine *─1 Unit 1─* UnitAlias
MealPlan 1─* PlannedMeal *─1 Recipe
ShoppingList 1─* ListItem *─1 Item
ListItem *─1 Person (added_by)
Staple 1─1 Item
OrderLine *─1 Item ; PantryEvent *─1 Item ; PantryEstimate 1─1 Item
```

Migration 0005 adds `parse_override.optional` so cached review edits preserve the
optional flag. Override keys casefold the raw line and collapse whitespace only.
Review answers copy the ordered alternatives verbatim, including null item ids.

Review answers immediately update identical active, unconfirmed ingredient lines
in the same transaction as the override and alias; indexing applies the cache
to future occurrences. Archived and confirmed lines are preserved.
