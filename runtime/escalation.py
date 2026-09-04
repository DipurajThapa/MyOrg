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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.health import FAILED, FINISHED, STALLED, WAITING, all_health  # noqa: E402
from runtime.notify import (CALL_APPROVAL, CALL_UNRESOLVED, IDEA_FAILED,  # noqa: E402
                            IDEA_STUCK, LESSON_PROPOSED, NEEDS_APPROVAL, RUN_COMPLETED,
                            RUN_FAILED, RUN_STALLED, outstanding, raise_notice, render)

# Mirrors health.STALLED_AFTER_MINUTES: the same span of nothing happening is the same
# kind of news, whether the work is a run that stopped moving or a request that cannot
# start. One number to reason about, not two.
STUCK_AFTER_MINUTES = 30


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


DEAD_END = {
    "blocked_retry_limit": "tried its allowed attempts and failed every time",
    "blocked_review_limit": "was returned by its checker too many times",
    "blocked_cycle_limit": "ran out of its cycle budget",
    "rejected": "was rejected at a human gate",
    "rejected_by_checker": "was rejected by its checker",
    "blocked_human": "reached an action that is never automated",
    "cancelled": "was stopped by a person",
}


# What to do about it, keyed the same way. `DEAD_END` says what happened; this says what is
# left to try. They sit together because a notice and the board must not diverge -- the board
# reads these, and so does the notice below.
NEXT_STEP = {
    "blocked_retry_limit": "Every attempt failed the same way, so repeating it will not help. "
                           "Read the step's last failure, then ask again with a narrower goal.",
    "blocked_review_limit": "The checker kept sending it back. Read its last verdict: the "
                            "acceptance criteria are usually asking for more than one step can do.",
    "blocked_cycle_limit": "It ran out of cycle budget. A person can extend it with "
                           "`python -m runtime.company_runtime extend-budget`; there is no "
                           "button for this yet.",
    "rejected": "A person declined this at the gate. Nothing further happens unless it is "
                "asked for again.",
    "rejected_by_checker": "The checker refused the work outright. The workflow needs "
                           "changing, not repeating.",
    "blocked_human": "This action is never automated. Do it yourself, outside the system.",
    "cancelled": "Stopped on purpose. Ask again if it is still wanted.",
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
            NEXT_STEP.get(run.runtime_status,
                          "Read the run, then start a new one or change the workflow."),
            org_id=org, run_id=run.run_id)
        if notice:
            raised.append(notice)
    elif run.state == WAITING:
        for step_id in run.waiting_on:
            notice = raise_notice(
                NEEDS_APPROVAL, f"{run.run_id} is waiting on your decision",
                f"Step {step_id} needs a person. {run.done} of {run.total} done.",
                "Open the Control Center and approve or reject it.",
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
    elif run.state == FINISHED:
        # The one piece of good news this scan reports. Everything else here is a thing
        # going wrong, which left the company able to tell somebody their request had died
        # and unable to tell them it had worked.
        notice = raise_notice(
            RUN_COMPLETED, f"{run.run_id} finished",
            f"“{run.goal}” is done. All {run.total} step(s) completed.",
            "Open the board and read what it produced.",
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
        "Open the Control Center's queue and keep or discard them.")
    return [notice] if notice else []


def escalate_ideas() -> list:
    """Work that was asked for and gave up before it ever became a run.

    Every other dead end here is a *run*, and a trigger that never planned has none -- so
    it fell through every check and was abandoned in silence: three planning attempts of
    real money spent, the person who asked never told, and nothing on any screen once the
    row left the queue. The failure is the operator's to see, not the planner's to bury.
    """
    try:
        from runtime.projection import DB_ENV, default_db
        import os
        if DB_ENV not in os.environ and not default_db().is_file():
            return []  # no store configured; there are no triggers to look at
        from runtime.db import Store
        store = Store(default_db())
        rows = store.failed_triggers()
    except Exception:  # noqa: BLE001 - escalation must never stop the driver
        return []
    raised = []
    for row in rows:
        notice = raise_notice(
            IDEA_FAILED, f"\"{row['goal'][:80]}\" could not be planned",
            f"Asked for by {row['source']} and given up on after {row['attempts']} "
            f"attempt(s). Last error: {(row['last_error'] or 'unrecorded').strip()[:300]}",
            "Reword it and ask again, or fix what the error names.",
            org_id=row["org_id"], run_id=row["id"])
        if notice:
            raised.append(notice)
    raised.extend(escalate_stuck_ideas(store))
    return raised


def escalate_stuck_ideas(store) -> list:
    """Work that keeps retrying because its failure is supposed to be temporary.

    A transient failure deliberately spends none of an idea's three attempts, so it retries
    every sweep for as long as the other end stays busy. That is right for a bad minute and
    silent forever during a real outage -- the operator sees a queued row and no reason to
    think anything is wrong. After `STUCK_AFTER_MINUTES` the retrying itself is the news.
    """
    cutoff = (utc_now() - timedelta(minutes=STUCK_AFTER_MINUTES)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    try:
        rows = store.stuck_triggers(cutoff)
    except Exception:  # noqa: BLE001 - escalation must never stop the driver
        return []
    raised = []
    for row in rows:
        notice = raise_notice(
            IDEA_STUCK, f"\"{row['goal'][:80]}\" has been retrying for a while",
            f"Asked for by {row['source']} at {row['created_at']} and still not planned. "
            f"It is being retried and has spent {row['attempts']} of its attempts, because "
            f"the failure looks temporary. Last error: "
            f"{(row['last_error'] or 'unrecorded').strip()[:300]}",
            "If the cause has cleared it will start on its own; if not, stop waiting on it.",
            org_id=row["org_id"], run_id=row["id"])
        if notice:
            raised.append(notice)
    return raised


def escalate_connector_calls() -> list:
    """The two things the connector gate can be waiting on, neither of which was ever raised.

    An outward call proposed and undecided is the strictest gate in this company, and it
    expires -- so silence here means the decision is missed rather than delayed. A call that
    left and never settled is worse: nothing may retry it, because nobody knows whether it
    happened, and only a person can go and look.
    """
    try:
        from runtime.projection import DB_ENV, default_db
        import os
        if DB_ENV not in os.environ and not default_db().is_file():
            return []  # no store configured; there are no connectors to look at
        from runtime.db import Store
        store = Store(default_db())
        waiting = store.all_pending_approvals()
        unresolved = store.all_in_flight_receipts()
    except Exception:  # noqa: BLE001 - escalation must never stop the driver
        return []
    raised = []
    for row in waiting:
        notice = raise_notice(
            CALL_APPROVAL, f"{row['action']} to {row['target_ref']} is waiting for you",
            f"{row['requested_by']} proposed it for run {row['run_id']}, sending "
            f"{row['payload_ref']}. It can no longer be approved after {row['expires_at']}.",
            "Open the board and approve or reject it.",
            org_id=row["org_id"], run_id=row["run_id"], step_id=row["id"])
        if notice:
            raised.append(notice)
    for row in unresolved:
        notice = raise_notice(
            CALL_UNRESOLVED, f"a call to {row['connector_id']} never came back",
            f"Receipt {row['id']} left at {row['created_at']} and never settled: "
            f"{(row['outcome_note'] or 'no answer was recorded').strip()[:200]}. "
            "Nobody knows whether it happened, so nothing will retry it.",
            "Check the other end, then record what you found with `reconcile`.",
            org_id=row["org_id"], run_id=row["id"])
        if notice:
            raised.append(notice)
    return raised


def scan(log=print) -> list:
    """Look over the whole company and raise whatever a person needs to know."""
    raised = []
    for run in all_health():
        raised.extend(escalate_run(run))
    raised.extend(escalate_memory())
    raised.extend(escalate_ideas())
    raised.extend(escalate_connector_calls())
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
