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
- **Config file:** `$LARDER_DATA/larder.toml`. It is optional; without it, every
  key takes its default.
- **Unknown keys are errors** at every level: the top level, `[secrets]`, each
  `[[people]]` and each `[[stores]]`. So are values of the wrong type. The error
  names the key and where it came from.
- **Environment overrides** apply to scalar keys only: `LARDER_TIMEZONE`,
  `LARDER_RECIPE_REPO_URL`, `LARDER_SECRETS_DEPLOY_KEY`,
  `LARDER_SECRETS_IMAP_PASSWORD_FILE` and `LARDER_SECRETS_NTFY_TOKEN_FILE`. An
  override wins over the file. Any other `LARDER_*` variable is an error.
- **Timezone** is an optional IANA name. Without it, larder uses the host's
  local time.
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

`ensure_data_dirs` creates `recipes/`, `emails/` and `secrets/` if they are
missing. Loading the config creates nothing.

### Secrets

- The config holds file paths only: the deploy key, the IMAP password file and
  the ntfy token file. A relative path resolves against `LARDER_DATA`. The
  defaults are `secrets/deploy_key`, `secrets/imap_password` and
  `secrets/ntfy_token`.
- Loading reads no secret file. Secret paths are redacted from `repr`, and
  larder never logs them.

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
