# 0015: The app is TLS-agnostic

**Status:** Accepted (2026-10-11). Supersedes [ADR 0014](0014-https-tailscale-cert-local-dns.md) as architecture.

## Context

The research ([https-lan](../research/https-lan.md)) found trade-offs in every
HTTPS option, and each household's network differs ([ADR 0016](0016-future-multi-household.md)).

## Decision

larder serves plain HTTP behind a reverse proxy that handles TLS. Forwarded
headers are trusted only from a configured proxy. Each deployment chooses its
own certificate approach; the project documents the options.

## Consequences

- No certificate code in the app; works with Caddy, Traefik, nginx, Tailscale serve, etc.
- Cookie `Secure` and PWA install depend on the proxy serving HTTPS.
- Proxy-header trust must be explicit configuration.
