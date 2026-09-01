#!/usr/bin/env python3
"""What a department may touch while it works, and the room it works in.

Until now every step ran with `--allowedTools ""`, so a department could only write prose
about work: no file read, no artifact produced, nothing a person could open. Turning tools
on is the difference between describing a reconciliation and doing one -- but done
carelessly it also hands every agent the ability to rewrite the runtime, the audit log and
the run state, which are the only reasons to trust any of its output.

Two facts, measured against the CLI rather than assumed (cycle E of the REV2 audit):

* a bare ``Read`` grant reads anything on the machine, this repository included;
* ``Read(./**)`` with ``--permission-mode dontAsk`` denies anything outside the working
  directory and allows what is inside it, and says why it refused.

So the workspace is a boundary only when the grant is scoped to it. Every rule here is,
and this module refuses to load a grant file where one is not.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRANTS_PATH = ROOT / "runtime" / "tools.json"
WORKSPACES = Path(os.environ.get("MYORG_WORKSPACES", ROOT / "runtime" / "workspaces"))
ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

# Tools no department gets, with the reason attached so a future change is a decision
# rather than an oversight.
UNGRANTABLE = {
    "Bash": "scoped by command, never by path -- no workspace can bound it",
    "WebFetch": "reaches outward; belongs behind a connector with admission control",
    "WebSearch": "reaches outward; belongs behind a connector with admission control",
    "Task": "spawns work the runtime did not plan and cannot govern",
    "Agent": "spawns work the runtime did not plan and cannot govern",
}
MAX_MANIFEST_FILES = 50
# The CLI writes its own project-memory file into whatever folder it runs in. It is the
# harness talking to itself, not the department's work, and reporting it as a deliverable
# would put a file the agent never wrote into the evidence.
HARNESS_ARTIFACTS = {"CLAUDE.md", "CLAUDE.local.md"}


@dataclass(frozen=True)
class Grant:
    """One department's tools, and the rules that keep them in their own room."""
    tools: tuple[str, ...]
    allow: tuple[str, ...]


def _grant(value: dict, where: str) -> Grant:
    names = value.get("tools")
    rules = value.get("allow")
    if not isinstance(names, list) or not isinstance(rules, list) or not names or not rules:
        raise SystemExit(f"{where}: a grant needs a non-empty tools list and allow list")
    for name in names:
        if name in UNGRANTABLE:
            raise SystemExit(f"{where}: {name} cannot be granted -- {UNGRANTABLE[name]}")
    for rule in rules:
        if "(" not in rule or not rule.split("(", 1)[1].startswith("./"):
            raise SystemExit(
                f"{where}: '{rule}' is not scoped to the workspace. An unscoped rule "
                f"reaches the whole machine, including this repository.")
    return Grant(tuple(names), tuple(rules))


@dataclass(frozen=True)
class Grants:
    default: Grant
    roles: dict[str, Grant]

    def for_role(self, role: str) -> Grant:
        return self.roles.get(role, self.default)


def load(data: dict) -> Grants:
    """Parse and check a grant document. Refuses rather than granting something wider."""
    if data.get("version") != 1:
        raise SystemExit("unsupported tool-grant version")
    default = _grant(data.get("default") or {}, "default")
    roles = {name: _grant(value, name) for name, value in (data.get("roles") or {}).items()}
    return Grants(default, roles)


def grants() -> Grants:
    try:
        return load(json.loads(GRANTS_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read tool grants: {error}") from error


def grant_for(role: str) -> Grant:
    return grants().for_role(role)


def roles() -> list[str]:
    """Every department the runtime knows about, whether or not it overrides the default."""
    return sorted(path.stem for path in (ROOT / ".claude" / "agents").glob("*.md"))


def workspace(run_id: str, step_id: str) -> Path:
    """The room one step works in. Created on demand, never the repository itself."""
    for value in (run_id, step_id):
        if not ID_RE.fullmatch(str(value)):
            raise SystemExit(f"invalid workspace id: {value!r}")
    path = WORKSPACES / run_id / step_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def produced_files(room: Path) -> list[dict]:
    """Everything the agent left behind, hashed, so the work can be checked."""
    found = []
    for path in sorted(p for p in room.rglob("*") if p.is_file()):
        relative = path.relative_to(room)
        if relative.as_posix() in HARNESS_ARTIFACTS or relative.parts[0].startswith("."):
            continue
        data = path.read_bytes()
        found.append({"path": relative.as_posix(),
                      "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    return found


def manifest(files: list[dict]) -> str:
    """The part of the evidence a person reads to see what was actually produced."""
    if not files:
        return "## Files produced\n\nNo files were produced; the deliverable is the text above.\n"
    lines = ["## Files produced", "", "| file | bytes | sha256 |", "|---|---|---|"]
    for item in files[:MAX_MANIFEST_FILES]:
        lines.append(f"| {item['path']} | {item['bytes']} | {item['sha256']} |")
    if len(files) > MAX_MANIFEST_FILES:
        lines.append(f"| … | | {len(files) - MAX_MANIFEST_FILES} more not listed |")
    return "\n".join(lines) + "\n"
