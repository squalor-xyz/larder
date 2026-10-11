# 0002: Python, stdlib-first

**Status:** Accepted (2026-10-11)

## Context

larder needs to: read a mailbox over IMAP, parse messy HTML emails, store data
in SQLite, serve a small web UI, read recipe files from git, optionally call
Ollama over HTTP, and run unattended in a container for years. The project
prefers the stdlib and few dependencies.

| Need | Python | C# / .NET | Go | TypeScript |
|---|---|---|---|---|
| IMAP + email parsing | stdlib (`imaplib`, `email`) | needs MailKit | needs go-imap | needs a library |
| HTML parsing | stdlib `html.parser` (clunky) | needs AngleSharp | `x/net/html` | needs a library |
| SQLite | stdlib `sqlite3` | needs Microsoft.Data.Sqlite | needs a driver | Bun built-in; recent Node `node:sqlite` |
| Web server | needs a framework | in-box ASP.NET Core | stdlib `net/http` | needs a framework |
| HTTP client | stdlib `urllib` | stdlib `HttpClient` | stdlib | built-in `fetch` |
| Deployment | container with runtime | larger container or AOT | single static binary | container with runtime |
| Dependency churn | low if lean | low | lowest | highest |

Recipe front matter is TOML, which stdlib `tomllib` parses.

## Decision

Python, stdlib-first. One web framework dependency ([ADR 0008](0008-flask.md)).
Any other dependency needs approval and a stated reason.

## Consequences

- The most iterative work (receipt and ingredient parsing) uses the strongest stdlib for it.
- Go was the runner-up (single binary, stdlib web server).
- Weaker static typing; use type hints and tests to compensate.
