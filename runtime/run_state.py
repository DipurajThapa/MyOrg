#!/usr/bin/env python3
"""The state machine every verb goes through, and the facts every verb reads.

Nothing here is a command. `mutate` is the one door: it takes the run lock, replays an
identical request id, refuses a terminal run, applies the change, writes the audit entry
*before* the run event, and appends. A verb decides what to change; this decides whether
the change is allowed to happen and that it is recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # importable both as `runtime.company_runtime` and as a script
from runtime.filelock import exclusive_lock  # noqa: E402
from runtime import audit as audit_log  # noqa: E402

RUNS = Path(os.environ.get("MYORG_RUNS_DIR", ROOT / "runtime" / "runs"))
POLICY_PATH = ROOT / "runtime" / "policy.json"
DEFAULT_ORG = os.environ.get("MYORG_ORG_ID", "default")
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
TERMINAL = {"completed", "rejected", "blocked_human", "blocked_retry_limit"}
# Every run_status a run can end in. The one list: health, escalation and the projection
# derive from it and a test holds them to it, so a new end state cannot read as "running".
TERMINAL_RUN = TERMINAL | {"blocked_cycle_limit", "blocked_review_limit",
                           "rejected_by_checker", "cancelled"}
# Step statuses that mean "a person has to act". Shared for the same reason.
WAITING_STEP = {"awaiting_approval", "blocked_human"}
CLAIM_SECONDS = int(os.environ.get("MYORG_CLAIM_SECONDS", "600"))
MESSAGE_KINDS = {"handoff", "question", "answer", "feedback", "decision"}
CLASSIFICATIONS = {"public", "internal", "confidential"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stamp(seconds: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def claim_is_live(step: dict) -> bool:
    """Is somebody actually doing this step right now?"""
    return bool(step.get("holder")) and str(step.get("claim_expires_at", "")) > now()


def mint_claim(state: dict, step: dict, holder: str) -> None:
    """Hand the step to one holder, with a token that only ever increases.

    The token is the sequence number of the event that grants it, so a later claim always
    outranks an earlier one and a stale holder's write can be told apart from the current
    holder's -- the fencing-token rule."""
    step["holder"] = holder
    step["claim_token"] = f"{state['run_id']}#{state['seq'] + 1}"
    step["claim_expires_at"] = stamp(CLAIM_SECONDS)


def release_claim(step: dict) -> None:
    step.update(holder="", claim_token="", claim_expires_at="")


def check_claim(step: dict, supplied: str | None) -> None:
    """Reject a write from anyone but the current holder.

    A caller that supplies no token at all is the human operator at the CLI, and is let
    through: the machine callers (the driver and the agent API) always carry one, and a
    person with shell access can already do anything. A *wrong* token is always refused --
    that is the case where two automated holders raced."""
    if supplied is None: return
    current = step.get("claim_token") or ""
    if supplied != current:
        raise SystemExit(f"stale claim: {supplied!r} is not the current claim on this step")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot load {path}: {error}") from error


def canonical(data: dict) -> bytes:
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode()


def revision(data: dict) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def policy() -> dict[str, str]:
    data = load_json(POLICY_PATH)
    if data.get("version") != 1 or not isinstance(data.get("actions"), dict):
        raise SystemExit("unsupported or invalid policy")
    return data["actions"]


def agent_exists(owner: str) -> bool:
    return (ROOT / ".claude" / "agents" / f"{owner}.md").is_file()


def validate_workflow(data: dict) -> None:
    errors = []
    actions = policy()
    if data.get("version") != 1: errors.append("version must be 1")
    if not ID_RE.fullmatch(str(data.get("id", ""))): errors.append("workflow id must be a lowercase slug")
    if not str(data.get("goal", "")).strip(): errors.append("goal is required")
    max_cycles = data.get("max_cycles")
    if not isinstance(max_cycles, int) or not 1 <= max_cycles <= 100: errors.append("max_cycles must be 1..100")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps: errors.append("steps must be a non-empty list")
    if errors: raise SystemExit("\n".join(errors))
    ids = [str(step.get("id", "")) for step in steps]
    if len(ids) != len(set(ids)): errors.append("step ids must be unique")
    for step in steps:
        step_id = str(step.get("id", ""))
        if not ID_RE.fullmatch(step_id): errors.append(f"invalid step id: {step_id}")
        if not agent_exists(str(step.get("owner", ""))): errors.append(f"{step_id}: unknown owner {step.get('owner')}")
        checker=step.get("checker")
        if checker:
            if not agent_exists(str(checker)): errors.append(f"{step_id}: unknown checker {checker}")
            if checker == step.get("owner"): errors.append(f"{step_id}: maker and checker must differ")
            if actions.get(step.get("action")) != "green": errors.append(f"{step_id}: checker belongs on a green preparation step before a separate human-gated action")
            review_limit=step.get("max_review_cycles")
            if not isinstance(review_limit,int) or not 1 <= review_limit <= 3: errors.append(f"{step_id}: max_review_cycles must be 1..3")
            # +2, not +1. A step is graded against its own acceptance criteria before a
            # checker ever sees it, and a rejected attempt is spent -- so +1 leaves nothing
            # for a single grader rejection. The planner prompt has advised +2 for a while
            # and models kept writing the schema minimum instead; every large generated
            # workflow on disk died this way, its first research step out of attempts with
            # 25 steps still pending behind it. Advice in a prompt is not a rule.
            if isinstance(review_limit,int) and isinstance(step.get("max_attempts"),int) and step["max_attempts"] < review_limit+2: errors.append(f"{step_id}: max_attempts must be at least max_review_cycles + 2 -- one go at the work, one spare for a grader rejection, and one per review return")
        elif "max_review_cycles" in step: errors.append(f"{step_id}: max_review_cycles requires checker")
        if step.get("action") not in actions: errors.append(f"{step_id}: action is not policy-classified: {step.get('action')}")
        attempts = step.get("max_attempts")
        if not isinstance(attempts, int) or not 1 <= attempts <= 5: errors.append(f"{step_id}: max_attempts must be 1..5")
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list): errors.append(f"{step_id}: depends_on must be a list")
        for dependency in dependencies if isinstance(dependencies, list) else []:
            if dependency not in ids: errors.append(f"{step_id}: unknown dependency {dependency}")
            if dependency == step_id: errors.append(f"{step_id}: cannot depend on itself")
    graph = {step["id"]: step.get("depends_on", []) for step in steps}
    visiting, visited = set(), set()
    def visit(node: str) -> None:
        if node in visiting: errors.append(f"dependency cycle includes {node}"); return
        if node in visited: return
        visiting.add(node)
        for dependency in graph.get(node, []): visit(dependency)
        visiting.remove(node); visited.add(node)
    for node in graph: visit(node)
    if errors: raise SystemExit("\n".join(dict.fromkeys(errors)))


def run_files() -> list[Path]:
    """Every file that is actually a run. `_`-prefixed files are sidecars: a run id
    must start with a letter, so the two can never collide."""
    return sorted(p for p in RUNS.glob("*.jsonl") if not p.stem.startswith("_"))


def run_path(run_id: str) -> Path:
    if not ID_RE.fullmatch(run_id): raise SystemExit("invalid run id")
    return RUNS / f"{run_id}.jsonl"


@contextmanager
def run_lock(run_id: str):
    with exclusive_lock(RUNS/f"{run_id}.lock"): yield


def read_events(run_id: str) -> list[dict]:
    path = run_path(run_id)
    if not path.exists(): raise SystemExit(f"unknown run: {run_id}")
    events = []
    previous_hash = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try: row=json.loads(line)
        except json.JSONDecodeError as error: raise SystemExit(f"{path}:{number}: invalid JSON: {error}") from error
        if row.get("seq") != number: raise SystemExit(f"{path}:{number}: broken event sequence")
        if row.get("prev_event_hash") != previous_hash: raise SystemExit(f"{path}:{number}: broken event chain")
        supplied=row.get("event_hash"); payload=dict(row); payload.pop("event_hash",None)
        expected=revision(payload)
        if supplied != expected: raise SystemExit(f"{path}:{number}: event hash mismatch")
        previous_hash=supplied; events.append(row)
    if not events: raise SystemExit(f"empty run: {run_id}")
    return events


def append_event(run_id: str, state: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    path = run_path(run_id)
    previous_hash = None
    if path.exists() and path.stat().st_size:
        previous_hash = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])["event_hash"]
    state.pop("event_hash",None); state.pop("prev_event_hash",None)
    state["prev_event_hash"]=previous_hash
    state["event_hash"]=revision(state)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(state, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush(); os.fsync(handle.fileno())


def attribution(state: dict, approver: str, actor_id: str | None = None) -> str:
    """How much this record is actually entitled to claim about who approved.

    The CLI takes `--approver` as a string and cannot authenticate it; the HTTP path binds
    a registered identity. The audit line used to say "approved by a named human" either
    way, which made the evidence layer -- the one thing built to be trustworthy -- assert
    something nobody had checked. So the note now states what was verified: the store is
    consulted when one exists, and when it does not the line says the name is unverified
    rather than implying it is not.

    `approver` is what the record shows a reader; `actor_id` is what the store is keyed by
    (B-10). The API passes both -- it authenticated an id and displays a name -- so the
    lookup no longer searched the registry for "Dipuraj Thapa" and reported the one
    authenticated path as unregistered. The CLI passes only a name and is checked as one.
    """
    try:
        from runtime.projection import default_db
        path = default_db()
        if not path.is_file():
            return f"approved by '{approver}' (name self-asserted at the CLI, unverified)"
        from runtime.db import NotFound, Store
        try:
            actor = Store(path).actor(state.get("org_id", DEFAULT_ORG), actor_id or approver)
        except NotFound:
            return f"approved by '{approver}' (not a registered actor in this organization)"
        if actor["actor_type"] != "human" or actor["status"] != "active":
            return (f"approved by '{approver}' (registered as an {actor['actor_type']}, "
                    f"status {actor['status']})")
        return f"approved by {approver}, a registered active human"
    except Exception:  # noqa: BLE001 - attribution must never block the gate it describes
        return f"approved by '{approver}' (attribution could not be checked)"


def audit_evidence(run_id: str) -> str:
    """The run's own log is the evidence for anything the runtime records about it."""
    path = run_path(run_id)
    try: return str(path.relative_to(ROOT)).replace("\\","/")
    except ValueError: return str(path)


def record_terminal(run_id: str, state: dict) -> None:
    audit_log.append(actor="runtime", action=f"run.{state['run_status']}", category="green",
                     target=run_id, approval="not-required", evidence=audit_evidence(run_id),
                     outcome="blocked" if state["run_status"] != "completed" else "ok",
                     note=f"the run reached {state['run_status']} and can go no further")


def mutate(run_id: str, request_id: str, event: str, actor: str, target: str, change, audit=None) -> dict:
    """`audit` returns the entry a gated transition must leave behind, or None.

    The entry is written *before* the run event, so a log the runtime cannot write stops
    the transition instead of letting an ungoverned action through unrecorded."""
    with run_lock(run_id):
        events = read_events(run_id)
        prior=next((item for item in events if item["request_id"] == request_id),None)
        if prior:
            if prior.get("event") != event or prior.get("actor") != actor or prior.get("target") != target: raise SystemExit("request_id reused for a different mutation")
            print("idempotent replay"); return events[-1]
        state = json.loads(json.dumps(events[-1]))
        if state["run_status"] != "active": raise SystemExit(f"run is terminal: {state['run_status']}")
        if state["cycle_count"] >= state["max_cycles"]:
            state.update(seq=state["seq"]+1, event="run.blocked_cycle_limit", actor="runtime", target=run_id, request_id=request_id, ts=now(), run_status="blocked_cycle_limit")
            record_terminal(run_id, state)
            append_event(run_id, state)
            raise SystemExit("run reached max_cycles")
        was = state["run_status"]
        change(state)
        # A run is active or in one of the canonical end states -- nothing else. Every reader
        # (health, escalation, projection, the ceiling) derives from TERMINAL_RUN, so a status
        # outside it would be invisible to all of them; refuse it here, once, for every verb.
        if state["run_status"] != "active" and state["run_status"] not in TERMINAL_RUN:
            raise SystemExit(f"unknown run status: {state['run_status']!r} is not in TERMINAL_RUN")
        state.update(seq=state["seq"]+1, event=event, actor=actor, target=target, request_id=request_id, ts=now(), cycle_count=state["cycle_count"]+1)
        entry = audit(state) if audit else None
        if entry: audit_log.append(evidence=audit_evidence(run_id), **entry)
        if state["run_status"] != was and state["run_status"] in TERMINAL_RUN: record_terminal(run_id, state)
        append_event(run_id, state)
        return state


