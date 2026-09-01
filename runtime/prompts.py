#!/usr/bin/env python3
"""What each agent is told, and how its answer is read back.

Every prompt the company sends lives here, so the wording an agent sees can be reviewed
in one place rather than hunted through the orchestration code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from runtime.backends import ExecutorError

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = ROOT / ".claude" / "agents"
MAX_HANDOFF_CHARS = 12000
MAX_SUBMISSION_CHARS = 40000
MIN_DELIVERABLE_CHARS = 200

VERDICTS = ("APPROVE", "RETURN", "REJECT")
VERDICT_PATTERN = re.compile(r"^\s*VERDICT:\s*(APPROVE|RETURN|REJECT)\b",
                             re.IGNORECASE | re.MULTILINE)
GRADE_PATTERN = re.compile(r"^\s*VERDICT:\s*(MEETS|FAILS)\b",
                           re.IGNORECASE | re.MULTILINE)
NOT_A_DELIVERABLE = re.compile(
    r"\b(i need more (information|detail|context)|could you (please )?(clarify|provide|"
    r"specify)|i('m| am) unable to|i cannot (do|complete|produce|help)|please (provide|"
    r"clarify|confirm)|what (would you like|should i))", re.IGNORECASE)


def parse_verdict(text: str) -> str:
    """An unreadable verdict is never an approval -- it goes back to the maker."""
    found = VERDICT_PATTERN.search(text)
    return found.group(1).upper() if found else "RETURN"


def agent_brief(owner: str) -> str:
    """The owning department's own instructions, minus YAML frontmatter."""
    path = AGENTS_DIR / f"{owner}.md"
    if not path.is_file():
        raise ExecutorError(f"no agent definition for step owner: {owner}")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip()


def clip(text: str, limit: int = MAX_HANDOFF_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... TRUNCATED at {limit} characters -- " \
        "this artifact is longer than what you were shown ...]"


def structural_failure(text: str) -> str | None:
    """Catch the common non-answer: a question or refusal where a deliverable belongs."""
    stripped = text.strip()
    if len(stripped) < MIN_DELIVERABLE_CHARS:
        return f"output is only {len(stripped)} characters -- that is not a deliverable"
    opening = stripped[:MIN_DELIVERABLE_CHARS]
    if NOT_A_DELIVERABLE.search(opening):
        return "output asks for clarification or refuses instead of delivering the work"
    return None


@dataclass(frozen=True)
class Handoff:
    """Work a directly-upstream step handed down, verified against its recorded hash."""
    step_id: str
    owner: str
    text: str


@dataclass(frozen=True)
class StepRequest:
    """A department asked to produce the deliverable for one step."""
    run_id: str
    step_id: str
    agent: str
    action: str
    goal: str
    brief: str
    handoffs: tuple[Handoff, ...] = ()
    feedback: str = ""
    remembered: tuple[str, ...] = ()
    kind = "work"

    def known(self) -> str:
        if not self.remembered:
            return ""
        lines = "\n".join(self.remembered)
        return ("What the company already learned that bears on this. Treat it as "
                f"settled unless your own evidence contradicts it:\n{lines}\n\n")

    def inbox(self) -> str:
        if not self.handoffs:
            return "No upstream work: yours is the first step on this path.\n"
        parts = ["Work handed to you by the steps yours depends on. Build on it; do not "
                 "redo it, and do not contradict it without saying why.\n"]
        for handoff in self.handoffs:
            parts.append(f"--- from {handoff.owner} (step: {handoff.step_id}) ---\n"
                         f"{handoff.text}\n")
        return "\n".join(parts)

    def rework(self) -> str:
        if not self.feedback:
            return ""
        return ("Your previous attempt at this step was returned by the checker. Their "
                "reasons are below. Fix exactly what they raised; do not start over, and "
                "do not repeat the same mistake.\n"
                f"--- checker feedback ---\n{self.feedback}\n--- end ---\n\n")

    def prompt(self) -> str:
        return (
            f"You are the {self.agent} of this company.\n"
            f"Company goal for this run: {self.goal}\n"
            f"Your step: {self.step_id} (action type: {self.action}).\n\n"
            f"{self.known()}"
            f"{self.inbox()}\n"
            f"{self.rework()}"
            "Produce the finished work product for this step as plain text. Do not ask "
            "questions, do not describe what you would do, and do not use any tools -- "
            "return the deliverable itself. It is recorded verbatim as the evidence for "
            "this step."
        )


@dataclass(frozen=True)
class CheckRequest:
    """An independent checker reviewing the maker's submission."""
    kind = "check"
    run_id: str
    step_id: str
    agent: str
    maker: str
    goal: str
    brief: str
    submission: str

    def prompt(self) -> str:
        return (
            f"You are the {self.agent} of this company, acting as the independent "
            f"checker for step {self.step_id}.\n"
            f"Company goal for this run: {self.goal}\n\n"
            f"--- submission from {self.maker} ---\n{self.submission}\n--- end ---\n\n"
            "Judge it against what the maker was actually able to do. The maker had no "
            "tools: it could not run code, execute tests, or read the repository, and "
            "neither can you. So judge the document in front of you on its own merits. "
            "Do not withhold approval because nothing was executed -- execution is "
            "outside this step's authority, not a defect in the work.\n\n"
            "Begin your reply with exactly one line:\n"
            f"VERDICT: {' or '.join(VERDICTS)}\n"
            "APPROVE if the document meets the goal. RETURN if it needs rework you can "
            "name and the maker could actually do. REJECT only if it cannot be salvaged. "
            "After that line, give your reasons as plain text. Do not use any tools."
        )


@dataclass(frozen=True)
class GradeRequest:
    """Scores a deliverable against the acceptance criteria its step declared."""
    kind = "grade"
    step_id: str
    agent: str
    goal: str
    brief: str
    criteria: tuple[str, ...]
    deliverable: str

    def prompt(self) -> str:
        listed = "\n".join(f"{n}. {c}" for n, c in enumerate(self.criteria, 1))
        return (
            f"Score this deliverable for step {self.step_id} against its acceptance "
            f"criteria. Company goal: {self.goal}\n\n"
            f"--- acceptance criteria ---\n{listed}\n\n"
            f"--- deliverable ---\n{self.deliverable}\n--- end ---\n\n"
            "Begin your reply with exactly one line:\n"
            "VERDICT: MEETS or FAILS\n"
            "MEETS only if every criterion is satisfied by what is actually written. "
            "If it FAILS, name the criteria it misses and what would fix them. The "
            "author had no tools and could not run anything, so do not require "
            "execution. Do not use any tools."
        )
