#!/usr/bin/env python3
"""The one-screen decision brief a human reads instead of the whole paper trail.

Written once, when a step parks on a human, and cached next to the run. Nobody can decide
well from 12 KB of agent prose; they can decide from five short lines and the option to
open the full work if something looks off.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402

MAX_FINDINGS = 3
MAX_LINE_CHARS = 140
FIELDS = ("ASK", "IF YES", "FINDINGS", "WATCH", "RECOMMEND")
FIELD_PATTERN = re.compile(
    r"^\s*(ASK|IF YES|FINDINGS|WATCH|RECOMMEND)\s*:\s*(.*)$", re.IGNORECASE)
BULLET = re.compile(r"^\s*[-*•]\s*(.+)$")


@dataclass(frozen=True)
class Brief:
    """What a person needs to say yes or no, and nothing else."""
    ask: str = ""
    if_yes: str = ""
    findings: tuple[str, ...] = ()
    watch: str = ""
    recommend: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.ask and self.recommend)

    @property
    def recommends_approval(self) -> bool:
        return self.recommend.strip().upper().startswith("APPROVE")

    def as_text(self) -> str:
        lines = [f"ASK: {self.ask}", f"IF YES: {self.if_yes}", "FINDINGS:"]
        lines += [f"- {item}" for item in self.findings]
        lines += [f"WATCH: {self.watch}", f"RECOMMEND: {self.recommend}"]
        return "\n".join(lines)


@dataclass(frozen=True)
class BriefRequest:
    """Asks for a decision brief, not a summary -- the difference is what gets cut."""
    kind = "brief"
    agent: str
    step_id: str
    action: str
    risk: str
    goal: str
    brief: str
    evidence: str

    def prompt(self) -> str:
        return (
            "A human has to approve or reject the step below and has about thirty "
            "seconds. Write the brief they will read. They cannot read the full work.\n\n"
            f"GOAL: {self.goal}\n"
            f"STEP: {self.step_id} -- action `{self.action}` ({self.risk} risk)\n\n"
            f"--- the work leading up to this ---\n{self.evidence}\n--- end ---\n\n"
            "Reply in exactly this shape and nothing else:\n"
            "ASK: <what they are being asked to allow, one sentence>\n"
            "IF YES: <what concretely happens next, one sentence>\n"
            "FINDINGS:\n"
            f"- <up to {MAX_FINDINGS} bullets, each under 20 words, only what changes "
            "the decision>\n"
            "WATCH: <the single biggest thing that could go wrong, one sentence>\n"
            "RECOMMEND: <APPROVE or REJECT> - <under 15 words on why>\n\n"
            "No preamble, no headings, no markdown emphasis. Plain words. If the work "
            "contains an unresolved objection, it belongs in WATCH. Do not use tools."
        )


def trim(text: str) -> str:
    collapsed = " ".join(text.split())
    return collapsed[:MAX_LINE_CHARS].rstrip()


def parse_brief(text: str) -> Brief:
    """Take what the shape gives us; a malformed brief is simply unusable, not fatal."""
    values: dict[str, str] = {}
    findings: list[str] = []
    collecting = False
    for line in text.splitlines():
        matched = FIELD_PATTERN.match(line)
        if matched:
            key = matched.group(1).upper()
            collecting = key == "FINDINGS"
            if not collecting:
                values[key] = trim(matched.group(2))
            elif matched.group(2).strip():
                findings.append(trim(matched.group(2)))
            continue
        bullet = BULLET.match(line)
        if collecting and bullet:
            findings.append(trim(bullet.group(1)))
    return Brief(ask=values.get("ASK", ""), if_yes=values.get("IF YES", ""),
                 findings=tuple(findings[:MAX_FINDINGS]), watch=values.get("WATCH", ""),
                 recommend=values.get("RECOMMEND", ""))


def brief_path(run_id: str, step_id: str) -> Path:
    return ROOT / "runtime" / "runs" / f"{run_id}.{step_id}.brief"


def load_brief(run_id: str, step_id: str) -> Brief | None:
    path = brief_path(run_id, step_id)
    if not path.is_file():
        return None
    parsed = parse_brief(path.read_text(encoding="utf-8"))
    return parsed if parsed.usable else None


def evidence_for(state: dict, step: dict, limit: int) -> str:
    """The work the decision rests on: this step's own output, else its dependencies'."""
    sources = []
    if step.get("evidence"):
        sources.append(step["evidence"])
    else:
        for dependency_id in step.get("depends_on", []):
            dependency = state["steps"].get(dependency_id, {})
            if dependency.get("evidence"):
                sources.append(dependency["evidence"])
    parts = []
    for source in sources:
        path = ROOT / source
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)[:limit]


def write_brief(run_id: str, step_id: str, state: dict, backend, limit: int) -> Brief | None:
    """Generate and cache the brief. A failure here must never block the approval.

    Known, accepted undercount (B-04): this is at most one model call per parked yellow
    step and it is not charged to the run -- the step has already made its transition by
    the time the brief is written, and a spend event of its own would cost a cycle. Every
    other call (work, grade, check, plan) is charged. Revisit if briefs become long."""
    step = state["steps"][step_id]
    evidence = evidence_for(state, step, limit)
    if not evidence.strip():
        return None
    try:
        output = backend(BriefRequest(
            agent=step["owner"], step_id=step_id, action=step["action"],
            risk=step["risk"], goal=state["goal"], evidence=evidence,
            brief="You write short, honest decision briefs for busy executives."))
    except Exception:  # noqa: BLE001 - a missing brief degrades, it does not break
        return None
    parsed = parse_brief(output)
    if not parsed.usable:
        return None
    brief_path(run_id, step_id).write_text(parsed.as_text() + "\n", encoding="utf-8")
    return parsed


__all__ = ["Brief", "BriefRequest", "brief_path", "load_brief", "parse_brief",
           "write_brief", "core"]
