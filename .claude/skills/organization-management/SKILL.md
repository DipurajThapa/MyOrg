---
name: organization-management
description: Track company goals, accountable tasks, and approval-bearing decisions across sessions. Use when the Chief of Staff accepts multi-step work, reports organization status, assigns ownership, records a decision, or closes verified work.
---

# Organization management

Goals, owned tasks and decisions live in one place: the runtime's run log. A **goal** is a
run (`goal` on the run), a **task** is a step with one `owner` and one status, and a
**decision** is either a human gate on a step (approve / reject / cancel, in the audit log),
a connector approval, or a memory decision. There is no second ledger to keep in step.

## Workflow

1. Before planning, read where everything stands with `runtime/health.py` (or `GET /v1/runs`
   from the Control Center). Do not start a run for work already moving.
2. For multi-step or cross-session work, plan it as a workflow with `runtime/planner.py`
   (`"<goal>" <run-id>`), then start the run with `runtime/company_runtime.py`
   (`create-run <workflow.json> <run-id> --actor chief-of-staff --request-id <id>`).
   Every step names one department as `owner`; add a `checker` where downstream decisions
   rely on the maker's own artifact.
3. Let the runtime move steps (`ready → in_progress → completed`, or parked at a gate).
   Done needs hashed evidence; the runtime will not record a step without it.
4. Material choices are decisions a person makes on the Control Center: a gated step, a
   connector action, or a lesson to remember. Each is recorded against a name and a reason.
5. Report Done / Awaiting approval / Blocked from `runtime/company_runtime.py`
   (`status <run-id>`).

Records are append-only. Never edit history. Do not store secrets, credentials, PII, raw
customer data, or long working context in a goal. A run never authorizes an outward or
irreversible action on its own; those still follow `CLAUDE.md` §3 and `audit-log`.

## Red flags

- Work with no named outcome or owner.
- Completion based only on an agent statement.
- A second run for the same goal before the human resolves priority.
