# Gold runs — versioned proof that the runtime really ran

`runtime/runs/` is gitignored (working state, rewritten constantly). That means the only
evidence the company has ever executed a real multi-agent run would otherwise live outside
version control. This directory holds a curated, frozen copy of one complete run so the
claim survives a fresh clone.

## `gold-auto-02` — autonomous run with agent-to-agent handoffs

Four steps, four departments, driven end to end by `runtime/executor.py` against the real
`claude` CLI. No human typed a command between the first step and the yellow gate.

| File | What it is |
|---|---|
| `gold-auto-02.workflow.json` | the plan, as validated at run creation |
| `gold-auto-02.jsonl` | the append-only, hash-chained event log for the whole run |
| `gold-auto-02.<step>.evidence` | each agent's actual output, hashed into the log |

What it demonstrates:

- **Autonomous execution** — `frame-goal → produce-output → validate-output` advanced with
  no human input (AGENT-05, LOOP-02).
- **Real handoffs** — each step received the previous step's evidence, re-hashed before it
  was trusted; the COO's output explicitly builds on the CTO's (AGENT-09, MEM-05).
- **The gate holding** — `release-output` is a 🟡 action, so the run stopped at
  `awaiting_approval` and waited for a person (HITL-01).

To verify the chain from a clone:

```bash
cp examples/gold-runs/gold-auto-02/* runtime/runs/
python -m runtime.company_runtime status --run-id gold-auto-02
```

Tracker: PROD-05, TEST-06, VCS-02. Audit: `docs/AUTONOMY-AUDIT-2026-09-01-REV2.md`.
