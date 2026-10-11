# 0012: Recipe repo on a git host; larder pushes with a scoped key

**Status:** Accepted (2026-10-11)

## Context

The recipe repo ([ADR 0005](0005-recipes-own-repo-plus-importers.md)) needs a
host, and larder must commit imports and edits.

## Decision

Host the recipe repo on a git host (e.g., GitHub). larder commits and pushes
using a deploy key scoped to that repo only.

## Consequences

- Least privilege: the key can't touch any other repo or account.
- The key is a secret on the data volume; never committed or logged.
- larder handles a remote that has diverged (pull/rebase before push; conflicts
  are surfaced in the admin view).
- Automated commits use a dedicated larder identity.
