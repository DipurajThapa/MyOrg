from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from pathlib import Path

from runtime.filelock import exclusive_lock

ROOT = Path(__file__).resolve().parents[1]

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    from runtime.filelock import exclusive_lock
    with exclusive_lock(Path(sys.argv[2])):
        print("held", flush=True)
        time.sleep(float(sys.argv[3]))
    """
)


class ExclusiveLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.lock_path = Path(self._tmp.name) / "locks" / "run.lock"

    def test_acquires_and_creates_missing_parent(self) -> None:
        with exclusive_lock(self.lock_path):
            self.assertTrue(self.lock_path.exists())

    def test_lock_is_reusable_after_release(self) -> None:
        for _ in range(3):
            with exclusive_lock(self.lock_path):
                pass

    def test_released_on_exception(self) -> None:
        with self.assertRaises(ValueError):
            with exclusive_lock(self.lock_path):
                raise ValueError("boom")
        with exclusive_lock(self.lock_path, timeout=1.0):
            pass

    def test_second_process_is_excluded_until_release(self) -> None:
        holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(ROOT), str(self.lock_path), "1.5"],
            stdout=subprocess.PIPE, text=True,
        )
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "held")

        with self.assertRaises(SystemExit):
            with exclusive_lock(self.lock_path, timeout=0.3):
                pass

        holder.wait(timeout=10)
        with exclusive_lock(self.lock_path, timeout=5.0):
            pass

    def test_serializes_concurrent_writers(self) -> None:
        target = Path(self._tmp.name) / "counter.txt"
        target.write_text("", encoding="utf-8")
        overlaps = []

        def worker() -> None:
            with exclusive_lock(self.lock_path, timeout=30.0):
                before = target.read_text(encoding="utf-8")
                time.sleep(0.01)
                target.write_text(before + "x", encoding="utf-8")
                overlaps.append(len(before))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=60)

        self.assertEqual(target.read_text(encoding="utf-8"), "x" * 8)
        self.assertEqual(sorted(overlaps), list(range(8)))


if __name__ == "__main__":
    unittest.main()
