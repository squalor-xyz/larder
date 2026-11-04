# Roadmap review, 2026-11-03

This review covers architecture, functionality and goals after S01–S07.

- **Reviewer:** grok-4.7 at high effort, read-only. Its claims were checked against the repository before any decision was made.
- **Owner decisions:** recorded below.
- **Scope:** slices were filed and respecced. The owner then approved the doc and ADR fixes listed at the end, and they were applied on the same branch.

## Verdict

The project is on the right track. S01–S07 match the ADRs. Tests enforce the schema: foreign keys, strict tables, and one active list. The MVP chain (recipes → parse → plan → list → deploy → two-week trial) produces a usable household app without needing orders or chat.

Top risks:

1. **List trust.** `ingredient_line` has one `qty` and one `item_id` (`src/larder/migrations/0001_init.sql:76-90`). `docs/specs/data-model.md:73-74` defers ranges and alternatives to "the parser slice", but S16 has no migration.
2. **Phase 2 feasibility.** Nobody has checked whether order emails contain item lines (`docs/open-questions.md`). The check (S31) was scheduled after the MVP trial.
3. **Late operational fixes.** `/` has no route, so a successful pairing (`src/larder/web/auth_views.py:241`) ends on a 404. Two bugs in shipped code were parked on S28, at the end of the MVP chain: IPv4-mapped trusted proxies, and the bootstrap link under multiple workers.

## Findings checked against the repository

| Finding | Evidence | Status |
|---|---|---|
| No `/` route; pairing redirects to 404 | `src/larder/web/auth_views.py:241`; no `/` route in `src/larder/web/` | Confirmed |
| Closed secrets map has no room for a second mailbox, SMTP or Matrix | `src/larder/config.py:46-49` | Confirmed |
| Ranges and alternatives have no columns | `0001_init.sql:76-90`, `docs/specs/data-model.md:73-74` | Confirmed |
| Config is "optional", yet larder refuses to start without an admin | `docs/specs/architecture.md:68-69` vs `:134` | Confirmed |
| G7 is in `docs/goals.md:25` but not in `slicer goals` | `slicer goals --json --lean` | Confirmed |
| S53 depends on S37; S31 on S30; S29 on S12 | `slicer show` | Confirmed |
| S28 holds two notes about bugs in shipped code | `slicer show S28` notes 2026-10-12, 2026-10-13 | Confirmed |
| S14 and S20 say "kid favorite"; the schema has one `favorite` flag | `slicer show S20` (`test_kid_favorite_weighting`) | Confirmed |
| Slices cite Q-numbers that no doc defines | `grep` for `Q[0-9]` under `docs/` finds nothing | Confirmed |
| ADRs 0018–0024 are dangling references | Each is written by its interview slice (S09, S15, S23, S26, S31, S36, S41) | **Not a defect:** forward reservations |
| S23 is unblocked | S23 depends on S15 | **Corrected:** blocked by S15 |

The full grok report has more detail: a requirement-to-slice map, an analysis of the critical path, and per-slice notes. The orchestrator kept a copy outside the repository. The parts acted on are summarized here.

## Owner decisions

| # | Question | Decision |
|---|---|---|
| D1 | Ranges and alternatives storage | New schema slice (migration 0003) before S16 |
| D2 | When to collect order-email samples | During the MVP: S31 no longer waits on S30 |
| D3 | Hugo import as a deploy gate | Dropped: S29 no longer depends on S12 |
| D4 | G7 (conversation) | Tracked in slicer goals; Phase 2 order is notifications → email → Matrix |
| D5 | Everyday screens | Today and List stay separate pages; new read-only week view (P1) |
| D6 | S41 scope | Split: notifications only; new conversation-channels interview |
| D7 | Favorite flag | One `favorite` flag (R4, ADR 0006); fix S14 and S20 wording |
| D8 | Offline (NF8) | S08 as written: static shell cached, pages network-first |
| D9 | Proposed new slices | Landing route, proxy and bootstrap fixes, scheduler, conversation secrets: all filed |
| D10 | Git host and conflicts | Any host via config (ADR 0012); new admin recipe-repo status page |
| D11 | Cleanups | S53 drops S37; S34 → later; S08 `start_url` is `/`; S24 drops "staples low" |
| D12 | SMTP secret | Own small slice after S41, so the notifier doesn't wait on the conversation interview |
| D13 | Who persists parsed fields | S17 writes the whole parse; S13 writes raw lines; S16 stays pure |
| D14 | Phase 2 gate | Only S31 (samples) moves early; S32 and S36 still wait on S30 |

Left to existing interviews: who plans the week (S19 question 6). Conversation work stays behind the S30 trial through S41 and the new interview.

## Slices filed and respecced

### New slices

All new slices are size S and fully specced in the S10 shape.

| ID | Title | Depends on | Unblocks | From |
|---|---|---|---|---|
| S59 | Ingredient ranges and alternatives schema (migration 0003: `qty_max`, `ingredient_line_alt`, `parse_override_alt`) | S04 | S16, S17, S24 | D1 |
| S60 | Read-only week view | S21, S14 | — | D5 |
| S61 | Interview: conversation channels (writes ADR 0028) | S30 | S53, S65 | D6 |
| S62 | Paired landing route `/` | S07 | S08, S22 | D9 |
| S63 | Proxy trust and single bootstrap fixes | S07 | S28 | D9 |
| S64 | In-process scheduler | S05 | S11, S32, S42 | D9 |
| S65 | Conversation secret fields (conversation IMAP, Matrix token) | S61, S03 | S54, S55 | D9 |
| S66 | Recipe repo status page (admin) | S11, S07 | — | D10 |
| S67 | SMTP secret field | S41, S03 | S42, S54 | D12 |

### Existing slices changed

- **Respecced (full text):** S08, S11, S12, S14, S16, S20, S22, S24, S26, S28, S29, S31, S41, S53, S54, S55, S56.
- **Edited one section:**
  - S17 Implement step 5 (D13).
  - S23 Owner input, new question 8 on how ranges and alternatives appear on the list.
- **Dependency changes:**

  | Slice | Change |
  |---|---|
  | S08 | +S62 |
  | S11 | +S64 |
  | S16 | +S59 |
  | S17 | +S59 |
  | S22 | +S62 |
  | S24 | +S59 |
  | S28 | +S63 |
  | S29 | −S12 |
  | S31 | now S01 only |
  | S32 | +S30, +S64 |
  | S36 | +S30 |
  | S42 | +S67, +S64 |
  | S53 | now S25 and S61 |
  | S54 | +S65, +S67 |
  | S55 | +S65, +S54 |

- **Status:** S34 → later.
- **Goals:** G7 added to the slicer goals block.
- **Notes:** S28 has a note saying its two bug notes moved to S63.

### Design choices recorded as owner input inside the new slices

Confirm each when its slice starts:

- **S59:** child tables for alternatives. A group-id column is not possible because of the unique constraints on `(recipe_id, position)` and `normalized_text`.
- **S63:** a `python -m larder bootstrap` pre-start step plus `create_app(..., bootstrap=True)`. This needs one sentence of ADR 0010 amended.
- **S64:** one daemon thread plus a `flock` on `$LARDER_DATA/scheduler.lock` (POSIX only).
- **S11:** sync and conflict status in `.git/larder-status.json` inside the clone.
- **S16:** the confidence penalty weights are placeholders, to be tuned against the golden table.

### Still open

- S25's refresh test needs a list version column. Its migration is now 0004 or later, because S59 takes 0003.
- S22 links to the recipe view but has no S14 edge.
- S19 owner input 3 still says "kid favorites", and S09 still says "creating the GitHub repo". Reword them when those interviews run.

## Doc and ADR fixes (applied)

1. `AGENTS.md` and `README.md`: the status now describes what S01–S07 built.
2. `docs/specs/architecture.md:68-69`: say that config may be absent only for keys with defaults. At least one `role = "admin"` person is required (`:134`, ADR 0010).
3. `docs/requirements.md` U2: describe two pages (Today, List) plus the read-only week view (D5).
4. `docs/requirements.md` NF8: "the static shell is cached; pages are network-first; editing needs the server" (D8).
5. `docs/roadmap.md`: add G7 and the conversation order to Phase 2: notifications → email → Matrix (D4).
6. `docs/goals.md`: list G7 after G6. Add a note to G4 that the one-time parse review during import week is accepted upkeep.
7. `docs/specs/data-model.md:73-74`: point at S59 (migration 0003) (D1).
8. `README.md` ADR index: note that 0018–0024 are reserved for the interview slices.
9. ADR 0012: no change needed. S11 now matches it (D10).
10. S30 Check: added a pass/fail line for G2 (list trust) and a baseline for G3 (duplicate purchases).
11. `docs/open-questions.md`: point the conversation-channels row at S61 instead of "Interview: notifications".
12. ADR 0010 bootstrap paragraph: amended to "each run of the bootstrap step replaces it" (S63).
