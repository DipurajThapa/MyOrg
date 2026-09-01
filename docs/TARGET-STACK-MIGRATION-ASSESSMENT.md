# Target stack vs. what exists — migration assessment

**Date:** 2026-09-01 · **Status:** assessment only, no action taken
**Evidence:** read from the repo at commit `dcd2899`. Effort figures are inference, marked **[I]**.

---

## 1. The short answer

Three of the eight layers are already what you want. Two are close. Three are a rewrite.

| Layer | Target | What exists | Gap |
|---|---|---|---|
| Autonomous runtime | Python workers + orchestration engine | **Python, already built** — executor, scheduler, planner, maker-checker, state machine | ✅ none |
| CI | GitHub Actions | **Exists and runs** — 3 jobs, CodeQL, SBOM, release gate | 🟡 no image build or deploy |
| Frontend language | React + TypeScript + Vite | **React 19 + TS 5.9 + Vite 8** | 🟡 wrapped in Next 16 + `vinext` + Cloudflare RSC |
| Backend | Python + FastAPI | Python **stdlib `http.server`** — 3 hand-rolled servers | 🔴 rewrite the HTTP layer |
| Database | PostgreSQL | **SQLite** + JSONL event logs | 🔴 port |
| Cache/queue | Redis | **nothing** | 🔴 build |
| Hosting | Hostinger VPS + Ubuntu + Docker Compose | **nothing containerised**; systemd units only | 🔴 build |
| Edge | Nginx or Caddy | example reverse-proxy conf, never used | 🟠 small |
| Testing | pytest + frontend tests + Playwright | 16 `unittest` files + 23 bash suites + 1 node test | 🟡 pytest is cheap, Playwright is not |

**Headline: ~6–8 focused weeks for one engineer [I].** The autonomous runtime — the hard,
original part — needs no migration. The cost is in the plumbing around it.

---

## 2. Layer by layer

### 2.1 Autonomous runtime — ✅ already there

`runtime/` is 5,700 lines of Python: the state machine, executor, scheduler, planner,
maker-checker, memory, health, escalation, projection. It is the product, and it moves to the
target stack unchanged.

**One real constraint.** `runtime/backends.py:56` dispatches work by shelling out to the
`claude` CLI. Inside a container that binary must be installed **and authenticated**, and its
credentials must survive restarts without being baked into the image. This is the single most
likely thing to derail the containerisation. **[I]** Budget 1–2 days and prove it early with a
throwaway container before committing to the rest.

### 2.2 Backend — 🔴 FastAPI rewrite of the HTTP layer only

Three separate stdlib servers exist: `api.py` (16 routes, 411 lines), `agent_api.py` (6 routes),
`approval_server.py`. All hand-roll routing, JSON parsing, security headers, rate limiting and
auth.

The good news: business logic already sits behind them in `service.py`, `db.py` and the runtime
modules. FastAPI replaces the transport, not the thinking. You also get for free the things the
tracker lists as missing — **API-06 OpenAPI** (automatic) and typed request validation
(currently hand-written).

**Effort: 4–6 days [I].** Port routes, move auth into a dependency, keep the existing security
headers and body caps as middleware, re-point the existing tests.

### 2.3 Database — 🔴 the biggest single job

- 4 migrations, 75 `execute()` call sites, 16 SQLite-specific constructs
  (`AUTOINCREMENT`, `INSERT OR …`, `sqlite3.IntegrityError`).
- `durability.py` implements backup/restore against SQLite files — rewritten for `pg_dump`.
- Placeholders change (`?` → `%s`), types change, upserts change, transaction semantics change.

**Decision you have to make first: what moves.** The runtime's system of record is **not**
SQLite — it is the hash-chained JSONL log in `runtime/runs/` (ARCH-01, settled deliberately).
SQLite is identity plus the operator read model. So:

- **Recommended:** port only SQLite → Postgres. Leave the JSONL execution log alone. Smaller,
  safer, and it keeps the settled architecture.
- **Not recommended yet:** moving the event log into Postgres. It is a bigger change than the
  whole rest of this list, and nothing in the target stack requires it.

**Effort: 5–8 days [I]** for the recommended scope.

### 2.4 Redis — 🔴 build, but do it *after* one open defect

Nothing uses Redis today. The natural jobs for it here are lease/ownership state, cross-process
scheduler coordination, and rate limiting (currently per-process, so it breaks the moment you
run two API containers).

**This connects directly to an open P0.** REC-10 (audit §12) found that step leases are
advisory: the in-process driver dispatches a step an external worker already holds, so the work
is done twice and one copy is discarded. Today that needs two drivers to collide. **Docker
Compose with more than one worker replica makes it the normal case.** Containerising before
REC-10 is fixed converts a latent defect into a routine one.

Related: the runtime's `filelock.py` uses OS file locks. Those are reliable on one host and one
filesystem. Across containers on a shared volume they are not a safe basis for correctness.

**Effort: 2–3 days [I]**, and it should follow the REC-10 fix, not precede it.

### 2.5 Frontend — 🟡 mostly subtraction

You already have React 19, TypeScript 5.9 and Vite 8. What you do not want is the layer on top:
`next@16`, `vinext`, `@vitejs/plugin-rsc`, `react-server-dom-webpack`, `wrangler` and a
Cloudflare Worker acting as the API proxy — plus custom bash build scripts
(`build-verified.sh`, `sites-env.sh`, `validate-artifact.sh`) that currently do not run on
Windows at all.

Under the target stack the Worker's job (proxying to the API) belongs to Caddy or Nginx.

**Effort: 3–5 days [I].** Mostly deletion and a plain Vite SPA build.

### 2.6 Hosting, edge, deployment — 🔴 all new, but standard

No Dockerfile, no compose file, and **no Python dependency manifest at all** (DEP-06) — no
`requirements.txt`, no `pyproject.toml`. That has to exist before anything can be containerised.

Services in the compose file: `api`, `worker` (scheduler/executor), `ui`, `postgres`, `redis`,
`caddy`. The existing systemd units in `deploy/` become redundant.

**Effort: manifest 0.5d · images + compose 2–3d · Caddy 1d · GH Actions → GHCR → VPS 2–3d ·
VPS setup, TLS, backups, hardening 2–3d [I].**

### 2.7 Testing — 🟡 pytest cheap, Playwright blocked

- 16 `unittest` files run under pytest **as-is** — pytest collects `unittest.TestCase` natively.
  Adding pytest is roughly a manifest entry, a `conftest.py` and CI wiring: **~1 day [I]**.
- The 23 bash module suites are prose-conformance checks over Markdown. They still work in a
  container; porting them to pytest is optional and low value.
- **Playwright is blocked on product, not tooling.** The Control Center today has project
  intake and view state only — there is no approve/reject screen (HITL-04). An E2E suite needs
  a flow to drive. Build the approval UI first, then Playwright: **3–4 days [I]** after that.

---

## 3. Recommended order

Each step is safe to stop after.

1. **Finish the two open P0s first — VAL-07, then REC-10.** Both change governance-critical
   code paths, and REC-10 in particular is a prerequisite for running more than one worker.
   Containerising first would multiply the bug.
2. **`pyproject.toml` + pytest** (DEP-06). Nothing can be built into an image without it, and
   it is half a day.
3. **Prove the `claude` CLI works inside a container**, authenticated, surviving restart. If
   this cannot be made to work, the whole shape of the deployment changes — find out now.
4. **FastAPI port**, keeping SQLite. One variable at a time; the existing tests stay the safety
   net.
5. **Postgres port**, keeping the JSONL event log where it is.
6. **Docker Compose + Caddy locally**, single replica.
7. **Redis**, then scale the worker past one replica — safe only after step 1.
8. **Frontend de-Next to a plain Vite SPA.**
9. **GitHub Actions → GHCR → Hostinger VPS**, plus backups and TLS.
10. **Approval UI (HITL-04), then Playwright E2E.**

## 4. Rough total

| Phase | Days **[I]** |
|---|---|
| Manifest, pytest, CLI-in-container spike | 2–4 |
| FastAPI port | 4–6 |
| Postgres port | 5–8 |
| Docker Compose + Caddy | 3–4 |
| Redis + multi-replica | 2–3 |
| Frontend de-Next | 3–5 |
| CI → registry → VPS + ops | 4–6 |
| Approval UI + Playwright | 6–8 |
| **Total** | **29–44 days** ≈ 6–9 weeks solo |

Excludes the P0 work in step 1, which is tracked separately in
`docs/AUTONOMY-AUDIT-2026-09-01-REV2.md`.

## 5. Two things worth deciding before any of it

1. **Does the event log stay as files?** Recommended yes. If it moves to Postgres, add ~2 weeks
   and re-open ARCH-01.
2. **How many worker replicas do you actually need?** If the answer is one, Redis and much of
   the coordination work can be deferred, and the migration drops by roughly a week.
