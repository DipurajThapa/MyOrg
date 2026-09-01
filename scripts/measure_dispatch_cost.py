"""A-09: what does a real dispatch cost, and can the floor be trimmed without hurting output?

Not a four-word prompt. This builds the exact StepRequest the executor builds -- full agent
brief, upstream evidence, a scoped tool grant and its own workspace -- and runs the exact
command `backends.py` runs, varying only the context profile.

Two numbers per profile, and the second one matters more: cost, and whether the deliverable
still passes its acceptance criteria. A cheaper dispatch that produces worse work is not a
saving.

Usage:  python a09.py [--repeats N]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\AgenticAI\MyOrg")
sys.path.insert(0, str(REPO))

from runtime import tools  # noqa: E402
from runtime.prompts import GradeRequest, Handoff, StepRequest, agent_brief  # noqa: E402

AGENT = "cfo-finance"
GOAL = "Report on Q4 marketing spend against budget and flag anything that needs a decision"
ACTION = "analyze"
CRITERIA = (
    "States the total Q4 spend and the total Q4 budget as explicit numbers",
    "States the variance in both currency and percent",
    "Names at least one line item driving the variance",
    "Every figure traces to the supplied ledger extract",
)
# Real upstream evidence, the way a previous step would have handed it over.
LEDGER = """channel,month,budget_usd,actual_usd
paid-search,2026-10,40000,44120
paid-search,2026-11,40000,51890
paid-search,2026-12,40000,58730
events,2026-10,25000,18200
events,2026-11,25000,9400
events,2026-12,25000,31500
content,2026-10,12000,11880
content,2026-11,12000,12240
content,2026-12,12000,12010
"""

PROFILES = {
    "current": [],
    "no-skills": ["--disable-slash-commands"],
    "no-plugins": ["--disable-slash-commands", "--strict-mcp-config"],
}


def build_request(workspace: Path) -> StepRequest:
    return StepRequest(
        run_id="a09-measure", step_id="variance-analysis", agent=AGENT, action=ACTION,
        goal=GOAL, brief=agent_brief(AGENT),
        handoffs=(Handoff(step_id="pull-ledger", owner="head-of-data", text=LEDGER),),
        remembered=(), workspace=workspace, grant=tools.grant_for(AGENT))


def run(command: list[str], cwd: Path, timeout: int = 300) -> dict:
    """One dispatch. Returns the CLI's own JSON, which carries total_cost_usd."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            cwd=str(cwd), check=False)
    if result.returncode != 0:
        return {"error": f"exit {result.returncode}: {result.stderr.strip()[:300]}"}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"unparseable: {result.stdout[:300]}"}


def dispatch(request: StepRequest, extra: list[str], workspace: Path) -> dict:
    """The command `backends.ClaudeCliBackend` builds, plus the profile flags and JSON out."""
    command = ["claude", "-p", request.prompt(), "--output-format", "json",
               "--append-system-prompt", request.brief,
               "--permission-mode", "dontAsk",
               "--tools", ",".join(request.grant.tools),
               "--allowedTools", *request.grant.allow] + extra
    return run(command, workspace)


def grade(deliverable: str) -> dict:
    """The product's own acceptance grader, so quality is judged the way it judges."""
    ask = GradeRequest(step_id="variance-analysis", agent=AGENT, goal=GOAL,
                       brief=agent_brief(AGENT), criteria=CRITERIA,
                       deliverable=deliverable)
    command = ["claude", "-p", ask.prompt(), "--output-format", "json",
               "--append-system-prompt", ask.brief, "--permission-mode", "dontAsk",
               "--tools", "", "--allowedTools", ""]
    return run(command, REPO)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()

    rows = []
    for name, extra in PROFILES.items():
        for attempt in range(1, args.repeats + 1):
            workspace = Path(tempfile.mkdtemp(prefix=f"a09-{name}-"))
            try:
                request = build_request(workspace)
                answer = dispatch(request, extra, workspace)
                if "error" in answer:
                    print(f"{name} #{attempt}: DISPATCH FAILED -- {answer['error']}", flush=True)
                    rows.append({"profile": name, "attempt": attempt, "error": answer["error"]})
                    continue
                usage = answer["usage"]
                deliverable = answer["result"]
                verdict = grade(deliverable)
                text = verdict.get("result", "") if "error" not in verdict else ""
                passed = text.strip().upper().startswith("VERDICT: PASS") or "PASSES" in text[:80].upper()
                files = [p.name for p in workspace.rglob("*") if p.is_file()
                         and p.name not in {"CLAUDE.md"}]
                row = {
                    "profile": name, "attempt": attempt,
                    "cost": answer["total_cost_usd"],
                    "grade_cost": verdict.get("total_cost_usd", 0.0),
                    "cache_create": usage["cache_creation_input_tokens"],
                    "cache_read": usage["cache_read_input_tokens"],
                    "out_tokens": usage["output_tokens"],
                    "chars": len(deliverable), "files": len(files),
                    "passed": passed,
                    "verdict": text.strip().splitlines()[0][:70] if text else "grader failed",
                }
                rows.append(row)
                print(f"{name} #{attempt}: ${row['cost']:.4f} "
                      f"(+${row['grade_cost']:.4f} grading) "
                      f"cache_create={row['cache_create']} out={row['out_tokens']} "
                      f"chars={row['chars']} files={row['files']} "
                      f"{'PASS' if passed else 'FAIL'} -- {row['verdict']}", flush=True)
            finally:
                shutil.rmtree(workspace, ignore_errors=True)

    print("\n--- A-09 ---------------------------------------------------------")
    print(f"{'profile':<12}{'dispatch':>10}{'grading':>10}{'total':>10}{'cache_cr':>10}{'quality':>9}")
    baseline = None
    for name in PROFILES:
        got = [r for r in rows if r["profile"] == name and "error" not in r]
        if not got:
            print(f"{name:<12}{'all failed':>10}")
            continue
        cost = sum(r["cost"] for r in got) / len(got)
        grading = sum(r["grade_cost"] for r in got) / len(got)
        cache = sum(r["cache_create"] for r in got) // len(got)
        passes = sum(1 for r in got if r["passed"])
        baseline = baseline if baseline is not None else cost
        print(f"{name:<12}${cost:>9.4f}${grading:>9.4f}${cost + grading:>9.4f}"
              f"{cache:>10}{passes:>5}/{len(got)}")
    if baseline:
        print(f"\nfloor measured earlier on a 4-token prompt: ~$0.40")
        print(f"a real dispatch on the current profile:      ${baseline:.4f}")
    json.dump(rows, open(Path(__file__).with_name("a09-results.json"), "w"), indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
