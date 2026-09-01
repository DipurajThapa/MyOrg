#!/usr/bin/env python3
"""Deterministic harness for bounded, human-gated Company OS workflows."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
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
TERMINAL_RUN = TERMINAL | {"blocked_cycle_limit", "blocked_review_limit"}
MESSAGE_KINDS = {"handoff", "question", "answer", "feedback", "decision"}
CLASSIFICATIONS = {"public", "internal", "confidential"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            if isinstance(review_limit,int) and isinstance(step.get("max_attempts"),int) and step["max_attempts"] < review_limit+1: errors.append(f"{step_id}: max_attempts must allow initial work plus every review return")
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
        state.update(seq=state["seq"]+1, event=event, actor=actor, target=target, request_id=request_id, ts=now(), cycle_count=state["cycle_count"]+1)
        entry = audit(state) if audit else None
        if entry: audit_log.append(evidence=audit_evidence(run_id), **entry)
        if state["run_status"] != was and state["run_status"] in TERMINAL_RUN: record_terminal(run_id, state)
        append_event(run_id, state)
        return state


def create_run(args) -> None:
    workflow_path = Path(args.workflow).resolve()
    workflow = load_json(workflow_path)
    validate_workflow(workflow)
    run_id = args.run_id
    with run_lock(run_id):
        path = run_path(run_id)
        if path.exists(): raise SystemExit(f"run already exists: {run_id}")
        states = {}
        for step in workflow["steps"]:
            states[step["id"]] = {"status":"ready" if not step.get("depends_on") else "pending", "attempts":0, "owner":step["owner"], "checker":step.get("checker"), "review_cycles":0, "max_review_cycles":step.get("max_review_cycles",0), "submissions":[], "action":step["action"], "risk":policy()[step["action"]], "max_attempts":step["max_attempts"], "depends_on":step.get("depends_on", [])}
        state = {"seq":1,"event":"run.created","target":run_id,"request_id":args.request_id,"actor":args.actor,"ts":now(),"run_id":run_id,"org_id":getattr(args,"org",None) or DEFAULT_ORG,"workflow_id":workflow["id"],"workflow_revision":revision(workflow),"goal":workflow["goal"],"max_cycles":workflow["max_cycles"],"cycle_count":0,"run_status":"active","messages":[],"steps":states}
        append_event(run_id, state)
        snapshot = RUNS / f"{run_id}.workflow.json"
        snapshot.write_bytes(canonical(workflow) + b"\n")
    print(f"{run_id}\t{state['workflow_revision']}")


def request_step(args) -> None:
    def change(state):
        step = state["steps"].get(args.step)
        if not step or step["status"] != "ready": raise SystemExit(f"step is not ready: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        if step["risk"] == "red": step["status"] = "blocked_human"; state["run_status"]="blocked_human"
        elif step["risk"] == "yellow": step["status"] = "awaiting_approval"
        else: step["status"] = "in_progress"; step["attempts"] += 1
    def audit(state):
        step = state["steps"][args.step]
        if step["risk"] == "green": return None  # ordinary work is not a gated action
        gated = step["risk"] == "yellow"
        return {"actor": args.actor, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}",
                "approval": "pending" if gated else "not-required",
                "outcome": "awaiting-approval" if gated else "refused",
                "note": "parked for a human decision" if gated
                        else "handed back to a human; no code path can approve this"}
    state = mutate(args.run_id,args.request_id,"step.requested",args.actor,args.step,change,audit)
    print(state["steps"][args.step]["status"])


def approve(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_approval": raise SystemExit(f"step is not awaiting approval: {args.step}")
        if not args.approval_ref.strip(): raise SystemExit("approval_ref is required")
        step.update(status="in_progress", attempts=step["attempts"]+1, approver=args.approver, approval_ref=args.approval_ref)
    def audit(state):
        step = state["steps"][args.step]
        return {"actor": args.approver, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "granted", "outcome": "ok",
                "note": f"approved by a named human against reference {args.approval_ref}"}
    mutate(args.run_id,args.request_id,"step.approved",args.approver,args.step,change,audit); print("in_progress")


def reject(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_approval": raise SystemExit(f"step is not awaiting approval: {args.step}")
        step.update(status="rejected", approver=args.approver, approval_ref=args.approval_ref)
        state["run_status"]="rejected"
    def audit(state):
        step = state["steps"][args.step]
        return {"actor": args.approver, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "denied", "outcome": "blocked",
                "note": f"declined by a named human against reference {args.approval_ref}"}
    mutate(args.run_id,args.request_id,"step.rejected",args.approver,args.step,change,audit); print("rejected")


def hold(args) -> None:
    """Park a step because a control could not run -- never because the work was judged bad.

    The deliverable is kept and handed to a person, so the decision a human makes is about
    real work they can read, and the agent never has to produce it twice."""
    proof,proof_hash=evidence_path(args.evidence)
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        step.update(status="awaiting_approval", held_reason=args.reason, held_evidence=proof, held_evidence_sha256=proof_hash)
    def audit(state):
        step=state["steps"][args.step]
        return {"actor": args.actor, "action": step["action"], "category": step["risk"],
                "target": f"{args.run_id}/{args.step}", "approval": "pending",
                "outcome": "awaiting-approval",
                "note": "a quality gate could not run, so the work waits for a person"}
    mutate(args.run_id,args.request_id,"step.held",args.actor,args.step,change,audit); print("awaiting_approval")


def evidence_path(value: str) -> tuple[str,str]:
    path=(ROOT/value).resolve()
    try: relative=path.relative_to(ROOT)
    except ValueError as error: raise SystemExit("evidence must be inside the repository") from error
    if not path.is_file(): raise SystemExit(f"evidence file does not exist: {relative}")
    return str(relative),hashlib.sha256(path.read_bytes()).hexdigest()


def release_dependents(state: dict) -> None:
    for candidate in state["steps"].values():
        if candidate["status"] == "pending" and all(state["steps"][dependency]["status"] == "completed" for dependency in candidate["depends_on"]): candidate["status"]="ready"
    if all(item["status"] == "completed" for item in state["steps"].values()): state["run_status"]="completed"


def verify_submission(step: dict) -> None:
    if not step.get("submissions"): raise SystemExit("step has no submission")
    submission=step["submissions"][-1]
    _,current_hash=evidence_path(submission["evidence"])
    if current_hash != submission["evidence_sha256"]: raise SystemExit("submission artifact changed after maker handoff")


def complete(args) -> None:
    proof,proof_hash=evidence_path(args.evidence)
    def change(state):
        if args.revision != state["workflow_revision"]: raise SystemExit("stale workflow revision")
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        submission={"submission_revision":len(step["submissions"])+1,"evidence":proof,"evidence_sha256":proof_hash,"maker":args.actor,"submitted_at":now()}
        step["submissions"].append(submission)
        step.update(status="awaiting_check" if step.get("checker") else "completed", evidence=proof, evidence_sha256=proof_hash)
        if not step.get("checker"): release_dependents(state)
    state=mutate(args.run_id,args.request_id,"step.completed",args.actor,args.step,change); print(state["run_status"])


def fail(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "in_progress": raise SystemExit(f"step is not in progress: {args.step}")
        if args.actor != step["owner"]: raise SystemExit(f"step owner is {step['owner']}, not {args.actor}")
        step["last_failure"]=args.reason
        step["status"]="ready" if step["attempts"] < step["max_attempts"] else "blocked_retry_limit"
        if step["status"] == "blocked_retry_limit": state["run_status"]="blocked_retry_limit"
    state=mutate(args.run_id,args.request_id,"step.failed",args.actor,args.step,change); print(state["steps"][args.step]["status"])


def send_message(args) -> None:
    if not ID_RE.fullmatch(args.message_id): raise SystemExit("invalid message id")
    proof,proof_hash=evidence_path(args.payload)
    if args.kind not in MESSAGE_KINDS: raise SystemExit("invalid message kind")
    if args.classification not in CLASSIFICATIONS: raise SystemExit("restricted or invalid classification")
    if not 1 <= len(args.subject.strip()) <= 160: raise SystemExit("subject must be 1..160 characters")
    def change(state):
        if any(item["id"] == args.message_id for item in state["messages"]): raise SystemExit("message id already exists")
        step=state["steps"].get(args.step)
        if not step: raise SystemExit(f"unknown step: {args.step}")
        current={step["owner"]}
        if step.get("checker"): current.add(step["checker"])
        adjacent=set()
        for dependency in step["depends_on"]:
            adjacent.add(state["steps"][dependency]["owner"])
            if state["steps"][dependency].get("checker"): adjacent.add(state["steps"][dependency]["checker"])
        for candidate in state["steps"].values():
            if args.step in candidate["depends_on"]:
                adjacent.add(candidate["owner"])
                if candidate.get("checker"): adjacent.add(candidate["checker"])
        pair={args.from_agent,args.to_agent}
        if not (pair <= current or (pair & current and pair & adjacent)): raise SystemExit("message participants must share this step or an adjacent workflow edge")
        if args.from_agent == args.to_agent: raise SystemExit("sender and receiver must differ")
        if args.reply_to:
            prior=next((item for item in state["messages"] if item["id"] == args.reply_to),None)
            if not prior: raise SystemExit("reply_to message does not exist")
            if prior["step"] != args.step or prior["from"] != args.to_agent or prior["to"] != args.from_agent: raise SystemExit("reply must reverse the original direction within the same step")
        state["messages"].append({"id":args.message_id,"step":args.step,"from":args.from_agent,"to":args.to_agent,"kind":args.kind,"subject":args.subject.strip(),"classification":args.classification,"payload":proof,"payload_sha256":proof_hash,"reply_to":args.reply_to,"sent_at":now()})
    mutate(args.run_id,args.request_id,"message.sent",args.from_agent,args.message_id,change); print(args.message_id)


def checker_message(state: dict, step_id: str, message_id: str, checker: str, kind: str = "feedback") -> dict:
    step=state["steps"][step_id]
    message=next((item for item in state["messages"] if item["id"] == message_id),None)
    if not message: raise SystemExit("checker message does not exist")
    if message["step"] != step_id: raise SystemExit("checker message belongs to another step")
    if message["from"] != checker or message["to"] != step["owner"] or message["kind"] != kind: raise SystemExit(f"checker must send {kind} to the maker")
    _,current_hash=evidence_path(message["payload"])
    if current_hash != message["payload_sha256"]: raise SystemExit("checker message artifact changed after sending")
    return message


def check_approve(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step)
        step.update(status="completed",checked_by=args.actor,check_message=args.message_id)
        checker_message(state,args.step,args.message_id,args.actor,"decision")
        release_dependents(state)
    state=mutate(args.run_id,args.request_id,"check.approved",args.actor,args.step,change); print(state["run_status"])


def check_return(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step); checker_message(state,args.step,args.message_id,args.actor)
        if step["review_cycles"] >= step["max_review_cycles"]:
            step["status"]="blocked_review_limit"; state["run_status"]="blocked_review_limit"
        else:
            step["review_cycles"]+=1; step["status"]="ready"; step["last_feedback_message"]=args.message_id
    state=mutate(args.run_id,args.request_id,"check.returned",args.actor,args.step,change); print(state["steps"][args.step]["status"])


def check_reject(args) -> None:
    def change(state):
        step=state["steps"].get(args.step)
        if not step or step["status"] != "awaiting_check": raise SystemExit(f"step is not awaiting check: {args.step}")
        if args.actor != step.get("checker"): raise SystemExit(f"step checker is {step.get('checker')}, not {args.actor}")
        verify_submission(step); checker_message(state,args.step,args.message_id,args.actor)
        step.update(status="rejected_by_checker",last_feedback_message=args.message_id); state["run_status"]="rejected_by_checker"
    mutate(args.run_id,args.request_id,"check.rejected",args.actor,args.step,change); print("rejected_by_checker")


def status(args) -> None:
    with run_lock(args.run_id): state=read_events(args.run_id)[-1]
    if args.json: print(json.dumps(state,indent=2,sort_keys=True)); return
    print(f"run={args.run_id}\tstatus={state['run_status']}\trevision={state['workflow_revision']}\tcycles={state['cycle_count']}/{state['max_cycles']}")
    for step_id, step in state["steps"].items(): print(f"{step_id}\t{step['status']}\towner={step['owner']}\tchecker={step.get('checker') or '-'}\trisk={step['risk']}\tattempts={step['attempts']}/{step['max_attempts']}\treviews={step.get('review_cycles',0)}/{step.get('max_review_cycles',0)}")
    print(f"messages={len(state.get('messages',[]))}")


def validate_cmd(args) -> None:
    validate_workflow(load_json(Path(args.workflow).resolve())); print("workflow valid")


def parser():
    result=argparse.ArgumentParser(description=__doc__); commands=result.add_subparsers(dest="command",required=True)
    command=commands.add_parser("validate"); command.add_argument("workflow"); command.set_defaults(func=validate_cmd)
    command=commands.add_parser("create-run"); command.add_argument("workflow"); command.add_argument("run_id"); command.add_argument("--actor",required=True); command.add_argument("--request-id",required=True); command.add_argument("--org",default=DEFAULT_ORG); command.set_defaults(func=create_run)
    for name,func in (("request-step",request_step),("fail",fail),("complete",complete),("hold",hold)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor",required=True); command.add_argument("--request-id",required=True)
        if name == "fail": command.add_argument("--reason",required=True)
        if name == "complete": command.add_argument("--evidence",required=True); command.add_argument("--revision",required=True)
        if name == "hold": command.add_argument("--evidence",required=True); command.add_argument("--reason",required=True)
        command.set_defaults(func=func)
    for name,func in (("approve",approve),("reject",reject)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--approver",required=True); command.add_argument("--approval-ref",required=True); command.add_argument("--request-id",required=True); command.set_defaults(func=func)
    command=commands.add_parser("send-message"); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("message_id"); command.add_argument("--from-agent",required=True); command.add_argument("--to-agent",required=True); command.add_argument("--kind",required=True,choices=sorted(MESSAGE_KINDS)); command.add_argument("--subject",required=True); command.add_argument("--payload",required=True); command.add_argument("--classification",required=True,choices=sorted(CLASSIFICATIONS)); command.add_argument("--reply-to"); command.add_argument("--request-id",required=True); command.set_defaults(func=send_message)
    for name,func in (("check-approve",check_approve),("check-return",check_return),("check-reject",check_reject)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor",required=True); command.add_argument("--message-id",required=True); command.add_argument("--request-id",required=True)
        command.set_defaults(func=func)
    command=commands.add_parser("status"); command.add_argument("run_id"); command.add_argument("--json",action="store_true"); command.set_defaults(func=status)
    return result


if __name__ == "__main__":
    args=parser().parse_args(); args.func(args)
