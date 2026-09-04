#!/usr/bin/env python3
"""Everything waiting on a human, and the only two things a human can do about it.

The driver parks yellow steps at `awaiting_approval` and stops red ones at
`blocked_human`. This module finds them across every run, gathers enough context to
decide, and applies the decision through the runtime's own approve/reject commands.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.briefing import Brief, load_brief  # noqa: E402
from runtime.executor import (MAX_HANDOFF_CHARS, clip, namespace,  # noqa: E402
                              quietly, request_id)

WAITING = core.WAITING_STEP
RISK_REASON = {
    "yellow": "This leaves the building or cannot be undone, so a human decides it.",
    "red": "Never automated. A human must do this themselves, outside the system.",
}


@dataclass(frozen=True)
class Decision:
    """One thing waiting on a person, with what they need in order to decide."""
    run_id: str
    step_id: str
    status: str
    owner: str
    action: str
    risk: str
    goal: str
    workflow_id: str
    context: tuple[tuple[str, str], ...] = field(default=())
    brief: Brief | None = None
    held_reason: str = ""
    org_id: str = ""
    unblocks: int = 0
    depth: int = 0
    waiting_since: str = ""

    @property
    def actionable(self) -> bool:
        """Red steps are handed back entirely -- there is nothing to approve."""
        return self.status == "awaiting_approval"

    @property
    def impact(self) -> str:
        if not self.unblocks:
            return "Nothing else is waiting on this."
        return f"{self.unblocks} later step(s) cannot start until you decide."

    @property
    def reason(self) -> str:
        if self.held_reason:
            # A green step can end up here: the work is fine, the check on it could not run.
            return f"A quality check could not run, so this needs you \u2014 {self.held_reason}"
        return RISK_REASON.get(self.risk, "Held for review.")


def parked_at(events: list[dict], step_id: str) -> str:
    """When this step started waiting -- not when the run last did anything.

    `state["ts"]` is the last event of the whole *run*, so a step parked on Monday reported
    itself as waiting since whenever some sibling branch last moved. Steps that are ready
    together are driven together, so a run with a parked gate and other work still going
    refreshed its own waiting time on every pass, and the gauge for "longest anything has
    waited on a person" reset with it. Walk back while this step was still waiting.
    """
    parked = events[-1]["ts"]
    for event in reversed(events[:-1]):
        if event.get("steps", {}).get(step_id, {}).get("status") not in WAITING:
            break
        parked = event["ts"]
    return parked


def run_ids() -> list[str]:
    return [path.stem for path in core.run_files()]


def upstream_context(state: dict, step: dict) -> tuple[tuple[str, str], ...]:
    """What the agents actually produced, so the decision is not taken blind."""
    gathered = []
    for dependency_id in step.get("depends_on", []):
        dependency = state["steps"].get(dependency_id, {})
        evidence = dependency.get("evidence")
        if not evidence:
            continue
        path = ROOT / evidence
        if not path.is_file():
            continue
        gathered.append((f"{dependency_id} ({dependency['owner']})",
                         clip(path.read_text(encoding="utf-8"), MAX_HANDOFF_CHARS)))
    return tuple(gathered)


def downstream_count(state: dict, step_id: str) -> int:
    """How much work this decision is holding up, following the DAG all the way down."""
    blocked, frontier = set(), [step_id]
    while frontier:
        current = frontier.pop()
        for candidate_id, candidate in state["steps"].items():
            if current in candidate.get("depends_on", []) and candidate_id not in blocked:
                blocked.add(candidate_id)
                frontier.append(candidate_id)
    return len(blocked)


def depth_of(state: dict, step_id: str, seen: frozenset = frozenset()) -> int:
    """Position in the chain, so earlier decisions are offered before later ones."""
    if step_id in seen:
        return 0
    parents = state["steps"].get(step_id, {}).get("depends_on", [])
    if not parents:
        return 0
    return 1 + max(depth_of(state, parent, seen | {step_id}) for parent in parents)


def sequence(decisions: list[Decision]) -> list[Decision]:
    """The order these should be decided in.

    Handed-back red steps first (they stop a whole run), then whatever is earliest in
    its workflow, then whatever unblocks the most work. Within a run this always
    follows the DAG, so a decision is never offered before one it depends on.
    """
    return sorted(decisions, key=lambda d: (
        0 if not d.actionable else 1, d.depth, -d.unblocks, d.run_id, d.step_id))


def pending(run_id: str | None = None, org_id: str | None = None) -> list[Decision]:
    """Every decision waiting on a human, in the order they should be taken.

    `org_id` scopes the queue to one organization: a decision belongs to whoever owns the
    run, and nobody else should even see that it exists."""
    decisions = []
    for identifier in ([run_id] if run_id else run_ids()):
        try:
            events = core.read_events(identifier)
        except SystemExit:
            continue
        state = events[-1]
        if org_id is not None and state.get("org_id") != org_id:
            continue
        for step_id, step in sorted(state["steps"].items()):
            if step["status"] not in WAITING:
                continue
            decisions.append(Decision(
                run_id=identifier, step_id=step_id, status=step["status"],
                owner=step["owner"], action=step["action"], risk=step["risk"],
                goal=state["goal"], workflow_id=state["workflow_id"],
                context=upstream_context(state, step),
                brief=load_brief(identifier, step_id),
                held_reason=step.get("held_reason", ""),
                org_id=state.get("org_id", ""),
                unblocks=downstream_count(state, step_id),
                depth=depth_of(state, step_id),
                waiting_since=parked_at(events, step_id)))
    return sequence(decisions)


def decide(run_id: str, step_id: str, approve: bool, approver: str, note: str) -> str:
    """Apply a human's decision. The runtime, not this module, enforces the rules."""
    if not approver.strip():
        raise SystemExit("an approver name is required -- decisions are attributable")
    if not note.strip():
        raise SystemExit("a reason is required -- it becomes the approval reference")
    command = core.approve if approve else core.reject
    return quietly(command, namespace(
        run_id=run_id, step=step_id, approver=approver.strip(),
        approval_ref=note.strip(),
        request_id=request_id(run_id, step_id, "approve" if approve else "reject")))


def render(decisions: list[Decision]) -> str:
    if not decisions:
        return "Nothing is waiting on you."
    lines = [f"{len(decisions)} decision(s) waiting on you:", ""]
    for decision in decisions:
        verb = "APPROVE/REJECT" if decision.actionable else "HANDED BACK (do it yourself)"
        lines += [f"  {decision.run_id} / {decision.step_id}",
                  f"    what:  {decision.action} ({decision.risk})",
                  f"    who:   {decision.owner}",
                  f"    goal:  {decision.goal}",
                  f"    why:   {decision.reason}",
                  f"    you:   {verb}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list", help="show everything waiting on a human")
    listing.add_argument("--run-id")

    for name in ("approve", "reject"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        command.add_argument("step")
        command.add_argument("--approver", required=True)
        command.add_argument("--note", required=True, help="why -- recorded as the reference")

    args = parser.parse_args(argv)
    if args.command == "list":
        print(render(pending(args.run_id)))
        return 0
    print(decide(args.run_id, args.step, args.command == "approve",
                 args.approver, args.note))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
