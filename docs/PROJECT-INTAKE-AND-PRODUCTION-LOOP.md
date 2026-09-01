# Project Intake and Production-Readiness Loop

Status: **PRODUCTION FOUNDATION IMPLEMENTED LOCALLY; RELEASE GATE BLOCKED**  
Decision owner: human sponsor  
Operating owner: Chief of Staff  
Evidence date: 2026-08-06

This document is the single process map for taking a request from an idea to a controlled
MyOrg run. It applies Six Sigma value-stream thinking without inventing timing data: the first
five manual intakes must establish the baseline before any lead-time improvement is claimed.

## 1. Charter and scope

**Purpose.** Give an operator one governed path to frame a project, route work, run an
independent maker-checker review, obtain human approval, and measure the outcome.

**Named users.** Sponsor/decision owner, intake operator, maker, checker, and system operator.

**MVP capabilities (capped at three).**

| ID | Capability | Outcome | Current evidence |
|---|---|---|---|
| CAP-I1 | Governed project intake | work does not start with an unknown outcome, owner, data boundary, or approval path | reusable intake pack + executable structure checks |
| CAP-I2 | Controlled maker-checker work | downstream work cannot use an unchecked artifact | runtime workflow and maker-checker tests |
| CAP-I3 | Flow and release visibility | operator can see queues, rework, approvals, and release blockers in one interface | Control Center prototype + readiness record |

Live external execution, provider/model dispatch, autonomous policy changes, credential
handling, and production deployment remain outside agent authority and require separate gates.

## 2. Intake process and required documents

Copy `templates/project-intake/` into a new project folder. A project receives a stable project
ID only after Stage 1. Do not create agent work before the Stage 3 Ready gate.

| Stage | Owner | Required action and document | Exit evidence | Human gate / stop condition |
|---|---|---|---|---|
| 0. Triage | Chief of Staff | capture request, sponsor, affected user, desired outcome, urgency, and source in `00-intake-brief` | minimum intake fields populated | stop if sponsor, user, outcome, or decision owner is unknown |
| 1. Clarify | Operator + sponsor | complete problem evidence, facts/assumptions, scope/non-goals, data classes, and dependencies in `01-discovery-evidence` | charter and evidence log | sponsor approves the problem and scope; stop on conflicting evidence |
| 2. Map | COO + affected users | record SIPOC, current-state steps, touch/wait observations, waste, failure demand, and journey in `02-value-stream-and-journey` | current and future maps with unknowns labelled | affected user validates the current state; no fabricated baseline |
| 3. Specify | Product + Data + Security | write testable requirements, exchange schema, source-of-truth, permissions, retention, failure modes, and traceability in `03-requirements-data-contract` | approved must-have requirements and data contract | decision owner accepts three-capability scope; stop if rights or authority are unresolved |
| 4. Control | Security + Operations | score risks and record prevention, contingency, approval class, rollback, and support owner in `04-risk-and-controls` | risks owned; red/yellow boundaries explicit | human approves exceptions; red actions never enter automated execution |
| 5. Validate | Checker + decision owner | run tests, UAT, accessibility/security/privacy review, recovery exercise, and release checklist in `05-test-release-readiness` | exact commands, results, evidence, and residual risks | release only if every required item is Passed; Blocked/Not run prevents production-ready wording |
| 6. Learn | Outcome owner | compare post-release outcome with the measured baseline; propose, do not silently apply, process changes | outcome record and approved lesson | stop/rollback trigger fires on critical defect, control failure, or agreed metric breach |

### Definition of Ready

A project is Ready only when CAP-I1 evidence exists: one outcome and owner, affected users,
scope and non-goals, evidence labels, current/future value stream, no more than three MVP
capabilities, approved must-have requirements, data rights/classification, approval class,
acceptance tests, maker/checker roles, dependencies, and rollback/support owner.

### Definition of Done

Implementation is Done only when linked requirements are implemented, checks are executed,
acceptance evidence is recorded, maker and checker are distinct, documentation and
observability are updated, no secret or temporary bypass remains, and residual risks are
owned. Done is not the same as released.

## 3. Six Sigma value-stream map

### SIPOC boundary

| Suppliers | Inputs | Process | Outputs | Customers |
|---|---|---|---|---|
| sponsor, customer, connected systems, policy owners | request, evidence, data references, constraints, authority | intake → map → specify → make → check → approve → release → measure | checked artifact, decision trail, system change or draft, outcome evidence | sponsor, affected user, operator, auditor |

### Current-state map and root-cause analysis

Timing is **UNKNOWN** until five representative manual intakes are observed. Record touch time,
wait time, queue time, first-pass yield, rework count, handoff count, and approval wait for
every step; never backfill estimates as facts.

| Current step | Value class | Observed gap | Root cause | Effect | Countermeasure |
|---|---|---|---|---|---|
| free-form request arrives | business value | outcome, owner, and data class often implicit | no canonical intake contract | clarification and risk arrive late | one minimum intake brief with required fields |
| Chief of Staff routes request | business value | routing evidence is spread across files | no project-level traceability row | operator reconstructs context | stable project/capability/requirement IDs |
| departments clarify separately | non-value-added | questions and answers can be duplicated | no shared discovery and evidence log | extra handoffs and inconsistent assumptions | one evidence log; reference, do not copy, artifacts |
| maker prepares output | value-added | readiness may be discovered during execution | work starts before Ready gate | rework and blocked runs | validate intake before run creation |
| checker reviews output | business value | quality control exists but identity is only a role claim | runtime has no authenticated actor binding | separation is logical, not cryptographic | identity/role service before live writes |
| human approves release | business value | approval queue is not visible in one product surface | CLI/file state has no operator service | waiting and missed decisions | approval inbox backed by runtime API |
| external system is updated | value-added | not implemented | no admitted connector gateway | advisory-only operation | narrow connector adapter after manual proof and human authorization |
| outcome is reviewed | business value | no project-level baseline dataset | no intake measurement record | benefits cannot be proven | instrument from first manual intake |

### Future-state flow

1. Capture once in the intake pack; validate missing minimum fields immediately.
2. Route a shared evidence reference to the required roles; parallelize consultations only when
   dependencies allow it.
3. Convert approved requirements into one controlled run; never ask agents to infer authority.
4. Exchange questions, feedback, and artifacts by typed reference and hash.
5. Require an independent checker before downstream use, then a separate human gate for
   external or irreversible action.
6. Write the acknowledgement, connector response, and outcome event back to the project view.
7. Review measured lead time, first-pass yield, rework, approval wait, and defects; change the
   process only through an approved decision.

The future state removes duplicate capture, status chasing, raw-payload copying, and late gate
discovery. It does not remove the checker or human approval: those are business-value controls.

## 4. Bidirectional data flow

| Direction | Producer → consumer | Contract | Write authority | Failure response |
|---|---|---|---|---|
| inbound | sponsor/customer → intake | request ID, source, classification, received-at, payload reference/hash | intake service after authentication | quarantine invalid/restricted payload; ask a typed question |
| clarification | system/operator ↔ sponsor | question, answer, thread ID, actor, timestamp | authenticated participant | retain unresolved state; do not infer an answer |
| internal work | orchestrator ↔ maker/checker | task contract, evidence reference/hash, message kind, reply link, revision | runtime policy + role binding | reject unauthorized party, stale revision, changed artifact, or duplicate mutation |
| approval | runtime ↔ decision owner | action class, exact proposed effect, evidence, approve/reject, expiry | authenticated decision owner only | remain awaiting approval; expiry requires revalidation |
| connector | gateway ↔ external system | idempotency key, scoped request, provider receipt/version, error class | admitted adapter with least privilege | retry only safe/transient failures; otherwise degrade and escalate |
| outcome | external/system event → project | correlation ID, outcome metric, source, observed-at | measurement adapter or verified human entry | mark stale/unknown; never fabricate success |

Implemented locally: internal file-reference exchange, immutable hashes, reverse replies,
short-lived signed identities with DB-bound roles, organization-scoped API, SQLite persistence,
ingress validation, exact single-use approvals, a fixture connector gateway, receipts, webhook
replay defense, and verified backup/restore. Still missing: managed production identity,
real-provider admission, deployed outcome storage/observability, and live UAT.

## 5. Customer and operator journey

| Moment | User goal | Current friction | Future-state experience | Evidence |
|---|---|---|---|---|
| Ask | explain the need once | free-form request lacks decision context | guided minimum intake with plain-language prompts | intake completeness and clarification count |
| Align | know the right problem is being solved | assumptions are scattered | sponsor sees facts, assumptions, exclusions, and outcome in one brief | approved charter |
| Commit | understand scope, owner, and timing | readiness is implicit | Ready gate shows missing documents and blockers | gate record |
| Follow | see progress without chasing agents | status lives in CLI/files | one work queue shows owner, stage, queue age, and next decision | runtime events |
| Review | trust the output | checking and release authority can be conflated | maker-checker evidence appears before a separate approval action | submission and decision trail |
| Recover | correct a defect safely | fallback is document-only | bounded return loop, explicit degraded mode, rollback/support owner | feedback, retry, incident, rollback events |
| Learn | know whether value was delivered | baseline and outcome are not joined | outcome comparison and approved lesson close the loop | metric source and decision record |

Accessibility, clear language, keyboard navigation, responsive layout, visible system status,
and non-color-only state cues are release requirements, not polish.

## 6. Requirements and traceability

| Requirement | Acceptance | Evidence/status |
|---|---|---|
| FR-I01 The system shall prevent Ready status when a minimum intake field or document control is absent. | service and UI validation reject incomplete Ready | implemented and tested in `runtime/service.py` and `tests/test_operator_runtime.py` |
| FR-I02 The system shall preserve one stable chain from outcome to capability, requirement, run, test, and release evidence. | durable project revision and release record resolve to exact evidence | project store and fail-closed release record implemented; live references pending |
| FR-X01 The runtime shall support attributable request/response and corrective feedback in both directions. | exchange tests pass | implemented in `runtime/company_runtime.py` |
| FR-MC01 A maker shall not approve its own submission. | self-approval test fails closed | implemented |
| FR-H01 External or irreversible action shall require authenticated human approval. | unauthenticated/agent/self approval denied; exact approval logged/consumed once | passed locally; managed-IdP UAT pending |
| NFR-A01 The operator UI shall be responsive, keyboard usable, and expose status without color alone. | lint, build, rendered checks, keyboard/accessibility review | automated structure/build passed; human assistive-technology review not run |
| NFR-R01 The product shall recover state and prove backup/restore before release. | checksum/integrity verified backup and successful restore | passed locally; production RPO/RTO exercise pending |
| NFR-O01 Every mutation shall emit a correlated, tamper-evident audit event. | hash-chain and service/connector transaction tests | passed locally; production log/alert sink pending |

## 7. Production coverage and release decision

| Area | Status | Exact evidence | Release gap |
|---|---|---|---|
| governed intake process | Implemented | this document, templates, `tests/module-project-intake.sh` | needs five observed projects and sponsor approval |
| deterministic workflow and replay | Implemented locally | legacy file harness plus SQLite service tests | deployed service/monitoring not proven |
| maker-checker and internal exchange | Implemented locally | exchange tests plus authenticated approval/effect tests | production-IdP UAT pending |
| operator UI/UX | Implemented locally | signed gateway; durable project/preferences; lint/build/artifact/render/proxy tests | runtime environment configuration and human accessibility/visual UAT |
| identity, tenancy, and role binding | Implemented locally | migration 002, gateway signature/replay checks, organization-keyed DB role tests | managed lifecycle/MFA/access-review evidence |
| production persistence and recovery | Implemented locally | three migrations, two verified audit chains, optimistic concurrency, backup/restore, timers | deployed volume/retention and monitored RPO/RTO exercise |
| ingress/egress connector gateway | Control plane + fixture implemented | authorization/expiry/scopes, kill switch, exact approval, idempotency, receipts, reconciliation and webhook tests | provider choice and human OAuth/live DNS/redirect/rotation exercise |
| security/privacy/accessibility review | Automated/local complete | threat model, CodeQL pipeline, SBOM/secret scan, privacy minimization, automated UI checks | external security and human privacy/accessibility sign-offs |
| observability and operations | Implemented locally | protected metrics, JSON trace logs, readiness integrity, alerts, timers, SLO/incident runbook | live sinks, owners, baseline and alert exercises |
| CI/CD, deployment, UAT, rollback | Controls implemented / execution blocked | GitHub workflow, evidence generator, fail-closed release gate, deploy artifacts and UAT matrix | remote CI, human UAT, approved deployment and timed rollback run |

**Release decision: BLOCKED.** Local development for the three bounded capabilities is complete,
but the product must not be described as production-ready while any external or human evidence
gate above is Blocked or Not run. See `docs/PRODUCTION-READINESS-GAP-CLOSURE-2026-08-06.md`.

## 8. Next loop and stop conditions

The next evidence-sized increment is **human-governed environment qualification**: choose the
managed IdP, deployment target, residency/retention policy, monitoring/SLO and first connector;
then execute UAT, external security/accessibility review, deployment and rollback rehearsal.

Stop and request a human decision before selecting those business/vendor facts, publishing a
Site, authorizing OAuth, accepting risk, or restoring production data. Stop the run on failed
authorization, tenant isolation, hash chain, changed approval payload, dependency audit,
recovery, accessibility severity-1/2, or rollback evidence.
