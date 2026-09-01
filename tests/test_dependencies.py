"""The toolchain is reproducible only if the claim "stdlib only" is checked, not asserted.

DEP-06 in the REV2 audit: there was no dependency manifest at all, so the environment the
tests ran in was whatever the machine happened to have. `pyproject.toml` now states the
contract -- Python 3.11+, no third-party packages -- and this test is what keeps the
statement true, because a single stray `import requests` would otherwise turn a documented
zero-dependency runtime into an undocumented one-dependency runtime silently.
"""
from __future__ import annotations

import ast
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("runtime", "scripts", "tests")
LOCAL = {"runtime", "scripts", "tests"}


def imported_top_level_modules() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for directory in SOURCE_DIRS:
        for path in (ROOT / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] if node.level == 0 else []
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top:
                        found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


class DependencyTest(unittest.TestCase):
    def test_the_manifest_exists_and_declares_the_interpreter(self) -> None:
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertIn("requires-python", manifest["project"])
        self.assertTrue(manifest["project"]["requires-python"].startswith(">=3.1"))

    def test_the_manifest_declares_no_dependencies(self) -> None:
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(manifest["project"]["dependencies"], [])

    def test_nothing_imports_a_package_outside_the_standard_library(self) -> None:
        """The whole point of the manifest. Names the offender and the file, so a failure
        is a decision to make rather than a puzzle to solve."""
        offenders = {
            module: sorted(files)
            for module, files in imported_top_level_modules().items()
            if module not in sys.stdlib_module_names and module not in LOCAL
        }
        self.assertEqual(offenders, {},
                         "declare these in pyproject.toml with an exact pin, or remove them")

    def test_the_running_interpreter_satisfies_the_manifest(self) -> None:
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))


if __name__ == "__main__":
    unittest.main()
