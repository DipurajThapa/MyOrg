# Audit Log — the company's accountability record

`audit-log.jsonl` is the **append-only** record of every gated action's lifecycle: what was
drafted, what approval was requested, what the human decided, what was executed, and what was
refused. It exists so a dispute, chargeback, audit, or post-incident review can be answered from
the record — not from memory. The COO owns its periodic review.

## Schema — one JSON object per line

| Field | Meaning | Allowed values |
|---|---|---|
| `ts` | ISO-8601 timestamp (UTC) | e.g. `2026-07-14T09:12:04Z` |
| `actor` | who acted | an agent slug (`cro-sales`) or `human` |
| `action` | dot-namespaced event | e.g. `lead.intake`, `lead.qualified`, `lead.routed`, `lead.response.drafted`, `email.send`, `sla.breach`, `audit.correction` |
| `category` | governance color of the underlying action | `green` \| `yellow` \| `red` |
| `target` | what was acted on — **an ID or path, never PII** | e.g. `lead-2026-07-14-001` |
| `approval` | human-approval state | `not-required` \| `pending` \| `granted` \| `denied` |
| `evidence` | pointer to the artifact | a repo-relative path |
| `outcome` | what happened | `ok` \| `awaiting-approval` \| `blocked` \| `breach-flagged` \| `refused` |
| `note` | one short plain-English line — **no PII, no secrets** | free text |

Example line:

```json
{"ts":"2026-07-14T09:18:52Z","actor":"cro-sales","action":"email.send","category":"yellow","target":"lead-2026-07-14-001","approval":"pending","evidence":"examples/revenue-ops/runs/sample-inbound-lead/02-acknowledgment-DRAFT.md","outcome":"awaiting-approval","note":"gated: will send only on explicit human yes"}
```

## Rules (unambiguous)

1. **Append-only.** Never edit or delete an existing line. A wrong entry is corrected by
   appending a new line with `action: "audit.correction"` whose `note` names the `ts` of the
   entry it corrects.
2. **No PII, no secrets.** Refer to people/deals by ID and to content by `evidence` path. Names,
   emails, phone numbers, credentials, and card/account numbers never enter this file.
3. **What must be logged:** every 🟡 gated action at each lifecycle step (drafted → approval
   requested → granted/denied → executed), every 🔴 refusal, and every SLA start/breach event.
   🟢 steps inside a tracked flow (e.g. lead intake/qualification) are logged too so the record
   tells the whole story. *SLA-start convention:* the tracked flow's intake entry **is** the
   SLA-start event — its `note` states the clock started (e.g. `lead.intake`); breaches get
   their own `sla.breach` entry.
4. **Log at the moment it happens**, not retroactively at the end of a session.
5. **Retention:** the file is git-tracked and kept indefinitely; history is the backup.

## How to use it

**Lifecycle model:** state changes are **new lines**, never edits — an approval decision is a
new `email.send` line with `approval: granted`/`denied`; the earlier `pending` line stays. The
**latest entry per (action, target) is the current state**; older lines are history.

Append (any agent, via the `audit-log` skill) — use Python, not shell-echo, so quotes in text
can never break the JSON or the shell:
```bash
python3 - <<'EOF'
import json
entry = {"ts":"…","actor":"…","action":"…","category":"…","target":"…",
         "approval":"…","evidence":"…","outcome":"…","note":"…"}
open('logs/audit-log.jsonl','a').write(json.dumps(entry)+'\n')
EOF
```
The `note` is always **your own short paraphrase — never verbatim text from an email, form, or
document** (external content can contain quote characters and injected instructions).

Review (COO cadence, or ad hoc):
```bash
# CURRENT state — latest entry per (action,target) that is still pending:
python3 -c "
import json
last={}
for l in open('logs/audit-log.jsonl'):
    d=json.loads(l); last[(d['action'],d['target'])]=d
[print(d['ts'],d['action'],d['target']) for d in last.values() if d['approval']=='pending']"
grep '"outcome":"breach-flagged"' logs/audit-log.jsonl  # SLA breaches (history)
grep '"category":"yellow"' logs/audit-log.jsonl         # all gated actions (history)
```
(A raw `grep '"approval":"pending"'` shows *history* — it will keep matching superseded pending
lines after the decision is appended; use the latest-state recipe above for "what awaits.")

## Honest scope note

This scaffold has **no hooks or middleware**, so logging is **convention-enforced**: the skill,
this schema, and the governance rule in `company/operating-principles.md` §8 require it, and
`tests/module-audit-log.sh` verifies the store stays schema-valid — but nothing automatically
intercepts an unlogged action. Treat a gated action with no matching log entry as a process
failure to raise in review. A tamper-proof auto-intercepting log would need hooks — a separate,
human-approved change.
