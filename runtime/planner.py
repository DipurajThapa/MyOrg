#!/usr/bin/env python3
"""Turns a goal in plain words into a workflow the runtime will accept.

The Chief of Staff decomposes the goal into owned, dependency-ordered steps. Whatever
comes back is validated against the real schema, and any errors are handed straight back
for repair, so nothing reaches disk that `create-run` would reject.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime import tools as _tools  # noqa: E402
from runtime.backends import is_transient  # noqa: E402
from runtime.executor import (BACKENDS, ClaudeCliBackend, ExecutorError,  # noqa: E402
                              agent_brief)

PLANNER = "chief-of-staff"
# Which departments can actually reach the outside world. The planner has to know, or
# it writes 'cite your sources' for a department working in an empty folder -- which it
# did, three runs in a row.
SEARCHERS = {role for role in _tools.roles() if _tools.reaches_outward(_tools.grant_for(role))}
MAX_REPAIR_ATTEMPTS = 3
CYCLES_PER_STEP = 4
MAX_CYCLES_CEILING = 100
JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def departments() -> list[str]:
    return sorted(path.stem for path in (ROOT / ".claude" / "agents").glob("*.md"))


def actions_by_risk() -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for action, risk in core.policy().items():
        grouped.setdefault(risk, []).append(action)
    return {risk: sorted(names) for risk, names in grouped.items()}


@dataclass(frozen=True)
class PlanRequest:
    """Asks the Chief of Staff to decompose a goal into a runnable workflow."""
    kind = "plan"
    agent: str
    goal: str
    workflow_id: str
    brief: str
    feedback: str = ""

    def rules(self) -> str:
        risks = actions_by_risk()
        return (
            f"Owners must be exactly one of: {', '.join(departments())}\n"
            f"Actions must be exactly one of these, and the risk word decides who may "
            f"run the step:\n"
            f"  green (an agent runs it alone): {', '.join(risks.get('green', []))}\n"
            f"  yellow (stops for a human): {', '.join(risks.get('yellow', []))}\n"
            f"  red (never automated): {', '.join(risks.get('red', []))}\n"
            "Rules the runtime enforces and will reject you for breaking:\n"
            "- step ids are lowercase slugs, unique, and may only use a-z 0-9 and -\n"
            "- max_attempts is 1..5 on every step\n"
            "- depends_on lists earlier step ids only; no cycles; no self-reference\n"
            "- a `checker` is optional, must be a different department from the owner, "
            "and may only sit on a green step; with it you must set max_review_cycles "
            "1..3 and max_attempts at least max_review_cycles + 2\n"
            "- `acceptance` is a list of short, checkable statements about the finished "
            "work; give 2-3 for every green step\n"
            "\n"
            "Acceptance criteria decide whether the work is accepted, so write ones a "
            "single agent can actually meet in one step:\n"
            f"- Only {', '.join(sorted(SEARCHERS))} can search the web. Every other "
            "department works from what earlier steps handed it and from the files in its "
            "own folder. Never ask a department for sources, prices, market data or "
            "current facts it has no way to obtain.\n"
            "- No step can run a command, open a URL you name, or reach a company system.\n"
            "- Put a number on anything you want counted, and keep it small enough to do "
            "well: 'the 3 largest competitors, each with a dated source' beats 'all "
            "competitors'.\n"
            "- Never write 'every', 'all', 'each and every', 'no claim without ...' or "
            "'fully'. An unbounded criterion cannot be satisfied and cannot be graded: a "
            "real run demanded 'every claim carries a dated source link' across ten "
            "products and failed three times on one missing price.\n"
            "- Ask for the work, not for perfection. If you want depth, say how deep on "
            "how many items, and let the rest be a list.\n"
            "\n"
            "Budget `max_attempts` for what really happens to a step:\n"
            "- An attempt is one go at the work. A step is graded against its own "
            "acceptance criteria first, and a rejected attempt is spent -- research and "
            "analysis often take two or three goes to satisfy their criteria before a "
            "checker ever sees them.\n"
            "- A `checker` then reviews, and every return costs another attempt.\n"
            "- So a checked step needs its review cycles plus room to be graded: "
            "`max_attempts = max_review_cycles + 2` is the floor the runtime enforces, "
            "and 4 or 5 is right for research a checker will scrutinise. The floor is a "
            "bare minimum, not a target: at exactly the floor a step gets one go at the "
            "work, one spare for a grader rejection, and its review returns, with nothing "
            "left over. A real run set 3 attempts against 2 review cycles, spent all "
            "three failing the grader on citations, and took the 25 steps waiting behind "
            "it down with it.\n"
            "- Give an unchecked step 2 or 3. One means a single bad answer ends it.\n"
            "\n"
            "`depends_on` is what decides how fast this runs. Steps with nothing left to "
            "wait for are dispatched together, so the shape of the graph is the schedule:\n"
            "- List a dependency only where the step genuinely needs that step's *output*. "
            "'It feels later' is not a dependency, and a chain of eight steps each waiting "
            "on the one before takes eight times as long as the work needs.\n"
            "- Independent research, retrieval and analysis belong side by side with the "
            "same (or no) dependencies -- several scans, several data pulls, several "
            "drafts for different departments.\n"
            "- Join them where the work genuinely merges: one step that depends on all the "
            "branches, which is where their findings get reconciled.\n"
            "- Do not split one piece of work across branches to look parallel. Two steps "
            "researching the same thing pay twice and then disagree.\n"
            "- The gated step goes last and depends on what it is about to act on.\n"
        )

    def repair(self) -> str:
        if not self.feedback:
            return ""
        return ("Your previous plan was rejected by the runtime. Fix exactly these "
                f"errors:\n{self.feedback}\n\n")

    def prompt(self) -> str:
        return (
            "You are the Chief of Staff. Turn this goal into a workflow your company can "
            "actually run.\n\n"
            f"GOAL: {self.goal}\n\n"
            f"{self.repair()}"
            f"{self.rules()}\n"
            "Think about which department genuinely owns each piece of work, what order "
            "the work has to happen in, and where a second pair of eyes is worth the "
            "delay. Put any step that sends, publishes, buys, signs, or changes settings "
            "last and give it a yellow action, so a human decides it.\n\n"
            'Reply with JSON only -- no prose, no code fences. Shape:\n'
            '{"version":1,"id":"' + self.workflow_id + '","goal":"...","max_cycles":N,'
            '"steps":[{"id":"...","owner":"...","action":"...","depends_on":[],'
            '"max_attempts":2,"acceptance":["...","..."]}]}\n'
            "Do not use any tools."
        )


class StubPlannerBackend:
    """Deterministic planner for tests. Never calls a model."""

    def __call__(self, request) -> str:
        return json.dumps({
            "version": 1, "id": request.workflow_id, "goal": request.goal,
            "max_cycles": 12,
            "steps": [
                {"id": "frame-goal", "owner": "chief-of-staff", "action": "analyze",
                 "depends_on": [], "max_attempts": 2,
                 "acceptance": ["states the outcome", "names the owner"]},
                {"id": "produce-output", "owner": "cto-engineering",
                 "action": "internal_write", "depends_on": ["frame-goal"],
                 "max_attempts": 2, "acceptance": ["answers the goal"]},
                {"id": "release-output", "owner": "chief-of-staff", "action": "publish",
                 "depends_on": ["produce-output"], "max_attempts": 1},
            ],
        })


def extract_json(text: str) -> dict:
    """Models like to wrap JSON in prose or fences; take the outermost object."""
    found = JSON_BLOCK.search(text)
    if not found:
        raise ExecutorError("planner returned no JSON object")
    try:
        return json.loads(found.group(0))
    except json.JSONDecodeError as error:
        raise ExecutorError(f"planner returned invalid JSON: {error}") from error


def enforce_budget(workflow: dict) -> None:
    """A plan that cannot finish inside its own cycle budget is a plan that deadlocks."""
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps:
        return
    needed = min(len(steps) * CYCLES_PER_STEP, MAX_CYCLES_CEILING)
    if not isinstance(workflow.get("max_cycles"), int) or workflow["max_cycles"] < needed:
        workflow["max_cycles"] = needed


def validation_errors(workflow: dict) -> str:
    try:
        core.validate_workflow(workflow)
    except SystemExit as error:
        return str(error)
    return ""


def plan(goal: str, workflow_id: str, backend,
         attempts: int = MAX_REPAIR_ATTEMPTS, log=print, costs: list | None = None) -> dict:
    """Ask for a workflow, and keep handing back the runtime's own errors until valid.

    `costs` collects what each attempt cost, the way `acceptance_failure` does (B-04): up to
    `attempts` model calls happen before any run exists to charge them to, so the caller
    seeds the run with the sum."""
    feedback = ""
    for attempt in range(1, attempts + 1):
        request = PlanRequest(agent=PLANNER, goal=goal, workflow_id=workflow_id,
                              brief=agent_brief(PLANNER), feedback=feedback)
        try:
            answer = backend(request)
            if costs is not None:
                costs.append(float(getattr(answer, "cost_usd", 0.0) or 0.0))
            workflow = extract_json(answer)
        except ExecutorError as error:
            # Repair attempts exist to tell the model its JSON was wrong. A busy server is
            # not a wrong answer, and handing "API Error: 529 Overloaded" back as feedback
            # spends the whole repair budget inside one outage -- which is what threw away a
            # real request. Stop and let the caller decide when to try again.
            if is_transient(str(error)):
                log(f"  attempt {attempt}: {error}")
                raise
            feedback = str(error)
            log(f"  attempt {attempt}: {error}")
            continue
        workflow["id"] = workflow_id
        # And the goal, for the same reason the id is forced: the caller owns it, the model
        # does not. Whatever it writes here is what `run.created` records, what the runs
        # list shows, and what a human reads on the screen where they approve an outward
        # action -- so a paraphrase quietly changes what the operator is told they asked
        # for, and the idea they typed and the run it became stop saying the same thing.
        workflow["goal"] = goal
        enforce_budget(workflow)
        feedback = validation_errors(workflow)
        if not feedback:
            log(f"  attempt {attempt}: valid plan with {len(workflow['steps'])} steps")
            return workflow
        log(f"  attempt {attempt}: rejected by the runtime -- {feedback.splitlines()[0]}")
    raise ExecutorError(f"no valid plan after {attempts} attempts; last errors:\n{feedback}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("goal")
    parser.add_argument("--id", required=True, help="workflow id (lowercase slug)")
    parser.add_argument("--out", help="where to write it (default: runtime/workflows/<id>.json)")
    parser.add_argument("--backend", choices=sorted(BACKENDS) + ["stub-planner"],
                        default="claude")
    parser.add_argument("--model")
    parser.add_argument("--attempts", type=int, default=MAX_REPAIR_ATTEMPTS)
    args = parser.parse_args(argv)

    if not core.ID_RE.fullmatch(args.id):
        print("workflow id must be a lowercase slug", file=sys.stderr)
        return 1
    backend = (StubPlannerBackend() if args.backend == "stub-planner"
               else ClaudeCliBackend(args.model))
    try:
        workflow = plan(args.goal, args.id, backend, args.attempts)
    except ExecutorError as error:
        print(f"planning failed: {error}", file=sys.stderr)
        return 1

    destination = Path(args.out) if args.out else ROOT / "runtime" / "workflows" / f"{args.id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    print(destination.resolve().relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
