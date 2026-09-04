"""The email sink: the one delivery that actually reaches a person.

The GitHub sink is a record, not a summons -- GitHub does not notify an account of its own
actions, so an issue opened by the reader alerts nobody. This one does reach somebody, which
is exactly why what it does when it is *not* set up matters as much as what it sends. A sink
that half-works is worse than none: `notify.deliver` treats exit 0 as delivered and never
tries again, so a lie about delivery loses the notice for good.

Nothing here sends mail. What is checked is the contract around the send: how it refuses,
what the message says, and that a password never leaves the process.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "notify-email.py"

NOTICE = {
    "id": "needs_approval-run-x-release-output", "kind": "needs_approval",
    "severity": "blocking", "org_id": "acme",
    "subject": "run-x is waiting on your decision",
    "detail": "Step release-output needs a person. 2 of 3 done.",
    "action": "Open the Control Center and approve or reject it.",
    "run_id": "run-x", "step_id": "release-output", "created_at": "2026-09-04T16:00:00Z",
}
CONFIGURED = {"MYORG_NOTIFY_EMAIL": "someone@example.com", "MYORG_SMTP_HOST": "smtp.invalid",
              "MYORG_SMTP_USER": "sender@example.com", "MYORG_SMTP_PASSWORD": "s3cret-app-pw"}


def load():
    spec = importlib.util.spec_from_file_location("notify_email", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmailSink(unittest.TestCase):
    def run_it(self, argument=None, **extra):
        """The script as `notify.deliver` runs it: no shell, notice as the last argument."""
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("MYORG_")}
        environment.update(extra)
        argv = [sys.executable, str(SCRIPT)]
        if argument is not None:
            argv.append(argument)
        return subprocess.run(argv, capture_output=True, text=True, env=environment,
                              timeout=30)

    def test_an_unconfigured_sink_refuses_and_names_what_is_missing(self):
        """It must not exit 0 having done nothing: that marks the notice delivered and it is
        never sent or retried. And the refusal has to name the settings, because this text is
        what gets recorded on the notice for somebody to read later."""
        result = self.run_it(json.dumps(NOTICE))
        self.assertNotEqual(result.returncode, 0, "silence here loses the notice for good")
        for name in ("MYORG_NOTIFY_EMAIL", "MYORG_SMTP_HOST", "MYORG_SMTP_USER",
                     "MYORG_SMTP_PASSWORD"):
            self.assertIn(name, result.stderr)

    def test_it_refuses_what_is_not_a_notice(self):
        for argument, why in ((None, "no argument at all"), ("not json", "not JSON"),
                              ('"a string"', "JSON but not an object")):
            with self.subTest(why=why):
                result = self.run_it(argument, **CONFIGURED)
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(result.stderr.strip(), "a refusal has to say why")

    def test_a_password_never_leaves_the_process(self):
        """The stderr of a failed send is kept on the notice and read by a person later, so
        anything printed on that path is as good as written down."""
        result = self.run_it(json.dumps(NOTICE), **CONFIGURED)
        self.assertNotEqual(result.returncode, 0, "smtp.invalid cannot be reached")
        for stream in (result.stdout, result.stderr):
            self.assertNotIn(CONFIGURED["MYORG_SMTP_PASSWORD"], stream)

    def test_the_message_says_what_happened_and_what_to_do(self):
        module = load()
        message = module.build(NOTICE, "runtime@example.com", "someone@example.com")
        self.assertEqual(message["To"], "someone@example.com")
        self.assertEqual(message["From"], "runtime@example.com")
        self.assertIn("[needs you]", message["Subject"], "severity is visible before opening")
        self.assertIn(NOTICE["subject"], message["Subject"])
        body = message.get_content()
        self.assertIn(NOTICE["detail"], body)
        self.assertIn(NOTICE["action"], body, "a notice without a next step is just noise")
        self.assertIn("run-x", body)
        self.assertIn("release-output", body)
        self.assertIn("/kanban", body, "somewhere to go and act on it")

    def test_severity_is_visible_in_the_subject_line(self):
        module = load()
        for severity, marker in (("blocking", "[needs you]"), ("attention", "[attention]"),
                                 ("routine", "[FYI]")):
            with self.subTest(severity=severity):
                message = module.build({**NOTICE, "severity": severity}, "a@b", "c@d")
                self.assertTrue(message["Subject"].startswith(marker),
                                "a full inbox is triaged from the subject alone")


if __name__ == "__main__":
    unittest.main()
