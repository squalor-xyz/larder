# 0027: Rule-based message interpretation, optional local LLM fallback

**Status:** Accepted (2026-10-12). The LLM fallback is a Later option.

## Context

Messages arriving by email or Matrix ([ADR 0025](0025-conversational-channels.md))
are short and about a narrow domain: list items, what's low, answers to
larder's questions. Free-form phrasing ("we're nearly out of taco stuff") is
harder to handle with rules. Local AI is preferred and never required
([ADR 0001](0001-self-hosted-floss.md)).

## Decision

- **Rules first (stdlib):** a small command grammar (e.g., `add milk, eggs`,
  `low: rice`, `list`, `what's low`) and item matching through the canonical
  items and aliases (I6).
- **Numbered replies:** larder's own questions offer numbered answers (e.g.,
  "Low on rice? 1 yes / 2 no / 3 out"). Each question has an ID, so a reply
  maps to it without guessing.
- **Unclear messages** get a short help reply listing the commands.
- **Optional LLM fallback (Later):** if the rules can't parse a message and a
  local model (e.g., Ollama) is configured, it may only propose intents from
  the same set. larder repeats the proposed action and waits for a yes before
  acting. The autonomy level check ([ADR 0026](0026-autonomy-levels.md)) still
  applies in code.

## Consequences

- Deterministic, testable and works offline, like the ingredient parser
  ([ADR 0007](0007-rule-based-ingredient-parsing.md)).
- Household members learn a few commands or answer by number.
- An LLM can make the conversation friendlier without being able to do
  anything the rules couldn't.
