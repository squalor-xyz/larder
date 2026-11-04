# 0021: Reference deployment

**Status:** Proposed (2026-11-04, owner interview S26). It becomes Accepted when the HTTPS spike (S27) passes and the host is chosen.

## Context

The app is TLS-agnostic ([ADR 0015](0015-app-tls-agnostic.md)), but each
deployment still needs HTTPS for PWA install and secure cookies
([research](../research/https-lan.md)). The deployment host was not chosen
when this interview ran.

## Decision

| Topic | Choice |
|---|---|
| Host | Not chosen. One of a TrueNAS box, an Intel NUC or a Raspberry Pi |
| Runtime | One container, run with Docker Compose where the host supports it |
| HTTPS candidate to try first | A Tailscale certificate, served by Caddy on the host |
| Reverse proxy | Caddy, run for larder. There is no existing proxy to reuse |
| Backups | The host's or NAS's snapshots of the data volume, daily |

## Open

- **Host.** The choice decides whether Docker Compose is available (a TrueNAS app platform may differ) and the CPU architecture of the image (arm64 on a Raspberry Pi).
- **Local DNS override.** The router or local resolver, and whether it protects against DNS rebinding, were not asked.
- **Phones off the VPN.** Whether household phones use Private DNS or Chrome Secure DNS was not asked.

## Consequences

- S27 tries the Tailscale certificate with Caddy first.
- S28 builds one container image that runs on the chosen host's architecture.
- No owned domain or DNS API token is needed for the first candidate.
- Site specifics (host name, tailnet name, addresses) stay in `local/`, not in the repo.
