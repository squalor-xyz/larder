# 0009: Recipe files are Hugo-style TOML Markdown

**Status:** Accepted (2026-10-11)

## Context

The recipe repo ([ADR 0005](0005-recipes-own-repo-plus-importers.md)) needs a
file format. Options: Hugo-style Markdown with TOML front matter, Cooklang, or
Markdown with structured ingredient data in the front matter.

## Decision

One Markdown file per recipe with `+++` TOML front matter and `## Ingredients`
/ `## Directions` sections.

## Consequences

- Lossless import from Hugo sites; renders on common git hosts.
- Parsed with stdlib `tomllib`. Writing TOML needs a small writer (stdlib has none).
- Publishing to a Hugo site later stays simple.
- Heading variants are normalized on import; the exact rules are set by the
  recipe-repo-conventions interview.
