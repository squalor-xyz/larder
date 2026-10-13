# 0010: Device pairing with a long-lived cookie

**Status:** Accepted (2026-10-11). Flow details amended 2026-10-13 (S06, S07).

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

## Flow details (2026-10-13, S06)

- **Roles** come from config: each person is `admin` or `member`. larder
  refuses to start (`ConfigError`) when config names no admin.
- **Bootstrap:** while no admin has an active device, larder prints a one-time
  bootstrap link to the server log at startup. It is single use and has no
  fixed expiry; each restart replaces it until one is used. Opening it lists
  the admins from config, and the device is paired to the one picked. The link
  only works while no admin has an active device: once one does (for example
  paired by CLI), the link is dead, even if unused.
- **CLI:** `python -m larder pair --person NAME` prints a one-time pairing link
  for any person in config. It follows the pairing-link rules below and is the
  recovery path when every admin device is lost or revoked.
- **Links in the log and CLI** are printed as a path (`/pair/<token>`) with a
  hint to open it on larder's usual address. There is no base URL setting. The
  admin page builds full links from the request host.
- **Pairing:** on the admin devices page, an admin picks a person. larder shows
  a QR code and the same link to copy. The new device scans the code or opens
  the link, and receives its cookie. The QR code is inline SVG rendered on the
  server with `segno` (approved by the owner on 2026-10-13; the license is
  checked when it is pinned), so the page needs no JavaScript and the CSP is
  unchanged.
- **Pairing links** are valid for 10 minutes and for one use, whichever ends
  first.
- **Opening a link only shows a confirm page.** A `GET` of `/pair/<token>`
  never uses the link up, because messaging apps prefetch links to build
  previews. The confirm page asks for an optional device name (and, on the
  bootstrap link, which admin). Submitting it (`POST`, CSRF-protected) uses the
  link up, creates the device and sets the cookie in one transaction, then
  redirects to `/`. An unknown, used or expired link gets the same plain 404
  page, so it does not say which.
- **Tokens** (bootstrap, pairing, device) come from `secrets`. Only SHA-256
  hashes are stored, and a token is looked up by its hash. CSRF tokens are
  compared in constant time.
- **Device cookie** (`larder_device`): a 1-year `Max-Age`, renewed on use, so
  active devices never expire. `last_seen` is updated, and the cookie
  re-issued, at most once an hour. A device unused for a year has to be paired again. An admin can
  revoke a device at any time, and revocation takes effect on its next
  request.
- **Session key and CSRF:** Flask's session cookie carries a per-session CSRF
  token, and every `POST`, `PUT`, `PATCH` and `DELETE` must send it back in a
  hidden `csrf_token` field (400 otherwise). The session is signed with a
  random 32-byte key in `secrets/session_key`, created with mode 0600 on first
  start and reused after (owner decision, 2026-10-13). The session cookie is
  `HttpOnly`, `SameSite=Lax` and `Secure` over HTTPS. It holds nothing but the
  CSRF token; who the user is comes from the device cookie.
- **Admin pages** (devices, recipe metadata, parse review, imports;
  requirement U3) are admin-only. Members get 403, and admin links are hidden
  from them.
- **Unpaired devices** get a plain page with HTTP 401 saying the device isn't
  set up yet and to ask an admin to pair it. It shows no app data. `/healthz`,
  `/pair/...` and static files stay reachable.
