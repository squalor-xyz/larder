# 0014: HTTPS via a Tailscale cert plus a local DNS override

**Status:** Superseded as architecture by [ADR 0015](0015-app-tls-agnostic.md) (2026-10-11).
Kept as a documented deployment option. See [research](../research/https-lan.md).

## Context

PWA install and secure cookies need HTTPS. Some household devices use
Tailscale; others aren't on the tailnet.

## Option

Serve larder behind a proxy using the Tailscale-issued certificate for its
`*.ts.net` name. On the home network, a local DNS override maps that name to
the host's LAN IP, so devices that aren't on the tailnet use the same URL and
certificate.

## Trade-offs

- One URL and a publicly trusted cert, with no local CA to install.
- Depends on Tailscale's hosted control plane; Headscale support is unverified.
- Machine and tailnet names appear in Certificate Transparency logs.
- Renewal must be automated (e.g., Caddy with a local tailscaled).
