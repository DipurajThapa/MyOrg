---
name: audit-log
description: >
  Append and review entries in the company's append-only audit log (logs/audit-log.jsonl).
  Use whenever a gated (🟡) action moves through its lifecycle — drafted, approval requested,
  granted/denied, executed — and for 🔴 refusals and SLA start/breach events. Also use to
  answer "who approved what, when?" from the record.
---

# Audit Log

Read the company's append-only record, and know how a gated action gets into it.

**You never write to it.** `CLAUDE.md` §3: the entry is a side effect of the gate, never
something an agent chooses to write -- an agent that can decide to log can decide not to, and
`runtime/audit.py` has no `append` command for exactly that reason. Schema: `logs/README.md`.

## What the runtime records, and when

| Moment | `action` example | `category` | `approval` |
|---|---|---|---|
| A tracked flow step completes (intake, qualification, routing) | `lead.intake`, `lead.qualified`, `lead.routed` | `green` | `not-required` |
| An SLA clock starts | the flow's intake entry doubles as SLA-start — its note states the clock started | `green` | `not-required` |
| An outward action is drafted and the human is notified | `lead.response.drafted` | `green` | `not-required` |
| The gated action awaits the human | `email.send` | `yellow` | `pending` |
| The human decides | `email.send` | `yellow` | `granted` / `denied` |
| The approved action is executed | `email.send` | `yellow` | `granted`, `outcome: ok` |
| A 🔴 action is refused and handed back | `payment.transfer` | `red` | `outcome: refused` |
| An SLA deadline passes unmet | `sla.breach` | `green` | `outcome: breach-flagged` |
| A prior entry was wrong | `audit.correction` | — | note names the corrected `ts` |
| A call to an outside system is about to leave | `connector.<action>` | `yellow` | `granted`, `outcome: attempted` |
| …and the provider accepted it | `connector.<action>` | `yellow` | `outcome: executed` |
| …and the provider rejected it | `connector.<action>` | `yellow` | `outcome: failed` |
| …and we never heard back | `connector.<action>` | `yellow` | `outcome: unresolved` |

**Why an outward call gets two lines, not one.** A call to somebody else's system can leave
this host and never be answered. `attempted` is written *before* the bytes go, so that
possibility is on the record even if this process dies mid-flight; the second line settles
it. `unresolved` is a real answer and the runtime never converts it into `failed` — doing so
is what makes a retry charge the customer twice. Those entries are written by
`runtime/live_gateway.py`, not by an agent; find the open ones with
`GET /v1/connectors/in-flight`.

## How an action reaches the log

Put the action through the gate. The runtime classifies it against `runtime/policy.json`,
writes the audit entry itself, and stops anything outward-facing until a named human decides:

```bash
python -m runtime.company_runtime gate <id>   --owner <department> --action <policy action>   --summary "<what the approver needs to know>" --request-id <id>
```

It prints what the gate decided: `awaiting_approval` (yellow -- now in the operator's console,
waiting on a person), `blocked_human` (red -- never automated, hand it back), or `in_progress`
(green -- ordinary work, which is not a gated action and gets no entry).

The human's decision writes its own entry when they approve or reject in the console. You do
not record their answer either.

**State changes are new lines.** An approval decision = a **new** entry for the same action/target
with `approval: granted`/`denied` — the earlier `pending` line is never edited. Latest entry per
(action, target) = current state.

## How to review

```bash
# CURRENT pending (latest state per action+target):
python3 -c "
import json
last={}
for l in open('logs/audit-log.jsonl'):
    d=json.loads(l); last[(d['action'],d['target'])]=d
[print(d['ts'],d['action'],d['target']) for d in last.values() if d['approval']=='pending']"
grep '"outcome":"breach-flagged"' logs/audit-log.jsonl  # SLA breaches (history)
grep '"category":"yellow"' logs/audit-log.jsonl         # all gated actions (history)
grep '"target":"<id>"' logs/audit-log.jsonl             # one item's full lifecycle
```
(Raw `grep '"pending"'` shows history and will keep matching superseded lines — use the
latest-state recipe for "what awaits a decision.")

The COO owns the periodic review (breaches, long-pending approvals, gated actions with no entry).

## Hard rules

- **Append-only** — never edit or delete a line; correct via a new `audit.correction` entry.
- **No PII, no secrets** — IDs and paths only; names/emails/numbers/credentials never enter the log.
- An entry is a **claim**, not proof the action was right — the `evidence` path is the proof.

## Red flags — stop if you catch yourself thinking

- *"I'll log it at the end of the session."* → Log at the moment; end-of-session logging drops entries.
- *"This action is too small to log."* → If it's 🟡 or 🔴, it's logged. No exceptions.
- *"I'll just fix the typo in that old entry."* → Never. Append an `audit.correction`.
- *"The name makes the note clearer."* → No PII. Use the ID; the evidence file holds the detail.

## Verification before claiming done

An append is done when: the line is the **last** line of the file, it **parses as JSON**, all nine
fields are present, and `evidence` points to a file that exists.
