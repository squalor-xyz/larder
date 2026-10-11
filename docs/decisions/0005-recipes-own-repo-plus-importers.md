# 0005: Recipes in a dedicated git repo, plus importers

**Status:** Accepted (2026-10-11)

## Context

Recipes tend to be scattered (bookmarks, personal sites, memory). A versioned,
portable home for recipe content is useful outside larder too.

## Decision

- Recipe content lives in a dedicated git repo (which may be public).
- Importer plugins bring recipes in from other sources. The first is a Hugo
  site importer.
- Import sources are not modified.

## Consequences

- Recipe content is versioned and portable.
- If the repo is public, it must not contain household data ([ADR 0006](0006-metadata-in-larder-db.md)).
- larder needs git write access to the repo ([ADR 0012](0012-recipe-repo-github-push.md)).
