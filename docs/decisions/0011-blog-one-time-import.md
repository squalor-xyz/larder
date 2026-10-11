# 0011: Hugo site import is one-time

**Status:** Accepted (2026-10-11)

## Context

Recipes from an existing Hugo site could be synced continuously or imported once.

## Decision

Import once. After that, the recipe repo is the source of truth. Publishing
from the recipe repo to a Hugo site is a Later option (R7).

## Consequences

- The importer needs no change tracking or conflict handling.
- New recipes are written in the recipe repo.
- Because the formats match ([ADR 0009](0009-recipe-repo-format.md)),
  publishing back can be a copy or a Hugo module.
