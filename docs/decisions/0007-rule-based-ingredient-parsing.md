# 0007: Rule-based ingredient parsing with review

**Status:** Accepted (2026-10-11)

## Context

Ingredient lines are free text. Typical cases:

- `1 can (15 oz) corn, no salt added`: package with size, plus notes
- `1 tbsp black pepper` / `1 tablespoon peanut butter`: unit synonyms
- `1 teaspoon honey, maple syrup, or agave`: alternatives
- `2-3 heads of broccoli, cut into florets`: a range
- `1 cup sliced fresh mushrooms (optional)`: optional
- `1 small mason jar`: not food

Options considered: a rule-based parser with review, LLM-assisted parsing, or
rewriting the sources in a strict format.

## Decision

A stdlib rule-based parser produces quantity, unit, item and notes plus a
confidence score. Low-confidence lines and unknown items go into a review
queue. Each confirmation is cached by normalized line text, so a line is
reviewed once. LLM suggestions are a Later option.

## Consequences

- Deterministic, testable, and works offline.
- Some initial review work, which drops toward zero over time.
