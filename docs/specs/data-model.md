# Data model (draft)

These are the entities needed for the requirements, not a schema. Field lists
are indicative only.

## MVP

| Entity | Key fields | Notes |
|---|---|---|
| **Person** | id, name, role (admin/member), notify_channel | Household members |
| **Device** | id, person_id, token_hash, label, paired_at, last_seen, revoked | [ADR 0010](../decisions/0010-device-cookie-auth.md) |
| **RecipeSource** | id, kind (`recipe-repo`, `hugo`, …), location | Where a recipe came from |
| **Recipe** | id, slug, title, source_id, source_ref, content_hash, body, archived | Mirrored from the recipe repo |
| **RecipeMeta** | recipe_id, meal_type, servings, in_rotation, favorite, effort | Edited in the UI; survives re-indexing ([ADR 0006](../decisions/0006-metadata-in-larder-db.md)) |
| **IngredientLine** | id, recipe_id, position, raw_text, qty, unit_id, item_id, notes, optional, is_food, confidence, confirmed | One per Markdown list item |
| **ParseOverride** | normalized raw text → qty, unit, item, notes, is_food | One confirmation applies everywhere ([ADR 0007](../decisions/0007-rule-based-ingredient-parsing.md)) |
| **Item** | id, canonical_name, category | Canonical grocery item |
| **ItemAlias** | item_id, alias | Learned from review |
| **Unit** | id, name, synonyms, dimension (mass/volume/count/package) | |
| **MealPlan / PlannedMeal** | week, date, recipe_id, status (planned/made/skipped), locked | |
| **ShoppingList / ListItem** | item_id or free text, qty, unit, source (plan/staple/manual/runout), checked, added_by | One active shared list |
| **Staple** | item_id, default_qty | One-tap adds |

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
IngredientLine *─1 Unit
PlannedMeal *─1 Recipe
ListItem *─1 Item
OrderLine *─1 Item ; PantryEvent *─1 Item ; PantryEstimate 1─1 Item
```
