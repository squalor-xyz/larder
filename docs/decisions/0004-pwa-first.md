# 0004: PWA first, native Android later

**Status:** Accepted (2026-10-11)

## Context

Everyday users, including non-technical ones, need a home-screen icon that
opens straight to the useful screen. Android is the primary mobile target.

## Decision

Server-rendered HTML with a PWA manifest, installable to the home screen. The
default view stays minimal: tonight's dinner, the list, an add-item box. A
native Android app is a Later option, considered only if the PWA falls short.

## Consequences

- One codebase, no app store, works on any modern phone.
- Offline support is limited to what a service worker caches.
- Push notifications go through external channels (ntfy/email), not the PWA.
- Installing needs HTTPS, provided by the deployment ([ADR 0015](0015-app-tls-agnostic.md)).
