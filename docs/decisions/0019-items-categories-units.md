# 0019: Items, categories, staples and units

**Status:** Accepted (2026-11-04, owner interview S15)

## Context

Ingredient parsing (S16), item matching (S17), list generation (S24) and the
later pantry estimates all depend on one category order, a unit policy and a
rule for staples. The shopping list order should make a store trip quick (L4).

## Decision

| Topic | Rule |
|---|---|
| Category order | produce, meat, dairy, bakery, canned, dry goods, spices, frozen, household, pet. The order lives in seed data and can change without code |
| Staples | None are seeded. The household adds staples in the app |
| Unit conversion | larder converts within a dimension only: volume (tsp, tbsp, cup) and weight (oz, lb). It never converts between volume and weight, and it never converts between a count and either |
| Pantry basics | Salt, pepper, oil and other basics never appear on the list automatically, unless the item is marked as a staple and is low |
| Cans | "1 can (15 oz)" counts as a can: the list says "2 cans black beans". The weight in parentheses is kept as a note |

## Consequences

- The parser keeps a parenthetical size as a note and takes the count as the quantity.
- List generation sums the same item across recipes within one dimension. Lines in unconvertible units stay separate lines.
- Seed data holds the category order and the unit table. It starts with no staples.
- How a range or an alternatives line becomes a list quantity is not decided here. It belongs to the shopping-list interview (S23).
