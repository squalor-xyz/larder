# 0020: Shopping list behavior

**Status:** Accepted (2026-11-04, owner interview S23)

## Context

List generation (S24) and the shared list UI (S25) need rules for combining
quantities, ordering, checking items off, resetting and keeping two phones in
step. [ADR 0019](0019-items-categories-units.md) already sets the category
order and the unit policy.

## Decision

| Topic | Rule |
|---|---|
| Aggregation | The same item across recipes is summed within one unit dimension. Each list line shows which recipes need it |
| Quantity shown | The recipe quantity ("3 tbsp chili powder"), not a purchase hint |
| Category order | One order for every store, per ADR 0019 |
| Checked items | Struck through and kept until someone taps "Clear" |
| Reset | A manual "Ordered" button, pressed after the order is placed, resets the list. There is no weekly automatic reset |
| Concurrency | Phones poll every few seconds. There is no live push |
| Free-text items | Allowed. They have no canonical item and sort after the categorized items |
| Range ("1-2 onions") | The list shows the high end, so the household does not run short |
| Alternatives ("butter or oil") | The list shows the first choice only |

## Consequences

- S24 sums per item and unit dimension, and keeps the source recipes on each line.
- S25 polls, strikes through checked items, and has "Clear" and "Ordered" controls.
- The high end of a range comes from `qty_max` ([S59](../specs/data-model.md)); the first choice of an alternatives line is the parent line, so the child rows are not read for the list.
- A purchase hint (a jar, not 3 tbsp) is not built. It can be added later without changing this rule's data.
