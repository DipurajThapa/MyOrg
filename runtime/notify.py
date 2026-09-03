#!/usr/bin/env python3
"""Telling a person that the company needs them.

Until now nothing reached outward: a run could stop dead and wait forever because the
only way to find out was to go and look. This is the outbox for that.

It deliberately does not send anything by itself. Sending is a yellow action, so the
default is a local file a human reads, and any real delivery (mail, chat, pager) is a
command the operator wires up on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime import company_runtime as core  # noqa: E402
from runtime.filelock import exclusive_lock  # noqa: E402

OUTBOX_ENV = "MYORG_OUTBOX"
DELIVERY_ENV = "MYORG_NOTIFY_COMMAND"
DELIVERY_TIMEOUT_SECONDS = 20

NEEDS_APPROVAL = "needs_approval"
RUN_FAILED = "run_failed"
RUN_STALLED = "run_stalled"
LESSON_PROPOSED = "lesson_proposed"
SMOKE_TEST = "smoke_test"
SEVERITY = {NEEDS_APPROVAL: "blocking", RUN_FAILED: "blocking",
            RUN_STALLED: "attention", LESSON_PROPOSED: "routine", SMOKE_TEST: "routine"}
STDERR_TAIL_CHARS = 400


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def outbox_path() -> Path:
    return Path(os.environ.get(OUTBOX_ENV, core.RUNS / "_outbox.jsonl"))


@dataclass(frozen=True)
class Notice:
    """One thing a person needs to know, and what they can do about it."""
    id: str
    kind: str
    severity: str
    org_id: str
    subject: str
    detail: str
    action: str
    run_id: str = ""
    step_id: str = ""
    created_at: str = ""
    delivered: bool = False
    attempts: int = 0        # delivery attempts so far; a failure is never silent
    last_error: str = ""     # why the last attempt failed, for `notify list`

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


def notice_id(kind: str, run_id: str, step_id: str) -> str:
    """Stable per subject, so the same problem is not reported twice."""
    return "-".join(part for part in (kind, run_id, step_id) if part)


def read_all() -> list[Notice]:
    path = outbox_path()
    if not path.is_file():
        return []
    latest: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        latest[record["id"]] = {**latest.get(record["id"], {}), **record}
    return [Notice(**record) for record in latest.values()]


def append(notice: Notice) -> Notice:
    path = outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(path.with_suffix(".lock")):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(notice), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return notice


def raise_notice(kind: str, subject: str, detail: str, action: str,
                 org_id: str = "", run_id: str = "", step_id: str = "") -> Notice | None:
    """Record that a person is needed. Returns None if it is already outstanding -- or
    already delivered and nothing about it has changed.

    The scan runs every pass. Without the second rule a run waiting on a decision would be
    re-raised, and re-sent, once a minute for as long as it waited: the sink would drown
    in the same fact. A *changed* fact (different subject or detail, e.g. more steps done)
    is a new notice under the same id, and is sent again."""
    identifier = notice_id(kind, run_id, step_id)
    existing = {item.id: item for item in read_all()}
    prior = existing.get(identifier)
    if prior is not None:
        if not prior.delivered:
            return None
        if (prior.subject, prior.detail) == (subject, detail):
            return None
    return append(Notice(
        id=identifier, kind=kind, severity=SEVERITY.get(kind, "routine"),
        org_id=org_id or core.DEFAULT_ORG, subject=subject, detail=detail,
        action=action, run_id=run_id, step_id=step_id, created_at=now()))


def mark_delivered(identifier: str) -> None:
    for notice in read_all():
        if notice.id == identifier:
            append(Notice(**{**asdict(notice), "delivered": True}))
            return


def outstanding() -> list[Notice]:
    """Blocking first: a stopped run matters more than a lesson to review."""
    order = {"blocking": 0, "attention": 1, "routine": 2}
    return sorted((n for n in read_all() if not n.delivered),
                  key=lambda n: (order.get(n.severity, 9), n.created_at))


def record_failure(notice: Notice, error: str) -> None:
    """A failed send is a fact about the notice, not a log line that scrolls away."""
    append(Notice(**{**asdict(notice), "attempts": notice.attempts + 1,
                     "last_error": error[:STDERR_TAIL_CHARS]}))


def describe(error: BaseException) -> str:
    """What went wrong, with the command's own words when it had any."""
    if isinstance(error, subprocess.CalledProcessError):
        tail = (error.stderr or error.stdout or b"").decode("utf-8", "replace").strip()
        return f"exit {error.returncode}: {tail[-STDERR_TAIL_CHARS:] or 'no output'}"
    if isinstance(error, subprocess.TimeoutExpired):
        return f"timed out after {DELIVERY_TIMEOUT_SECONDS}s"
    return f"{type(error).__name__}: {error}"


def deliver(log=print) -> list[Notice]:
    """Hand outstanding notices to whatever the operator wired up, if anything.

    The contract, in one place: the command named by MYORG_NOTIFY_COMMAND is run once per
    outstanding notice, with the notice as JSON in its **last argument**; exit 0 means
    delivered and the notice is marked so; anything else leaves it outstanding, records the
    attempt and the command's stderr on the notice, and it is tried again next pass. The
    command is split like a shell would but never run through one, so notice content is
    never shell syntax. Notice ids are stable per (kind, run, step), so a sink that is
    retried can tell a retry from a new fact.

    With no command configured this is a no-op on purpose: the company never reaches
    outward on its own.
    """
    command = os.environ.get(DELIVERY_ENV, "").strip()
    waiting = outstanding()
    if not command or not waiting:
        return waiting
    # posix=False on Windows, or shlex eats the backslashes in the command's own path.
    argv = shlex.split(command, posix=os.name != "nt")
    argv = [part.strip('"') for part in argv]
    sent = []
    for notice in waiting:
        try:
            subprocess.run(argv + [json.dumps(asdict(notice))],
                           timeout=DELIVERY_TIMEOUT_SECONDS, check=True,
                           capture_output=True)
        except (OSError, subprocess.SubprocessError) as error:
            reason = describe(error)
            record_failure(notice, reason)
            log(f"  could not deliver {notice.id} (attempt {notice.attempts + 1}): {reason}")
            continue
        mark_delivered(notice.id)
        sent.append(notice)
    return sent


def smoke(log=print) -> int:
    """Send one synthetic notice through the real path and say which stage failed.

    Exit codes: 1 the notice could not be written; 2 no command is configured; 3 the
    command ran and failed (its error is on the notice and in the log); 0 delivered to the
    sink. Whether a *person* saw it is the one thing this cannot check -- go and look.
    """
    import uuid
    stamp = f"{now().replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:6]}"
    notice = raise_notice(
        SMOKE_TEST, f"MyOrg test notice {stamp}",
        "This is a synthetic notice sent by an operator to prove the delivery path. "
        "Nothing is waiting on you.", "Confirm you can see this, then close or acknowledge it.",
        run_id=f"smoke-{stamp.lower()}")
    if notice is None:
        log("stage 1 FAIL: the notice could not be written to the outbox")
        return 1
    log(f"stage 1 ok: notice {notice.id} written to {outbox_path()}")
    if not os.environ.get(DELIVERY_ENV, "").strip():
        log(f"stage 2 FAIL: {DELIVERY_ENV} is not set; the notice stays in the outbox")
        return 2
    log(f"stage 2 ok: {DELIVERY_ENV} is set")
    sent = deliver(log=log)
    if notice.id not in {n.id for n in sent}:
        failed = next((n for n in read_all() if n.id == notice.id), notice)
        log(f"stage 3 FAIL: the command did not deliver it -- {failed.last_error}")
        return 3
    log("stage 3 ok: the command exited 0 and the notice is marked delivered")
    log("stage 4: a person must now confirm they saw it where it was sent")
    return 0


def render(notices: list[Notice]) -> str:
    if not notices:
        return "Nothing needs you."
    lines = []
    for notice in notices:
        mark = "!" if notice.blocking else " "
        located = "/".join(part for part in (notice.run_id, notice.step_id) if part)
        where = f" [{located}]" if located else ""
        lines += [f"{mark} {notice.subject}{where}",
                  f"    {notice.detail}",
                  f"    do: {notice.action}"]
        if notice.last_error:
            lines.append(f"    delivery failed {notice.attempts}x, last: {notice.last_error}")
    blocking = sum(1 for n in notices if n.blocking)
    lines += ["", f"{len(notices)} waiting; {blocking} blocking."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    commands.add_parser("deliver")
    commands.add_parser("test", help="send one synthetic notice through the real path")
    seen = commands.add_parser("ack")
    seen.add_argument("notice_id")

    args = parser.parse_args(argv)
    if args.command == "list":
        print(render(outstanding()))
    elif args.command == "deliver":
        print(f"delivered {len(deliver())} notice(s)")
    elif args.command == "test":
        return smoke()
    else:
        mark_delivered(args.notice_id)
        print("acknowledged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
