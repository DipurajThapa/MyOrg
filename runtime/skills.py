#!/usr/bin/env python3
"""Which capabilities this company actually has, and which it only claims.

Every department lists the skills it wields. Some of those live in this repository; the
rest come from the environment the agents run inside. Nothing here guesses: a reference
resolves to a real `SKILL.md`, or it is declared as an external dependency, or it is
reported as unresolved. A skill nobody can point at is a claim, not a capability.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

AGENTS_DIR = ROOT / ".claude" / "agents"
SKILLS_DIR = ROOT / ".claude" / "skills"
MANIFEST = ROOT / "company" / "skills.manifest.json"
SKILLS_HEADING = re.compile(r"^##\s+Skills you wield\s*$", re.MULTILINE)
NEXT_HEADING = re.compile(r"^##\s+", re.MULTILINE)
REFERENCE = re.compile(r"`([a-z0-9][a-z0-9:_-]*)`")
LOCAL = "local"
DECLARED = "declared"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class Skill:
    """One capability a department says it can use."""
    reference: str
    resolution: str
    family: str
    provider: str = ""

    @property
    def usable(self) -> bool:
        """Resolvable today. `declared` means we know where it comes from, not that
        it has been executed here -- that stays an open question until it runs."""
        return self.resolution in (LOCAL, DECLARED)


def family_of(reference: str) -> str:
    """Skills group by namespace; ungrouped ones are their own small family."""
    return reference.split(":", 1)[0] if ":" in reference else "ungrouped"


def local_skills() -> set[str]:
    return {path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md")}


def load_manifest() -> dict:
    if not MANIFEST.is_file():
        return {"version": 1, "skills": {}}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def declared_skills() -> dict[str, dict]:
    return load_manifest().get("skills", {})


def declared_by(agent: str) -> list[str]:
    """The skill references one department lists, in the order it lists them."""
    path = AGENTS_DIR / f"{agent}.md"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    found = SKILLS_HEADING.search(text)
    if not found:
        return []
    rest = text[found.end():]
    end = NEXT_HEADING.search(rest)
    block = rest[:end.start()] if end else rest
    # Departments get named in this block as partners ("with `chro-people`"); they are
    # colleagues, not capabilities.
    colleagues = {path.stem for path in AGENTS_DIR.glob("*.md")}
    return [reference for reference in dict.fromkeys(REFERENCE.findall(block))
            if reference not in colleagues]


def departments() -> list[str]:
    return sorted(path.stem for path in AGENTS_DIR.glob("*.md"))


def resolve(reference: str, local: set[str] | None = None,
            manifest: dict[str, dict] | None = None) -> Skill:
    """A reference is local, an explicitly declared external dependency, or unresolved."""
    available = local_skills() if local is None else local
    known = declared_skills() if manifest is None else manifest
    bare = reference.split(":", 1)[-1]
    if reference in available or bare in available:
        return Skill(reference, LOCAL, family_of(reference), "in-repo")
    if reference in known:
        entry = known[reference]
        return Skill(reference, DECLARED, entry.get("family", family_of(reference)),
                     entry.get("provider", ""))
    return Skill(reference, UNRESOLVED, family_of(reference))


def audit() -> dict[str, list[Skill]]:
    """Every department's declared skills, resolved. The whole picture in one call."""
    available, known = local_skills(), declared_skills()
    return {agent: [resolve(reference, available, known)
                    for reference in declared_by(agent)]
            for agent in departments()}


def unresolved(result: dict[str, list[Skill]] | None = None) -> dict[str, set[str]]:
    """What is missing, grouped into families rather than listed one by one."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for skills in (result or audit()).values():
        for skill in skills:
            if skill.resolution == UNRESOLVED:
                grouped[skill.family].add(skill.reference)
    return dict(grouped)


def summary(result: dict[str, list[Skill]] | None = None) -> dict:
    found = result or audit()
    every = [skill for skills in found.values() for skill in skills]
    unique = {skill.reference: skill for skill in every}
    counts: dict[str, int] = defaultdict(int)
    for skill in unique.values():
        counts[skill.resolution] += 1
    return {"departments": len(found), "references": len(every),
            "distinct": len(unique), **counts,
            "families_missing": len(unresolved(found))}


def render(result: dict[str, list[Skill]] | None = None) -> str:
    found = result or audit()
    lines = []
    for agent, skills in found.items():
        missing = [s.reference for s in skills if s.resolution == UNRESOLVED]
        mark = "!" if missing else " "
        usable = sum(1 for s in skills if s.usable)
        lines.append(f"{mark} {agent:<26} {usable}/{len(skills)} usable")
        if missing:
            lines.append(f"{'':>4}missing: {', '.join(sorted(missing))}")
    totals = summary(found)
    lines += ["", f"{totals['distinct']} distinct skills across "
                  f"{totals['departments']} departments: "
                  f"{totals.get(LOCAL, 0)} in this repo, "
                  f"{totals.get(DECLARED, 0)} declared external, "
                  f"{totals.get(UNRESOLVED, 0)} unresolved."]
    families = unresolved(found)
    if families:
        lines.append("")
        lines.append("Unresolved, grouped into families to build or declare:")
        for family, references in sorted(families.items(),
                                         key=lambda item: (-len(item[1]), item[0])):
            lines.append(f"  {family:<22} {len(references):>3}  "
                         f"{', '.join(sorted(references)[:4])}"
                         f"{' ...' if len(references) > 4 else ''}")
    return "\n".join(lines)


TOOL_PATH = re.compile(r"`((?:runtime|scripts|tests)/[A-Za-z0-9_./-]+\.(?:py|sh))`")


@dataclass(frozen=True)
class Tool:
    """A command a skill says it runs. Either it is there, or the skill is prose."""
    skill: str
    path: str
    exists: bool

    @property
    def bound(self) -> bool:
        return self.exists


def tools_of(skill: str) -> list[Tool]:
    """Executables a skill's own instructions tell an agent to run."""
    document = SKILLS_DIR / skill / "SKILL.md"
    if not document.is_file():
        return []
    text = document.read_text(encoding="utf-8")
    return [Tool(skill, path, (ROOT / path).is_file())
            for path in dict.fromkeys(TOOL_PATH.findall(text))]


def bindings() -> dict[str, list[Tool]]:
    """Every in-repo skill and the executables it actually reaches for."""
    return {skill: tools_of(skill) for skill in sorted(local_skills())}


def broken_tools() -> list[Tool]:
    """Skills pointing at a script that is not there. Drift, caught early."""
    return [tool for tools in bindings().values() for tool in tools if not tool.bound]


def render_bindings() -> str:
    found = bindings()
    executable = {name: tools for name, tools in found.items() if tools}
    lines = [f"{len(executable)} of {len(found)} in-repo skills run something; "
             "the rest are written procedure."]
    for name, tools in sorted(executable.items()):
        for tool in tools:
            lines.append(f"  {'ok ' if tool.bound else 'MISSING'} {name:<26} {tool.path}")
    prose = sorted(name for name, tools in found.items() if not tools)
    if prose:
        lines += ["", "Procedure only (an agent follows these by hand):",
                  "  " + ", ".join(prose)]
    return "\n".join(lines)


def build_manifest(provider: str) -> dict:
    """Turn today's unresolved references into an explicit, reviewable dependency list."""
    entries = {}
    for skills in audit().values():
        for skill in skills:
            if skill.resolution == LOCAL or skill.reference in entries:
                continue
            entries[skill.reference] = {"family": skill.family, "provider": provider,
                                        "status": "expected-from-environment"}
    return {"version": 1,
            "note": "External skills these departments reference. Declared, not proven: "
                    "each is expected from the environment the agents run in, and is "
                    "unverified until it has actually been invoked here.",
            "skills": dict(sorted(entries.items()))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="fail if any department claims a skill nobody can resolve")
    parser.add_argument("--write-manifest", metavar="PROVIDER",
                        help="record today's external references as declared dependencies")
    parser.add_argument("--tools", action="store_true",
                        help="which skills actually run something")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.write_manifest:
        manifest = build_manifest(args.write_manifest)
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"{MANIFEST.relative_to(ROOT)}: {len(manifest['skills'])} skills declared")
        return 0

    if args.tools:
        print(render_bindings())
        return 1 if args.check and broken_tools() else 0

    found = audit()
    print(json.dumps(summary(found), indent=2) if args.json else render(found))
    if args.check and unresolved(found):
        print("\nunresolved skills: build them, or declare them in "
              f"{MANIFEST.relative_to(ROOT)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
