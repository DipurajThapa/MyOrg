from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "release-evidence" / "gate-record.template.json"


class ReleaseGate(unittest.TestCase):
    def execute(self, record: Path, expected: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", "scripts/release_gate.py", "--record", str(record), "--expect", expected],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )

    def test_template_is_explicitly_blocked(self):
        result = self.execute(TEMPLATE, "blocked")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["decision"], "blocked")

    def test_ready_requires_every_check_evidence_signoff_and_revision(self):
        record = json.loads(TEMPLATE.read_text(encoding="utf-8"))
        record["source_revision"] = "a" * 40
        record["checks"] = {key: True for key in record["checks"]}
        record["evidence"] = {key: f"evidence/{key}.json" for key in record["evidence"]}
        record["signoffs"] = {key: f"signed:{key}" for key in record["signoffs"]}
        record["decision"] = "ready"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release.json"
            path.write_text(json.dumps(record), encoding="utf-8")
            passed = self.execute(path, "ready")
            self.assertEqual(passed.returncode, 0, passed.stderr)
            record["checks"]["rollback_rehearsal_passed"] = False
            path.write_text(json.dumps(record), encoding="utf-8")
            denied = self.execute(path, "ready")
            self.assertNotEqual(denied.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
