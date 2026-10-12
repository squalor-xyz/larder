# Slice relay roles, models and efforts

The owner's preferred defaults for running a slice as a slice relay
(`slicer ai relay`). Read each slice's size and effort with
`slicer show ID --json --lean`. The owner can override any role per run.

**Status:** Accepted (2026-10-12). The agents repo's
`multi-agent-workflows/relay.conf` matches: its default lines are the
sonnet implementer, opus reviewer and opus architect, and a comment states
the haiku rule, since that file has no conditions.

## Matrix

| Role | When | Vendor | Model | Effort |
|---|---|---|---|---|
| Implementer | Well specified, and effort 1 with size S or M | Claude | haiku | xhigh |
| Implementer | Well specified, and effort 2 with size S | Claude | haiku | xhigh |
| Implementer | Anything else | Claude | sonnet | high |
| Reviewer and gate | Every slice | Claude | opus | medium |
| Architect | Only on a size L slice | Claude | opus | high |

## Rules

- A reviewer never wrote code in the slice it reviews.
- No cross-vendor reviewer unless the owner asks.
- Claude roles run as Claude Code subagents.
- "Well specified" (proposed definition, not yet confirmed by the owner):
  the slice names its files, failing tests, implementation steps and checks,
  and needs no open owner decision.

## Source

- Implementer rule: owner, 2026-10-12.
- Reviewer: `relay.conf` (`relay-reviewer claude opus medium`).
- Architect: owner, 2026-10-12 (Claude opus, high; the grok line was removed
  from `relay.conf`).
- Architect only on size L: the owner's global agent guidance.
