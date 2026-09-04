#!/usr/bin/env python3
"""The runtime's command line, and the one name every other module imports it by.

The state machine and the verbs moved out into four files -- `run_state` holds `mutate` and
everything it needs, `run_verbs` a step's life, `run_outcomes` how it ends, `run_review` the
second opinion and the department gate. They are re-exported here on purpose:
`import company_runtime as core` is how the executor, the scheduler, the service, the agent
API and the tests reach the runtime, and none of them should have to change because a verb
moved to a neighbouring file.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # runnable as a script too

from runtime import run_outcomes as _outcomes  # noqa: E402
from runtime import run_review as _review  # noqa: E402
from runtime import run_state as _state  # noqa: E402
from runtime import run_verbs as _verbs  # noqa: E402

# Reloading this module is how a test picks up a changed MYORG_* value -- `CLAIM_SECONDS`
# and `RUNS` are read from the environment once, at import. They are read in `run_state`
# now, so reloading only this module would re-import the old values and the change would
# vanish silently. Reload the sources first, then bind what they now hold. `importlib`
# re-executes a reload in the module's existing namespace, so this flag surviving is
# exactly what tells us this run is a reload and not the first import.
if globals().get("_SOURCES_BOUND"):
    for _source in (_state, _outcomes, _verbs, _review):
        importlib.reload(_source)
_SOURCES_BOUND = True

from runtime.run_state import (CLAIM_SECONDS, CLASSIFICATIONS, DEFAULT_ORG, ID_RE,
                               MESSAGE_KINDS, POLICY_PATH, ROOT, RUNS, TERMINAL, TERMINAL_RUN,
                               WAITING_STEP, agent_exists, append_event, attribution,
                               audit_evidence, canonical, check_claim, claim_is_live, load_json,
                               mint_claim, mutate, now, policy, read_events, record_terminal,
                               release_claim, revision, run_files, run_lock, run_path, stamp,
                               validate_workflow)
from pathlib import Path

from runtime.run_outcomes import (cancel_run, charge, complete, evidence_path, extend_budget,
                                  fail, release_dependents, release_step, verify_submission)
from runtime.run_verbs import (approve, create_run, expire_claim, hold, reject, renew_claim,
                               request_step, take)
from runtime.run_review import (check_approve, check_reject, check_return, checker_message,
                                gate, send_message)

__all__ = [
    "CLAIM_SECONDS", "CLASSIFICATIONS", "DEFAULT_ORG", "ID_RE", "MESSAGE_KINDS",
    "POLICY_PATH", "ROOT", "RUNS", "TERMINAL", "TERMINAL_RUN", "WAITING_STEP", "agent_exists",
    "append_event", "approve", "attribution", "audit_evidence", "cancel_run", "canonical",
    "charge", "check_approve", "check_claim", "check_reject", "check_return",
    "checker_message", "claim_is_live", "complete", "create_run", "evidence_path",
    "expire_claim", "extend_budget", "fail", "gate", "hold", "load_json", "mint_claim",
    "mutate", "now", "policy", "read_events", "record_terminal", "reject", "release_claim",
    "release_dependents", "release_step", "renew_claim", "request_step", "revision",
    "run_files", "run_lock", "run_path", "send_message", "stamp", "take", "validate_workflow",
    "verify_submission",
    "parser", "status", "validate_cmd",
]


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
    for name,func in (("request-step",request_step),("fail",fail),("complete",complete),("hold",hold),("take",take)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor",required=True); command.add_argument("--request-id",required=True)
        if name == "fail": command.add_argument("--reason",required=True)
        if name == "complete": command.add_argument("--evidence",required=True); command.add_argument("--revision",required=True)
        if name == "hold": command.add_argument("--evidence",required=True); command.add_argument("--reason",required=True)
        if name in ("request-step","take"): command.add_argument("--holder")
        if name in ("complete","fail","hold"): command.add_argument("--claim-token")
        command.set_defaults(func=func)
    for name,func in (("approve",approve),("reject",reject)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--approver",required=True); command.add_argument("--approval-ref",required=True); command.add_argument("--request-id",required=True); command.set_defaults(func=func)
    command=commands.add_parser("send-message"); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("message_id"); command.add_argument("--from-agent",required=True); command.add_argument("--to-agent",required=True); command.add_argument("--kind",required=True,choices=sorted(MESSAGE_KINDS)); command.add_argument("--subject",required=True); command.add_argument("--payload",required=True); command.add_argument("--classification",required=True,choices=sorted(CLASSIFICATIONS)); command.add_argument("--reply-to"); command.add_argument("--request-id",required=True); command.set_defaults(func=send_message)
    for name,func in (("check-approve",check_approve),("check-return",check_return),("check-reject",check_reject)):
        command=commands.add_parser(name); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor",required=True); command.add_argument("--message-id",required=True); command.add_argument("--request-id",required=True); command.add_argument("--spend",type=float,default=0.0)
        command.set_defaults(func=func)
    command=commands.add_parser("gate", help="put one department action through the gate"); command.add_argument("run_id"); command.add_argument("--owner",required=True); command.add_argument("--action",required=True); command.add_argument("--summary",required=True); command.add_argument("--org"); command.add_argument("--request-id",required=True); command.set_defaults(func=gate)
    command=commands.add_parser("release-step"); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor",required=True); command.add_argument("--reason",required=True); command.add_argument("--spend",type=float,default=0.0); command.add_argument("--claim-token"); command.add_argument("--request-id",required=True); command.set_defaults(func=release_step)
    command=commands.add_parser("expire-claim"); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--actor"); command.add_argument("--request-id",required=True); command.set_defaults(func=expire_claim)
    command=commands.add_parser("renew-claim"); command.add_argument("run_id"); command.add_argument("step"); command.add_argument("--holder",required=True); command.add_argument("--claim-token",required=True); command.add_argument("--request-id",required=True); command.set_defaults(func=renew_claim)
    command=commands.add_parser("extend-budget"); command.add_argument("run_id"); command.add_argument("--cycles",type=int,required=True); command.add_argument("--approver",required=True); command.add_argument("--request-id",required=True); command.set_defaults(func=extend_budget)
    command=commands.add_parser("cancel-run"); command.add_argument("run_id"); command.add_argument("--approver",required=True); command.add_argument("--reason",required=True); command.add_argument("--request-id",required=True); command.set_defaults(func=cancel_run)
    command=commands.add_parser("status"); command.add_argument("run_id"); command.add_argument("--json",action="store_true"); command.set_defaults(func=status)
    return result


if __name__ == "__main__":
    args=parser().parse_args(); args.func(args)
