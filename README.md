<img src="assets/larder-512.png" alt="larder logo" width="96" height="96">

# larder

Self-hosted, FLOSS household food system: recipes, weekly dinner planning and a
shared shopping list.

**Status:** planning. There is no application code yet.
**License:** [AGPL-3.0](LICENSE)

## Docs

| Doc | What it covers |
|---|---|
| [Goals](docs/goals.md) | Goals, non-goals, principles |
| [Requirements](docs/requirements.md) | Functional and non-functional requirements, non-requirements |
| [Architecture](docs/specs/architecture.md) | Components, data flow, deployment, security & privacy |
| [Data model](docs/specs/data-model.md) | Draft entities and relationships |
| [Roadmap](docs/roadmap.md) | Phases and exit criteria |
| [Work items (slicer)](.slicer/render/ROADMAP.md) | Tracked slices; use `slicer list` / `slicer next` |
| [Open questions](docs/open-questions.md) | Unresolved items and what each one blocks |
| [Research: HTTPS on the LAN](docs/research/https-lan.md) | Certificate options and Android/PWA findings |

### Decisions (ADRs)

| # | Decision |
|---|---|
| [0001](docs/decisions/0001-self-hosted-floss.md) | Self-hosted, FLOSS, purpose-built |
| [0002](docs/decisions/0002-python.md) | Python, stdlib-first |
| [0003](docs/decisions/0003-dedicated-mailbox.md) | Dedicated least-privilege mailbox for receipts |
| [0004](docs/decisions/0004-pwa-first.md) | PWA first, native Android later |
| [0005](docs/decisions/0005-recipes-own-repo-plus-importers.md) | Recipes in a dedicated git repo, plus importers |
| [0006](docs/decisions/0006-metadata-in-larder-db.md) | Recipe metadata lives in larder's DB |
| [0007](docs/decisions/0007-rule-based-ingredient-parsing.md) | Rule-based ingredient parsing with review |
| [0008](docs/decisions/0008-flask.md) | Flask as the web framework |
| [0009](docs/decisions/0009-recipe-repo-format.md) | Recipe files are Hugo-style TOML Markdown |
| [0010](docs/decisions/0010-device-cookie-auth.md) | Device pairing with a long-lived cookie |
| [0011](docs/decisions/0011-blog-one-time-import.md) | Hugo site import is one-time |
| [0012](docs/decisions/0012-recipe-repo-github-push.md) | Recipe repo on a git host; larder pushes with a scoped key |
| [0013](docs/decisions/0013-weekly-planning.md) | Weekly planning |
| [0014](docs/decisions/0014-https-tailscale-cert-local-dns.md) | HTTPS via Tailscale cert + local DNS override (superseded; deployment option) |
| [0015](docs/decisions/0015-app-tls-agnostic.md) | The app is TLS-agnostic |
| [0016](docs/decisions/0016-future-multi-household.md) | Single household first, portable for others later |
| [0017](docs/decisions/0017-dev-conventions.md) | Development tooling and conventions |
| [0025](docs/decisions/0025-conversational-channels.md) | Conversational channels: email and Matrix |
| [0026](docs/decisions/0026-autonomy-levels.md) | Autonomy levels per person |
| [0027](docs/decisions/0027-message-interpretation.md) | Rule-based message interpretation, optional local LLM fallback |
