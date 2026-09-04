#!/usr/bin/env python3
"""Deliver one notice by email. Wire this up as MYORG_NOTIFY_COMMAND, or don't.

The runtime never reaches outward on its own: `notify.deliver` runs whatever command
MYORG_NOTIFY_COMMAND names, once per outstanding notice, with the notice as JSON in its
last argument, and treats exit 0 as delivered. With no command set, nothing is sent. This
script is one such command; installing it is the operator's decision, and until the
settings below are present it refuses rather than half-works.

    MYORG_NOTIFY_EMAIL      where notices go            (required)
    MYORG_SMTP_HOST         mail server                 (required)
    MYORG_SMTP_USER         account to sign in as       (required)
    MYORG_SMTP_PASSWORD     that account's password     (required)
    MYORG_SMTP_PORT         defaults to 587 (STARTTLS)
    MYORG_SMTP_FROM         defaults to MYORG_SMTP_USER

The address lives in an environment variable rather than in this file on purpose: a
recipient is a personal detail and this repository is public.

Gmail needs an App Password, not the account password, and only issues one with two-factor
sign-in turned on. Nothing here disables certificate verification -- if the connection
cannot be trusted the send fails, and a notice that failed to send stays outstanding with
the reason recorded on it, to be tried again next pass.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

REQUIRED = ("MYORG_NOTIFY_EMAIL", "MYORG_SMTP_HOST", "MYORG_SMTP_USER", "MYORG_SMTP_PASSWORD")
SEVERITY_PREFIX = {"blocking": "[needs you]", "attention": "[attention]", "routine": "[FYI]"}


def build(notice: dict, sender: str, recipient: str) -> EmailMessage:
    """One notice as a message a person can act on without opening anything else."""
    severity = str(notice.get("severity", "routine"))
    message = EmailMessage()
    message["Subject"] = f"{SEVERITY_PREFIX.get(severity, '[FYI]')} {notice.get('subject', 'MyOrg')}"
    message["From"] = sender
    message["To"] = recipient
    lines = [
        str(notice.get("detail", "")).strip(),
        "",
        f"What to do: {str(notice.get('action', '')).strip()}",
        "",
        f"Run:      {notice.get('run_id') or '-'}",
        f"Step:     {notice.get('step_id') or '-'}",
        f"Company:  {notice.get('org_id') or '-'}",
        f"Raised:   {notice.get('created_at') or '-'}",
        "",
        "Board: http://127.0.0.1:8080/kanban",
    ]
    message.set_content("\n".join(lines))
    return message


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: notify-email.py '<notice json>'", file=sys.stderr)
        return 2
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        # Named, so the failure recorded on the notice says exactly what to set. The value
        # of a secret is never printed -- only whether it was there.
        print(f"not configured: set {', '.join(missing)}", file=sys.stderr)
        return 3
    try:
        notice = json.loads(argv[-1])
    except json.JSONDecodeError as error:
        print(f"notice was not JSON: {error}", file=sys.stderr)
        return 4
    if not isinstance(notice, dict):
        print("notice must be a JSON object", file=sys.stderr)
        return 4

    user = os.environ["MYORG_SMTP_USER"].strip()
    sender = os.environ.get("MYORG_SMTP_FROM", "").strip() or user
    recipient = os.environ["MYORG_NOTIFY_EMAIL"].strip()
    host = os.environ["MYORG_SMTP_HOST"].strip()
    port = int(os.environ.get("MYORG_SMTP_PORT", "587") or 587)
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(user, os.environ["MYORG_SMTP_PASSWORD"])
            server.send_message(build(notice, sender, recipient))
    except (smtplib.SMTPException, OSError, ssl.SSLError) as error:
        # `notify.deliver` keeps this text on the notice and tries again next pass, so it
        # has to say what went wrong without ever repeating the credentials.
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(f"sent {notice.get('id', '?')} to {recipient}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
