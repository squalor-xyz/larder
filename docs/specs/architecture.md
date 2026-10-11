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
                  │        notifier (P2) ──► ntfy / email                           │
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

## Deployment

- A single container image plus a data volume (`larder.db`, recipe clone, raw
  emails, secrets).
- Any reverse proxy terminates TLS. larder trusts forwarded headers only from
  a configured proxy.
- Backups: SQLite online backup plus a data-volume snapshot. The recipe repo is
  also backed up by its git remote.

## Security and privacy

- **No public exposure:** LAN, plus an optional VPN.
- **Least-privilege inputs:** a dedicated receipts mailbox; a repo-scoped
  deploy key; no personal email, payment or store credentials.
- **Public vs. private data:** the recipe repo may be public, so only recipe
  content goes there. Plans, lists, orders, pantry data and metadata stay in
  the private DB.
- **Secrets** are read from files or the environment, never committed, never logged.
- **Auth:** device pairing with a long-lived, revocable cookie that is
  `HttpOnly`, `SameSite`, and `Secure` behind HTTPS; CSRF protection on forms
  ([ADR 0010](../decisions/0010-device-cookie-auth.md)).
