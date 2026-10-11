# 0008: Flask as the web framework

**Status:** Accepted (2026-10-11)

## Context

[ADR 0002](0002-python.md) allows one web framework dependency. Candidates:
Bottle (single file, MIT), Flask (BSD-3), Starlette (BSD-3, async, needs an
ASGI server).

## Decision

Flask with server-rendered Jinja templates.

## Consequences

- Widely documented, FLOSS.
- Brings transitive dependencies (Werkzeug, Jinja2 and others). Pin them, and
  review the tree on first install.
- A production WSGI server is a separate dependency, chosen when the container is built.
