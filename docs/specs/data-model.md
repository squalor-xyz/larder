# Data model

The MVP tables below are the schema in
[`0001_init.sql`](../../src/larder/migrations/0001_init.sql). Phase 2 tables
are indicative only; each is added by its own slice as a new migration.

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
| **recipe_source** | id, kind (`recipe-repo`, `hugo`, …), location | Where a recipe came from. Unique on (kind, location) |
| **recipe** | id, source_id, slug, title, source_ref, content_hash, body, archived | Mirrored from the recipe repo. Unique on (source_id, slug) |
| **recipe_meta** | recipe_id, meal_type, servings, in_rotation, favorite, effort | Edited in the UI; survives re-indexing ([ADR 0006](../decisions/0006-metadata-in-larder-db.md)). `servings` is positive when set |
| **unit** | id, name (unique), dimension (mass/volume/count/package) | |
| **unit_alias** | id, unit_id, alias (unique) | Spellings that map to a unit (`tbsp`, `tablespoon`) |
| **item** | id, canonical_name (unique), category | Canonical grocery item |
| **item_alias** | id, item_id, alias (unique) | Learned from review |
| **ingredient_line** | id, recipe_id, position, raw_text, qty, unit_id, item_id, notes, optional, is_food, confidence (0–1), confirmed | One per Markdown list item. Unique on (recipe_id, position) |
| **parse_override** | id, normalized_text (unique), qty, unit_id, item_id, notes, is_food | One confirmation applies everywhere ([ADR 0007](../decisions/0007-rule-based-ingredient-parsing.md)) |
| **meal_plan** | id, week_start (unique) | One row per week |
| **planned_meal** | id, meal_plan_id, date, recipe_id, status (planned/made/skipped), locked | |
| **shopping_list** | id, created_at, archived_at | At most one row has `archived_at` null: the one active shared list |
| **list_item** | id, list_id, item_id, free_text, qty, unit_id, source (plan/staple/manual/runout), checked, added_by, added_at | `item_id` or `free_text` must be set |
| **staple** | id, item_id (unique), default_qty, unit_id | One-tap adds |

`schema_migrations` (version, name, applied_at) records applied migrations. It
is not part of the domain model.

### Deletes

| Foreign key | On delete |
|---|---|
| `recipe_meta.recipe_id`, `ingredient_line.recipe_id` | CASCADE |
| `unit_alias.unit_id`, `item_alias.item_id`, `staple.item_id` | CASCADE |
| `planned_meal.meal_plan_id`, `list_item.list_id` | CASCADE |
| `list_item.added_by` | SET NULL |
| All others (`device.person_id`, `recipe.source_id`, `planned_meal.recipe_id`, every `unit_id` and `item_id` on a line, override or list item, and `staple.unit_id`) | RESTRICT |

### People

Config is the source of truth for people: `config.Person` holds the role,
autonomy level and contact details (email, Matrix id, notify channel). The
`person` table is a thin mirror (id, name, active) so other tables can
reference a person. Syncing it from config is a later slice; migration 0001
only creates the table.

### Deviations from the earlier draft

- `unit_alias` replaces `Unit.synonyms`, so each alias is unique across units.
- `device.revoked` became `revoked_at`, a timestamp.
- `person` keeps only id, name and active. Role, autonomy and contacts stay in
  config.
- `staple` gains `unit_id`, matching `default_qty`.
- Ranges and alternatives in ingredient lines ("1–2 onions", "butter or oil")
  are left to the parser slice, which adds columns in a later migration.

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
