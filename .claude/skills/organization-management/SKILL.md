---
name: organization-management
description: Track company goals, accountable tasks, and approval-bearing decisions across sessions. Use when the Chief of Staff accepts multi-step work, reports organization status, assigns ownership, records a decision, or closes verified work.
---

# Organization management

This is the durable control surface for three jobs: tie work to a named outcome (`goal`), give
each executable unit one owner (`task`), and preserve material choices (`decision`). Run commands
from the repository root with `python3 scripts/org_state.py`.

## Workflow

1. Read `python3 scripts/org_state.py status` before planning; do not duplicate active work.
2. For multi-step or cross-session work, `create goal "..." --outcome "..."`, then activate it after scope is clear.
3. Create only necessary tasks. Each requires `--goal` and an agent slug as `--owner`. Compile
   cross-functional execution into a validated `runtime/company_runtime.py` workflow. Assign an
   independent checker where downstream decisions rely on the maker's internal artifact.
4. Move tasks through `planned → in_progress → blocked/awaiting_approval → done`. Done requires evidence.
5. Record material choices as decisions. Approval requires `--approval` naming human evidence.
6. Run `validate`, then report Done / Awaiting approval / Blocked.

Records are append-only snapshots. Never edit history. Do not store secrets, credentials, PII,
raw customer data, or long working context. State never authorizes an outward or irreversible
action; those still follow `CLAUDE.md` §3 and `audit-log`.

## Red flags

- Work with no named outcome or owner.
- Completion based only on an agent statement.
- A second competing active goal before the human resolves priority.
