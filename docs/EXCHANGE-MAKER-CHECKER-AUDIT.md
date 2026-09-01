# Multi-directional Exchange and Maker-Checker Audit

Status: **SECOND INCREMENT IMPLEMENTED; INTERNAL MANUAL VALIDATION REQUIRED**

## Outcome and capability contract

| ID | Capability | Named user | User outcome | Acceptance |
|---|---|---|---|---|
| CAP-X1 | Typed exchange envelope | Department agent | Ask, answer, hand off, and return evidence without copying raw data into runtime state | direction, participant, classification, artifact hash, thread, and idempotency tests pass |
| CAP-X2 | Maker-checker separation | Human operator | Know that an independent role checked internal work before downstream use | maker cannot check; downstream stays blocked until checker decision |
| CAP-X3 | Bounded correction loop | Maker and checker | Return deficient work with attributable feedback without an endless review loop | every submission and feedback is retained; configured review cap stops the run |

Arbitrary agent-to-agent broadcast, live customer data, a provider-specific adapter, and
automatic outward execution remain excluded. The later service foundation adds signed identity
binding and the connector authorization/reconciliation control plane without changing this
legacy file-harness boundary.

## Coverage and RCA

| Requirement | Before this increment | Root cause | Severity | Disposition |
|---|---|---|---|---|
| EX-01 Two-way agent exchange | Missing: runtime stored only step state | no message contract or participant authorization | Critical | typed envelopes implemented within a step and across adjacent DAG legs |
| EX-02 Data lineage | Partial: completion stored one evidence hash | no per-message payload reference, reply link, or submission history | High | payload hashes, reply direction, and submission revisions implemented |
| MC-01 Independent checker | Missing: owner completed its own step | workflow schema had one role and human approval was incorrectly carrying both authority and quality | Critical | checker must exist and differ from maker |
| MC-02 Checker decision | Missing | no `awaiting_check` state or checker-only transition | Critical | approve, return, and reject transitions implemented |
| MC-03 Corrective return | Missing | failure retry was not quality rework and carried no checker feedback | High | feedback-linked review counter and return state implemented |
| SEC-X1 Data boundary | Partial | raw payloads could be copied into task/event state by convention | High | runtime stores repository path + hash only; restricted data class rejected |
| SEC-X2 Artifact immutability | Partial | maker evidence was hashed only at completion | High | checker recomputes hash and rejects post-handoff changes |
| SEC-X3 Authenticated agent identity | Missing in the legacy CLI harness | CLI actor is a role claim, not a signed provider identity | High | implemented at the service/API boundary in migration 002 and `runtime/gateway_auth.py`; legacy harness remains local/manual |
| INT-X1 External bidirectional connectors | Provider adapter missing | no selected provider payload/OAuth contract | Critical | authorization, kill-switch, receipt and reconciliation control plane implemented in migration 003; provider exercise remains blocked |

## Workflow and boundaries

1. Makers, checkers, and directly adjacent workflow legs exchange typed questions, answers, and
   handoffs using repository evidence references. Arbitrary broadcast is denied.
2. Maker submits a hashed artifact. The step becomes `awaiting_check`; dependencies remain blocked.
3. Checker sends an attributable decision or feedback message.
4. Approval completes the internal quality gate. Return reopens the maker step within the review
   budget. Rejection or review exhaustion stops the run.
5. Checker steps are green preparation only. A separate later yellow action still waits for human
   approval. Checker approval never authorizes a send,
   publish, spend, signature, access change, or other human-held decision.

Confidential message metadata may reference an authorized internal artifact by path and hash.
Restricted content, credentials, PII, payment/health data, or raw payload text must not enter the
event stream. This is an exchange contract, not a data lake.

## Traceability

| Outcome | Requirement | Component | Test | Status |
|---|---|---|---|---|
| Two-way attributable exchange | EX-01, EX-02 | `send-message`, hash-chained run state | `tests/module-maker-checker.sh` | Implemented |
| Independent quality gate | MC-01, MC-02 | workflow `checker`, `awaiting_check`, checker commands | same | Implemented |
| Controlled correction | MC-03, SEC-X2 | submission revisions, feedback link, review cap, hash recheck | same | Implemented |
| Live external exchange | INT-X1 | provider/tool adapters | not run | Deferred |

## Stop and next gate

Stop on an unknown participant, wrong direction, invalid classification, changed artifact,
unclassified action, rejected check, or exhausted review/cycle/retry budget. Human approval rules
from `CLAUDE.md` remain unchanged.

## Residual gaps, deliberately not added to this increment

| Gap | Why it remains | Required before |
|---|---|---|
| Legacy CLI actor authentication | file-harness role names remain local claims; service users use signed DB bindings | exposing the legacy CLI to a network or sensitive system |
| Provider-specific connector adapter | authorization/egress policy exists, but no provider and payload were selected | CRM, email, billing, support, or API write-back |
| Payload-specific schema validation | no real business payload type has been selected or manually proven | first connector-specific workflow |
| Delivery acknowledgement and timeout | shared event state proves recording, not that a provider consumed a message | unattended or scheduled execution |
| Governed-record deletion policy | transient purge is implemented; durable records/backups await legal hold and retention decisions | confidential production metadata |

These are not reasons to add more agents. They are admission criteria for the next connector
increment and must be tested against one named workflow before generalization.

The internal gold workflow passed maker-checker exchange and one return cycle, then stopped at the
yellow publish boundary as designed. Next decision: approve discovery for one read-only connector
adapter tied to a named workflow; do not generalize into a connector platform.
