# Controlled Runtime Audit and First Increment

Status: **FIRST INCREMENT VALIDATED; SECOND INTERNAL-EXCHANGE INCREMENT IMPLEMENTED**

## Outcome and scope

The Chief of Staff needs to carry one cross-functional assignment across sessions without losing
sequence, ownership, evidence, retries, or human decisions. This increment adds three capabilities:

| Capability | Named user | Outcome | Acceptance |
|---|---|---|---|
| Replayable workflow state | Chief of Staff | Resume the same goal and see authoritative step state | append-only run reloads after process exit |
| Bounded execution loop | Department agent | Execute only a ready, owned step and stop after limits | DAG, revision, evidence, retry, cycle tests pass |
| Policy and approval gate | Human operator | Yellow work waits; red work is never delegated | state transitions reject bypasses |

Excluded now: model/provider calls, automatic agent dispatch, external connector execution,
scheduler, authenticated actor/approver identity, and production deployment. The workflow is manual-first.

## Evidence and RCA

| Gap | Evidence before fix | Root cause | Severity | Disposition |
|---|---|---|---|---|
| No executable orchestrator | `CLAUDE.md` said “dispatch”; no runtime existed | roles and workflows were prose specifications | Critical | deterministic workflow runtime implemented |
| Gates were prompt-enforced | policy text and grep tests only | no state-transition authority outside the model | Critical | policy-classified runtime transitions implemented |
| No replay/restart | task detail lived in chat; new `state/` tracked only flat records | no immutable workflow revision or event stream | High | append-only run snapshots + input revision implemented |
| No retry/stop enforcement | loop documents named caps but code did not enforce them | validation targeted words, not behavior | High | retry and cycle limits executable and tested |
| Completion could become stale | no binding between output and workflow input | missing revision check and evidence contract | High | revision + repository evidence file required |
| Duplicate/concurrent commands could conflict | no idempotency key or state lock | commands had no request identity or serialized authority | High | request IDs + per-run/state locks prevent duplicate or conflicting successors |
| Approval identity not authenticated | audit records accept human-entered text | no identity provider or signed approval channel | High | still **Partial**; record reference only until an approved connector exists |
| Tools not forced through gateway | tools/connectors are external to this repo | no provider/tool adapter exists | Critical | policy gate exists; execution adapter remains blocked until manual gold run passes |

## Harness contract

The model may propose work. The runtime owns the workflow revision, ready-state calculation,
policy class, retry/cycle budget, approval wait, evidence check, terminal state, and event history.
Green steps may enter progress. Yellow steps stop at `awaiting_approval`. Red steps stop at
`blocked_human` and cannot be approved or executed by the runtime.

Every mutation requires a unique request ID. Repeating it is an idempotent replay. A failed step
returns to ready only while its attempt budget remains. Any stale revision, missing evidence,
unclassified action, unknown owner, cyclic dependency, or exceeded budget stops the run.

## Manual gold run

Use `runtime/workflows/manual-gold-run.json`. Execute the four steps in order with
`python3 runtime/company_runtime.py`. The final publish step must visibly stop for approval.
Use fictional/internal evidence only. The run passes when it reaches `completed`, its event stream
replays after a new process starts, and the tests independently verify bypass rejection.

## Human boundaries and next gate

- Human approval remains mandatory for every yellow action.
- Red actions are performed by the human outside this runtime; the runtime only reports the stop.
- Approval references are not authenticated identity proof. Do not connect live outward tools yet.
- Provider-driven dispatch may be considered only after a human reviews a successful gold run.

The controlled manual gold run passed locally. Next, validate
`runtime/workflows/maker-checker-gold-run.json`; do not add model/provider autonomy yet.
