#!/usr/bin/env python3
"""Noticing that something has stopped, and saying so.

The runtime already has terminal states -- blocked_retry_limit, rejected_by_checker,
blocked_review_limit. What it never had was anyone to tell. A run could exhaust its
retries at 2am and simply sit there. This turns those dead ends, and anything parked on
a human, into notices somebody will actually see.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.health import FAILED, STALLED, WAITING, all_health  # noqa: E402
from runtime.notify import (LESSON_PROPOSED, NEEDS_APPROVAL, RUN_FAILED,  # noqa: E402
                            RUN_STALLED, outstanding, raise_notice, render)

DEAD_END = {
    "blocked_retry_limit": "tried its allowed attempts and failed every time",
    "blocked_review_limit": "was returned by its checker too many times",
    "blocked_cycle_limit": "ran out of its cycle budget",
    "rejected": "was rejected at a human gate",
    "rejected_by_checker": "was rejected by its checker",
    "blocked_human": "reached an action that is never automated",
}


def run_org(run_id: str) -> str:
    from runtime.executor import current_state
    try:
        return current_state(run_id).get("org_id", "")
    except SystemExit:
        return ""


def escalate_run(run) -> list:
    """One run's worth of "somebody is needed"."""
    raised = []
    org = run_org(run.run_id)
    if run.state == FAILED:
        reason = DEAD_END.get(run.runtime_status, run.detail)
        notice = raise_notice(
            RUN_FAILED, f"{run.run_id} has stopped and cannot continue",
            f"It {reason}. {run.done} of {run.total} steps finished.",
            "Read the run, then start a new one or change the workflow.",
            org_id=org, run_id=run.run_id)
        if notice:
            raised.append(notice)
    elif run.state == WAITING:
        for step_id in run.waiting_on:
            notice = raise_notice(
                NEEDS_APPROVAL, f"{run.run_id} is waiting on your decision",
                f"Step {step_id} needs a person. {run.done} of {run.total} done.",
                "Open the approvals console and approve or reject it.",
                org_id=org, run_id=run.run_id, step_id=step_id)
            if notice:
                raised.append(notice)
    elif run.state == STALLED:
        notice = raise_notice(
            RUN_STALLED, f"{run.run_id} has gone quiet",
            run.detail, "Check whether the driver is still running.",
            org_id=org, run_id=run.run_id)
        if notice:
            raised.append(notice)
    return raised


def escalate_memory() -> list:
    """Lessons an agent wants kept are worthless until somebody rules on them."""
    try:
        from runtime.memory import proposals
        waiting = proposals()
    except SystemExit:
        return []
    if not waiting:
        return []
    notice = raise_notice(
        LESSON_PROPOSED, f"{len(waiting)} lesson(s) waiting to be remembered",
        "; ".join(entry.subject for entry in waiting[:3]),
        "Open the approvals console and keep or discard them.")
    return [notice] if notice else []


def scan(log=print) -> list:
    """Look over the whole company and raise whatever a person needs to know."""
    raised = []
    for run in all_health():
        raised.extend(escalate_run(run))
    raised.extend(escalate_memory())
    if raised:
        log(f"escalated {len(raised)} new notice(s)")
    return raised


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deliver", action="store_true",
                        help="also hand notices to the configured delivery command")
    args = parser.parse_args(argv)
    scan()
    if args.deliver:
        from runtime.notify import deliver
        deliver()
    print(render(outstanding()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
