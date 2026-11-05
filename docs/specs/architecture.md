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
