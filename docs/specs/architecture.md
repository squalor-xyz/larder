# Architecture (draft)

## Overview

One Python service with a SQLite database. It serves server-rendered HTML
installed as a PWA, keeps a clone of the recipe git repo, and in Phase 2 polls
a dedicated mailbox for order receipts. TLS is handled by a reverse proxy in
front of it ([ADR 0015](../decisions/0015-app-tls-agnostic.md)).

```
                 ┌───────────────────── larder (one container) ─────────────────────┐
 recipe git repo ─┤► recipe sync ─┐                                                 │
 import sources ──┤► importers ───┼─► ingredient parser ─► review queue             │
                  │               │            │                                    │
                  │               ▼            ▼                                    │
                  │            SQLite ◄── planner ◄── pantry estimator (P2)         │
                  │               │                        ▲                        │
 dedicated IMAP ──┤► order poller + store parsers (P2) ────┘                        │
                  │               │                                                 │
                  │               ▼                                                 │
                  │        web app (HTML + PWA) ◄── reverse proxy (TLS) ◄── phones  │
                  │               │                                                 │
                  │        notifier (P2) ──► ntfy / email / Matrix                  │
 email / Matrix ──┤► conversation adapters (P2) ─► intent core + autonomy check     │
                  │        Ollama client (optional) ──► local Ollama                │
                  └─────────────────────────────────────────────────────────────────┘
```

## Components

| Component | Responsibility | Phase |
|---|---|---|
| Recipe sync | Clone/pull/commit/push the recipe repo | MVP |
| Importers | Plugin interface: source → recipe files. First plugin: Hugo site | MVP |
| Ingredient parser | Rule-based line parser, unit normalization, confidence | MVP |
| Item matcher | Canonical items, aliases, categories | MVP |
| Review queue | Confirm low-confidence parses and matches once | MVP |
| Planner | Rotation + recency ranking; pantry-aware in P2 | MVP |
| Shopping list | Shared list; plan → aggregated items; staples; category order | MVP |
| Web app | Server-rendered HTML, PWA manifest + service worker | MVP |
| Auth | Device pairing, long-lived revocable cookie | MVP |
| Order poller | IMAP fetch, raw archive, per-store parser plugins | P2 |
| Pantry estimator | Purchases × consumption × shelf life; runout prediction | P2 |
| Notifier | Channel plugins (ntfy, email) and schedules | P2 |
| Intent core | Typed intents shared by all channels; per-person autonomy check ([ADR 0026](../decisions/0026-autonomy-levels.md)) | P2 |
| Conversation adapters | Email and Matrix in/out; known senders only ([ADR 0025](../decisions/0025-conversational-channels.md)) | P2 |
| Message interpreter | Command grammar and numbered replies; optional LLM fallback ([ADR 0027](../decisions/0027-message-interpretation.md)) | P2 |
| Ollama client | Optional suggestions over HTTP | Later |

## Stack

- Python, stdlib-first ([ADR 0002](../decisions/0002-python.md)): `sqlite3`,
  `tomllib`, `imaplib`/`email`, `html.parser`, `urllib`, `smtplib`, and
  `subprocess` for git.
- Flask with Jinja templates ([ADR 0008](../decisions/0008-flask.md)). The WSGI
  server is chosen when the container is built.
- Recipe files: Hugo-style TOML Markdown ([ADR 0009](../decisions/0009-recipe-repo-format.md)).

## Configuration

All household-specific values (people, timezone, recipe repo URL, store sender
rules, category order, notification channels) come from a TOML config file on
the data volume plus `LARDER_*` environment overrides. Nothing
household-specific is in code ([ADR 0016](../decisions/0016-future-multi-household.md)).
A placeholder config is in [`larder.example.toml`](../../larder.example.toml).

- **Data directory:** `LARDER_DATA` is required and must be an existing directory.
- **Config file:** `$LARDER_DATA/larder.toml`. Every key has a default except the
  people list: larder refuses to start unless at least one person has
  `role = "admin"` (see Auth below), so in practice the file is required.
- **Unknown keys are errors** at every level: the top level, `[secrets]`, `[git]`, each
  `[[people]]` and each `[[stores]]`. So are values of the wrong type. The error
  names the key and where it came from.
- **Environment overrides** apply to scalar keys only: `LARDER_TIMEZONE`,
  `LARDER_RECIPE_REPO_URL`, `LARDER_SECRETS_DEPLOY_KEY`,
  `LARDER_SECRETS_IMAP_PASSWORD_FILE` and `LARDER_SECRETS_NTFY_TOKEN_FILE`. An
  override wins over the file. Any other `LARDER_*` variable is an error.
- **Timezone** is an optional IANA name. Without it, larder uses the host's
  local time.
- **Git identity:** optional `[git]` keys `author_name` and `author_email`
  default to `larder` and `larder@localhost`. Both must be nonempty strings.
  They set the author and committer for recipe commits. These keys are
  file-only, with no environment overrides.
- **Trusted proxies:** `trusted_proxies` is an array of IP addresses or CIDR
  networks (`"127.0.0.1"`, `"10.0.0.0/8"`). Entries are parsed strictly, so
  `"10.0.0.1/24"` is an error. larder applies `X-Forwarded-For`,
  `X-Forwarded-Proto` and `X-Forwarded-Host` only when the connecting address is
  in the list. The default is empty, which ignores forwarded headers. The key is
  file-only; there is no `LARDER_*` override.
- **People** have a role (`admin` or `member`) and an autonomy level, `L0` or
  `L1`. The level defaults to `L0` ([ADR 0026](../decisions/0026-autonomy-levels.md)).

### Data directory

| Path | Contents |
|---|---|
| `larder.db` | SQLite database; the schema is numbered migrations in `src/larder/migrations/` |
| `larder.toml` | Config file (optional) |
| `recipes/` | Clone of the recipe repo |
| `emails/` | Raw order emails |
| `secrets/` | Secret files; created with mode 0700 if missing |
| `secrets/session_key` | 32 random bytes that sign the session cookie; created with mode 0600 at first start and reused |
| `scheduler.lock` | Lock file for the in-process scheduler; created when the scheduler starts (see Scheduler below) |
| `recipes.lock` | Exclusive lock serializing recipe git operations across threads and processes |
| `recipes/.git/larder-status.json` | Last sync outcome, timestamp and conflict details; never committed |
| `secrets/deploy_key` | Owner-provided repo-scoped SSH private key, mode 0600 |
| `secrets/known_hosts` | Owner-provided SSH host keys for the recipe git host |

`ensure_data_dirs` creates `recipes/`, `emails/` and `secrets/` if they are
missing. Loading the config creates nothing.

### Secrets

- The config holds file paths only: the deploy key, the IMAP password file and
  the ntfy token file. A relative path resolves against `LARDER_DATA`. The
  defaults are `secrets/deploy_key`, `secrets/imap_password` and
  `secrets/ntfy_token`.
- Loading reads no secret file. Secret paths are redacted from `repr`, and
  larder never logs them.

## Scheduler

Periodic work (recipe pull, order polling, pings) runs on one in-process
scheduler, `larder.scheduler.Scheduler`, with no separate process or compose
service (stdlib first, [ADR 0002](../decisions/0002-python.md)).

- **One daemon thread** runs registered interval jobs one at a time, in
  registration order. A job is a no-argument callable. Its first run is
  registration time plus one interval, unless `register` receives an explicit
  nonnegative `first_run_delay` (zero means immediately due). Each later run is
  one interval after the previous run finished. A job that raises is logged to `larder.scheduler` and
  does not stop the others.
- **One process per data directory** runs jobs. `start()` takes an exclusive
  `flock` on `$LARDER_DATA/scheduler.lock` (POSIX only). A second process on the
  same data directory logs once and runs no jobs. `stop()` joins the thread and
  releases the lock.
- **Wiring:** `create_app` builds the scheduler and stores it in
  `app.extensions["larder.scheduler"]` without starting it, so tests and WSGI
  workers start no thread. `python -m larder` starts it before the dev server and
  stops it afterwards. Starting it under a WSGI server is decided with the
  container (S28).

## Recipe index

After every successful pull and local commit, `repo.py` indexes the clone's
`recipes/<slug>.md` files while holding `recipes.lock`. Push and scheduled sync
also use this pull path. Indexing opens and closes its own database connection.
A database indexing failure logs a generic error, leaves sync successful, and
is retried on the next successful pull or commit. Recipe content is never
included in that error log.

The mirror uses one `recipe_source` with kind `recipe-repo` and location
`recipes`; `source_ref` is the repo-relative `recipes/<slug>.md` path. SHA-256
of the file bytes detects content changes. Unchanged files keep their rows and
ingredient lines. Changed files update the title and Markdown body and replace
the raw ingredient lines (positions start at 1, confidence is 0, unconfirmed).
Within the same transaction, `parse_and_match_recipe` parses and matches each
unconfirmed line, applying cached human answers first. Confirmed lines stay
unchanged. `recipe_meta` and `parse_override` are never replaced by indexing.

A missing file archives its recipe, retaining its metadata. Restoring the same
slug unarchives that row, including when its content hash is unchanged. Renaming
a file creates a new recipe and archives the old slug; metadata stays with the
old slug. A file that cannot be read or parsed, or has no title, is skipped;
its existing row and ingredient lines stay unchanged, and an error log names
only its slug. Other valid files are still indexed. Files with `draft = true`
are excluded; a previously indexed recipe that becomes a draft is archived,
keeping its metadata. All database writes share one transaction.

## Recipe sync

When `recipe_repo_url` is set, `create_app` registers the `recipe-sync` job.
It pulls and pushes any pending local commits as soon as the scheduler thread
starts (within its one-second tick), then every 900 seconds after the previous
run finishes. The pull and conditional push hold one recipe lock. Only the
process holding the scheduler lock runs the job. Without a URL no job is
registered. The factory itself performs no git or network work.

`larder.recipes.repo` exposes settings-first `ensure_clone`, `pull`, `commit`,
`push` and `status`. Git operations hold an exclusive `flock` on
`recipes.lock`. A missing clone is created under `recipes/`, including from an
empty remote. Writes go straight to `main`: callers write files, then call
`commit(settings, paths, message)` with an imperative sentence such as
`Add pad-thai.`. This commits the named paths, pulls with rebase and pushes.
`push` also rebases first. Pulling alone preserves unpushed local commits and
reports `needs_attention` with their count; it never labels them synced. The
scheduled job retries their push, including after an earlier push was rejected.
Sync and commit require the clone to be on `main`; another branch raises a
`RepoError` and persists `needs_attention`, including on repeated attempts.
An empty or whitespace-only commit message raises `ValueError` before staging
any paths, preserving the caller's working files and index.

If a rebase conflicts, sync aborts it, preserves the local commit and clean
working tree, skips the push and persists `needs_attention` with the affected
files under `.git/`. A later process can read the frozen `RepoStatus` through
`status`: state (`not_cloned`, `ok`, `needs_attention`), detail, last successful
sync timestamp (UTC ISO-8601), ahead/behind counts and conflicted files. Counts
compare against the last fetched `origin/main`; `status` does not fetch.
After the owner resolves the conflict, the next successful sync clears it.
Other git failures raise a sanitized `RepoError`; fetch, rebase and push
failures also persist a needs-attention sync status with a sanitized detail
identifying the failed operation.

The owner creates the remote and installs a write-enabled repo deploy key and
the host's SSH key in the paths above. Git uses `subprocess.run` without a
shell. SSH receives only the configured identity through `GIT_SSH_COMMAND`,
with `IdentitiesOnly=yes`, `BatchMode=yes`, `UserKnownHostsFile` and
`StrictHostKeyChecking=yes`; it never prompts. Logs contain command verbs,
excluding URLs, paths, commit messages and raw git diagnostics. The sync
module never reads the deploy key itself. Local bare repos exercise the same
git operations in tests without host credentials or network access.

## Planner

`larder.planner.rank` is pure Python on frozen dataclasses. It performs no SQL,
clock reads or writes. Its inputs are:

- `Candidate(recipe_id, title, favorite=False, effort=None)`: a rotation dinner.
- `History(recipe_id, day)`: one made or planned event, with a Python `date`;
  skipped events are omitted. Future planned events are included.
- `Rules(nights=7, min_repeat_days=10, recency_weight=1, favorite_weight=7,
  effort_weight=1, recency_cap=365)`: programmatic tuning, with no new TOML keys.
- `Seed(value=0)`: deterministic tie-breaking, independent of the global RNG.
- `Pick(day, recipe_id, locked=False, effort=None)`: a dated slot; a null
  recipe id means the household must fill it. Supply effort for existing picks
  outside the candidate pool so archived project meals still count during swaps.

`planner_db.rotation_candidates(conn)` returns rows containing recipe_id,
title, favorite and effort for unarchived, in-rotation recipes with meal_type
null or `dinner`. `planner_db.meal_history(conn)` returns recipe_id and ISO date
for every made or planned event. Both borrow the caller's connection and write
nothing. Convert candidates with `Candidate(**dict(row))` and history with
`History(row["recipe_id"], date.fromisoformat(row["date"]))`.

`suggest_week(week_start, rules, history, *, candidates, seed=Seed())` returns
a tuple of dated picks starting on Sunday. `rules.nights` must be 1–7; the
default returns all seven nights. The planning session (S21) removes the
household's chosen open night, yielding six dinners. This engine does not
persist a plan, roll skipped meals forward or provide planning UI.

Eligibility precedes scoring: a recipe cannot occur twice within the suggested
week; project meals are allowed only on Sunday or Saturday, at most one per
week. A history event less than `min_repeat_days` before or after the slot
normally excludes its recipe. Exactly ten days is eligible with the defaults.
If no remaining meal satisfies the repeat gap for a slot, that gap alone is
relaxed and the least recently made or planned remaining meal comes first.
The weekly uniqueness and project restrictions never relax. An insufficient
pool leaves dated empty slots.

For eligible meals, score is capped age × recency weight + favorite flag ×
favorite weight + effort fit × effort weight. Age is days since the latest
made or planned event, clamped to 0–365 by default; never-made meals receive
the cap, and a future latest plan has age zero. A favorite's default bonus is
equivalent to seven recency days. Effort fit is 1 for a `quick` meal on a
weekday or a `project` meal on a weekend, otherwise 0 (including null or
unknown effort). These weights were delegated for implementation by the owner
on 2026-11-05 and remain reviewable tuning choices. Relaxed repeats sort by
uncapped age before score. Equal ranks use a local RNG seeded by the supplied
seed and slot date; sorting candidates by recipe id before drawing tie values
makes input order irrelevant.

`alternatives(week, night, k, *, candidates, rules, history, seed=Seed())`
returns up to k ranked candidates for an existing slot in the in-progress
week. A night absent from the week raises `ValueError("night is not in week")`.
It excludes every occupied recipe, including the target's current recipe,
counts project meals in the other slots, and offers no swaps for a locked
target or nonpositive k. Strict repeat-gap candidates precede relaxed repeats.
The function leaves every pick and lock unchanged.

## Deployment

- A single container image plus a data volume (`larder.db`, recipe clone, raw
  emails, secrets).
- Any reverse proxy terminates TLS. larder trusts forwarded headers only from
  a configured proxy.
- Backups: SQLite online backup plus a data-volume snapshot. The recipe repo is
  also backed up by its git remote.

## Security and privacy

- **No public exposure:** LAN, plus an optional VPN.
- **Least-privilege inputs:** a dedicated receipts mailbox; a dedicated
  conversation mailbox and Matrix bot account; a repo-scoped deploy key; no
  personal email, payment or store credentials.
- **Untrusted messages:** conversation input is parsed into intents and checked
  against the sender's autonomy level in code.
- **Public vs. private data:** the recipe repo may be public, so only recipe
  content goes there. Plans, lists, orders, pantry data and metadata stay in
  the private DB.
- **Secrets** are read from files or the environment, never committed, never logged.
- **Auth:** device pairing with a long-lived, revocable cookie that is
  `HttpOnly`, `SameSite`, and `Secure` behind HTTPS; a per-session CSRF token
  on every state-changing request; larder refuses to start without an admin in
  config ([ADR 0010](../decisions/0010-device-cookie-auth.md)).

## Parse review

Admins open `/admin/review` from the navigation, which shows the unresolved line
count. The queue contains unconfirmed low-confidence or unmatched food lines
from active recipes. Each row shows raw text, parsed fields and the first item
candidate. Confirm accepts the suggestion with one tap; Edit opens quantity,
range, unit, notes, optional and item controls. An admin can choose an existing
item, create one, or mark a line non-food. A non-empty new-item name takes
precedence over the selected candidate. Unmatched food needs an item before
confirmation. Existing admin and CSRF protections cover every action.

Answers and learned item aliases are saved atomically. The override key is the
raw line casefolded with whitespace collapsed; quantities and punctuation are
retained. An answer immediately applies to every active, unconfirmed line with
the same key, within the answer transaction. Archived and already-confirmed
lines stay unchanged. Indexing reuses the answer across recipes and changed
files, marks it confirmed and gives it confidence 1. Alternatives are copied unchanged, including
unknown item ids; review edits only the first choice and line fields. Non-food
answers persist `is_food=0` for exclusion by future shopping-list generation.
LLM suggestions and shopping-list generation are outside this feature.

## Recipe browsing and metadata

Every paired household member can browse `/recipes`, filter by meal type and
in/out of rotation, and open `/recipes/<id>`. The navigation links to the list;
when Tonight has a planned meal, its View recipe link opens that recipe.
Archived recipes are omitted and their detail and metadata URLs return 404.
Recipe bodies render a small Markdown subset: headings, flat ordered and
unordered lists, bold text and paragraphs. Source HTML is escaped; other
Markdown syntax (including links) is displayed literally.

Admins can edit meal type (dinner/breakfast/lunch/side/dessert), positive integer
servings, in rotation, favorite and effort (quick/normal/project) through
`/admin/recipes/<id>/meta`. Meal type, servings and effort may be unset. These
values stay in `recipe_meta` and survive recipe reindexing. Recipe content is
edited in git, and ingredient review remains on the separate review page.

The admin list shows rotation checkboxes initialized from saved metadata. Save
rotation applies checked and unchecked states only to submitted rows on the
filtered page; recipes outside that page stay unchanged. New recipes retain
the schema default of in rotation. Both metadata saves and bulk rotation saves
use explicit transactions and the existing admin and CSRF guards. Members see
read-only pages without these controls.

## Weekly planning session

Every paired person can review and edit `/plan`. It defaults to the first Sunday
on or after today; `?week=YYYY-MM-DD` selects any Sunday, including the current
week for edits. A first visit to a current or future week saves seven ranked
suggestions (or open slots when the rotation is insufficient); revisiting preserves
saved choices. A week whose Saturday is before today is read-only: it shows stored
meals, or empty slots if none exist, without creating a plan or suggestions.
POST requests for ended weeks are rejected without writes.

Swap chooses the next ranked alternative, excluding recipes already shown for
that night in the form and recipes occupying other active nights. The form carries
the shown ids through successive swaps without a schema change. Locked nights
have no automatic swaps; Lock toggles the lock. Pick can choose any active recipe,
including recipes outside rotation. Skip retains a row with status `skipped`,
which frees that recipe from ranking history. Open night removes that night's row.
Rolling skipped meals forward belongs to the Today view (S22).

Confirm accepts zero, one or several open nights and saves `confirmed_at` as UTC
ISO text. `larder.planner.plan.subscribe(fn)` registers process-local callbacks;
`fire("plan_confirmed", week_start)` calls them synchronously after the save
transaction commits. Callbacks receive the event name and Sunday as a `date`.
Every subsequent save of a confirmed plan fires the same event. Callback errors
propagate after commit; the saved plan remains committed. List generation is a
separate subscriber implemented by the shopping-list slice.

## Shopping list generation

S24 adds `shopping.generate.regenerate(conn, today=...)`. It replaces plan rows
on the active shared list in one immediate transaction, creating the list when
needed. It reads every confirmed week whose Saturday is on or after today;
only `planned` meals contribute. Each occurrence of a recipe contributes its
recipe quantities, without serving scaling. Optional and non-food lines, and
resolved items with a `staple` row, are excluded. Untrusted guesses to staples
keep their raw text and unresolved warning. Manual, staple and runout rows survive.

Ranges use `qty_max`; alternatives use only the parent (first choice), without
joining the alternative rows. Quantities sum per item and compatible dimension.
US customary tsp/tbsp/cup and metric ml/l convert within volume; avoirdupois
oz/lb and metric g/kg convert within mass. The largest contributing unit is
shown. Package units (including cans and jars), other units and unitless lines
stay separate. If any contributing quantity is unknown, the sum stays unknown.
A plan row retains its check only when its summed quantity is unchanged,
comparing in the previous display unit to avoid conversion roundoff.

Unmatched lines and unconfirmed matches below the parser review threshold keep
their raw text as free-text items. Generation returns deduplicated warnings
(`Unresolved ingredient: ...`) to its caller; warnings are not stored. Identical
raw texts with compatible units aggregate. Each generated row links to every
contributing recipe through `list_item_recipe`. `shopping_db.list_items` reads
items in configured category order, with uncategorized items after known
categories and free text last.

Importing `larder.web` registers a single process-local plan subscriber, even
without calling `create_app`. Confirming or
editing a confirmed plan in an app context regenerates the list synchronously
after the plan commit, using that app's connection and host-local date (the
same date convention as the planner view). No app or connection is captured by
the subscriber. Non-web callers invoke `regenerate` directly. As with other
plan subscribers, an error propagates after the plan commit; the list transaction
rolls back. Pantry subtraction, staple buttons and list UI belong to later slices.
