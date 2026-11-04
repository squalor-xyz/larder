# 0018: Recipe repo conventions

**Status:** Accepted (2026-11-04, owner interview S09)

## Context

[ADR 0009](0009-recipe-repo-format.md) fixed the file format (Hugo-style
Markdown with `+++` TOML front matter). It left the exact layout, heading
normalization and commit rules to this interview. The parser (S10), the sync
(S11), the Hugo importer (S12) and the index (S13) all depend on them.

## Decision

| Topic | Convention |
|---|---|
| Path | `recipes/<slug>.md` in the recipe repo |
| Slug | The filename without `.md`. Renaming a recipe's title never changes its identity |
| Headings | `## Ingredients`, `## Directions`, optional `## Notes`. `## Steps` is read as `## Directions` |
| Ingredient sub-groups | `### Sauce` and similar headings under `## Ingredients` are allowed |
| Front matter keys | `title`, `date`, `tags`, and an optional `source` (where the recipe came from). Other keys are ignored on read and preserved on write |
| Commit identity | `larder <larder@localhost>`, configurable in `larder.toml` |
| Commit message | One imperative sentence ending with a period, for example `Add pad-thai.` or `Update pad-thai.` |
| Branch | larder commits straight to `main` |
| Drafts | A recipe with `draft = true` is kept out of larder |
| Content license | CC BY-SA 4.0 for the recipe content |

## Consequences

- The parser (S10) reads only `recipes/*.md` and normalizes headings as above.
- The sync (S11) commits to `main` with the identity and message format above.
- The Hugo importer (S12) skips `draft = true` files and writes into `recipes/`.
- The recipe repo needs a license file for CC BY-SA 4.0. larder does not add one.
- Pull-request review of automated changes is not supported.
