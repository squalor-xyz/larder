# 0010: Device pairing with a long-lived cookie

**Status:** Accepted (2026-10-11). Flow details are settled in the pairing interview slice.

## Context

larder is LAN/VPN-only, and everyday users may be non-technical. Options: no
auth on the LAN, a shared password, or pairing each device once.

## Decision

An admin pairs each device once (e.g., QR code or one-time link). The device
receives a long-lived, revocable cookie tied to a Person. There's no login
screen after pairing.

## Consequences

- Everyday users never see a login; list edits are attributed per person.
- Guests on the network can't use larder without being paired.
- Needs a device table (token hash, person, label, last seen, revoked) and admin revocation.
- The cookie is `HttpOnly` and `SameSite`; `Secure` when served over HTTPS.
