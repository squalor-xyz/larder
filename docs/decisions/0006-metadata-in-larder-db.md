# 0006: Recipe metadata lives in larder's DB

**Status:** Accepted (2026-10-11)

## Context

Planning needs metadata that recipe files usually don't carry (meal type,
servings, in rotation, favorite, effort). Options: front matter fields, an
overlay file, or editing in larder's UI.

## Decision

Edit the metadata in larder's UI and store it in the private SQLite DB, keyed
by recipe and preserved across re-indexing.

## Consequences

- Recipe files stay clean, and household preferences stay out of a possibly public repo.
- Metadata relies on data-volume backups, not git.
- Indexing must identify recipes stably (slug + source) so metadata isn't orphaned.
