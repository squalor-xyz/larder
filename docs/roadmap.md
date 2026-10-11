# Roadmap

This page is the phase overview. Work items are tracked in slicer: run
`slicer list` / `slicer next`, or read the rendered
[ROADMAP](../.slicer/render/ROADMAP.md).

Slices titled **Interview: …** collect the owner's decisions, and the slices
that depend on them can't start until they're done. Every slice also has an
**Owner input** section listing what to confirm before starting.

## Phase 0: Foundations

Conventions, project skeleton, config, database, web shell, device pairing, PWA shell.

## Phase 1: MVP (recipes, planner, shopping list)

1. Recipe repo conventions, file parser, repo sync, Hugo importer, index, metadata UI
2. Ingredient parsing, item matching, review queue
3. Planning rules, suggestion ranking, weekly session, Today view
4. Shopping list behavior, generation, shared list UI
5. Deployment interview, HTTPS spike, container, deploy and onboard, two-week trial

**Exit:** for two straight weeks the household picks dinners and shops from
larder's list, and a non-technical member uses the home-screen app without help.

## Phase 2: Orders, pantry, notifications

Receipts mailbox, IMAP poller, store parsers, order matching, pantry estimates,
use-it-up nudges, runout prediction, pantry-aware planning, notifications.

**Exit:** lists skip items already on hand, and recurring staples are flagged
before they run out.

## Later

Local LLM suggestions · more importers · publish recipes to a Hugo site · store
product names · kitchen tablet mode · native Android app · distributable
self-host packaging · hosted multi-household instance.
