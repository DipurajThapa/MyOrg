#!/usr/bin/env python3
"""The operator inbox: one GitHub issue per MyOrg notice, through `gh`.

Assign this to MYORG_NOTIFY_COMMAND. It receives the notice as JSON in its last argument
(the contract in `runtime/notify.py`), and nothing here is ever run through a shell, so
notice text is text.

    MYORG_NOTIFY_COMMAND="python3 scripts/notify_github.py"
    MYORG_NOTIFY_GITHUB_REPO="owner/repo"      # explicit; never guessed from cwd in production
    GH_TOKEN=...                                # only where the service user has no `gh` login

Why an issue per notice, and not a running incident issue: notices are rare (a decision
waiting, a run that stopped), each is a separate thing a person must act on, and an open
issue *is* the outstanding item -- closing it is the acknowledgement. The runtime retries
a failed send every pass, so this is idempotent on the notice id: a retry of a notice that
already has an issue adds nothing; a *changed* fact under the same id (the runtime only
re-raises when subject or detail changed) reopens the issue and comments.

Exit codes: 0 delivered (created, commented, or already there); 2 not configured or `gh`
unusable; 3 GitHub refused. Everything it did is on stdout; every failure on stderr.

This is an inbox. It is not paging: GitHub does not notify a person about their own
actions, so the issue must be created by an identity other than the one meant to read it.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

MARKER = "myorg-notice"
TITLE_MAX = 120
LIST_LIMIT = 200
GH_TIMEOUT_SECONDS = 15


def gh(*args: str) -> subprocess.CompletedProcess:
    """Run `gh` with an argument list -- never a shell. MYORG_GH lets a test stand in."""
    # posix=False keeps Windows paths intact but leaves the quotes on; strip them, as the
    # runtime does for MYORG_NOTIFY_COMMAND itself.
    executable = [part.strip('"') for part in
                  shlex.split(os.environ.get("MYORG_GH", "gh"), posix=os.name != "nt")]
    return subprocess.run([*executable, *args], capture_output=True, text=True,
                          timeout=GH_TIMEOUT_SECONDS, check=False, encoding="utf-8")


def fail(code: int, message: str) -> int:
    print(f"notify_github: {message}", file=sys.stderr)
    return code


def load_notice(argv: list[str]) -> dict:
    raw = sys.stdin.read() if argv and argv[-1] == "-" else (argv[-1] if argv else "")
    notice = json.loads(raw)
    if not isinstance(notice, dict) or not notice.get("id") or not notice.get("subject"):
        raise ValueError("a notice needs at least an id and a subject")
    return notice


def repository() -> str:
    explicit = os.environ.get("MYORG_NOTIFY_GITHUB_REPO", "").strip()
    if explicit:
        return explicit
    # Falls back to the checkout `gh` is standing in -- fine at a keyboard, wrong for a
    # service, which is why the runbook makes the variable required in deployment.
    found = gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner")
    return found.stdout.strip() if found.returncode == 0 else ""


def render(notice: dict) -> tuple[str, str]:
    severity = notice.get("severity", "routine")
    title = f"[MyOrg · {severity}] {notice['subject']}"[:TITLE_MAX]
    where = "/".join(p for p in (notice.get("run_id", ""), notice.get("step_id", "")) if p)
    body = "\n".join(filter(None, (
        notice.get("detail", "").strip(),
        "",
        f"**Do:** {notice['action'].strip()}" if notice.get("action") else "",
        "",
        f"Run/step: `{where}`" if where else "",
        f"Organization: `{notice.get('org_id', '')}`" if notice.get("org_id") else "",
        f"Raised: {notice.get('created_at', '')}" if notice.get("created_at") else "",
        "",
        "Closing this issue is the acknowledgement. It is not paging: nobody is woken by it.",
        "",
        f"`{MARKER}: {notice['id']}`",
    )))
    return title, body


def existing_issue(repo: str, notice_id: str) -> dict | None:
    """Exact match on the marker line, from a plain listing -- GitHub's search index lags
    a fresh issue by seconds, and a retry inside that window would make a duplicate."""
    listed = gh("issue", "list", "--repo", repo, "--state", "all", "--limit", str(LIST_LIMIT),
                "--json", "number,state,body,url")
    if listed.returncode != 0:
        raise RuntimeError(f"gh issue list failed: {listed.stderr.strip() or listed.stdout.strip()}")
    needle = f"`{MARKER}: {notice_id}`"
    for issue in json.loads(listed.stdout or "[]"):
        if needle in (issue.get("body") or ""):
            return issue
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        notice = load_notice(argv)
    except (ValueError, json.JSONDecodeError) as error:
        return fail(2, f"could not read the notice from the last argument: {error}")
    try:
        repo = repository()
    except (OSError, subprocess.SubprocessError) as error:
        return fail(2, f"gh is not usable here: {error}")
    if not repo:
        return fail(2, "no repository: set MYORG_NOTIFY_GITHUB_REPO=owner/repo")

    title, body = render(notice)
    try:
        found = existing_issue(repo, notice["id"])
        if found is None:
            created = gh("issue", "create", "--repo", repo, "--title", title, "--body", body)
            if created.returncode != 0:
                return fail(3, f"GitHub refused the issue: {created.stderr.strip() or created.stdout.strip()}")
            print(f"created {created.stdout.strip()} for {notice['id']}")
            return 0
        number = str(found["number"])
        # Same id again. If nothing is new the runtime would not have re-raised it, so a
        # second arrival means the fact changed: bring the issue back into view and say so.
        if found.get("state", "").upper() == "CLOSED":
            reopened = gh("issue", "reopen", "--repo", repo, number)
            if reopened.returncode != 0:
                return fail(3, f"GitHub refused to reopen #{number}: {reopened.stderr.strip()}")
        commented = gh("issue", "comment", "--repo", repo, number, "--body",
                       f"Raised again by MyOrg:\n\n{body}")
        if commented.returncode != 0:
            return fail(3, f"GitHub refused the comment on #{number}: {commented.stderr.strip()}")
        print(f"updated {found.get('url', '#' + number)} for {notice['id']}")
        return 0
    except (OSError, subprocess.SubprocessError, RuntimeError, KeyError) as error:
        return fail(3, str(error))


if __name__ == "__main__":
    raise SystemExit(main())
