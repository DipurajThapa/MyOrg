#!/usr/bin/env python3
"""Offline administrative controls for identities, connectors, and recovery."""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from runtime.auth import TokenService
from runtime.connectors import validate_manifest
from runtime.db import Store, restore_backup

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
ROLES = {"system-admin", "chief-of-staff", "maker", "decision-owner", "connector-gateway", "auditor", "viewer"}


def identifier(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a lowercase slug")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MyOrg offline administration (requires host access)")
    result.add_argument("--db", default=os.environ.get("MYORG_DB", "runtime/data/myorg.db"))
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--org", required=True, type=identifier)
    init.add_argument("--name", required=True)
    actor = commands.add_parser("actor")
    actor.add_argument("--org", required=True, type=identifier)
    actor.add_argument("--id", required=True, type=identifier)
    actor.add_argument("--type", required=True, choices=["human", "agent", "service"])
    actor.add_argument("--name", required=True)
    actor.add_argument("--role", action="append", required=True, choices=sorted(ROLES))
    actor_status = commands.add_parser("actor-status")
    actor_status.add_argument("--org", required=True, type=identifier)
    actor_status.add_argument("--id", required=True, type=identifier)
    actor_status.add_argument("--status", required=True, choices=["active", "disabled"])
    org_status = commands.add_parser("organization-status")
    org_status.add_argument("--org", required=True, type=identifier)
    org_status.add_argument("--status", required=True, choices=["active", "suspended"])
    token = commands.add_parser("issue-token")
    token.add_argument("--org", required=True, type=identifier)
    token.add_argument("--actor", required=True, type=identifier)
    token.add_argument("--ttl", type=int, default=300)
    revoke = commands.add_parser("revoke-token")
    revoke.add_argument("--token", required=True)
    identity = commands.add_parser("bind-identity")
    identity.add_argument("--issuer", required=True, choices=["chatgpt-sites"])
    identity.add_argument("--subject", required=True)
    identity.add_argument("--org", required=True, type=identifier)
    identity.add_argument("--actor", required=True, type=identifier)
    connector = commands.add_parser("register-connector")
    connector.add_argument("--org", required=True, type=identifier)
    connector.add_argument("--manifest", required=True, type=Path)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", required=True, type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("--input", required=True, type=Path)
    restore.add_argument("--confirm-target", required=True)
    purge = commands.add_parser("purge-transient")
    purge.add_argument("--idempotency-days", type=int, default=30)
    commands.add_parser("verify")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store(args.db)
    if args.command != "restore":
        store.migrate()
    if args.command == "init":
        store.bootstrap_organization(args.org, args.name)
        print(json.dumps({"organization": args.org, "status": "ready"}))
    elif args.command == "actor":
        store.upsert_actor(args.org, args.id, args.type, args.name, args.role)
        print(json.dumps(store.actor(args.org, args.id), sort_keys=True))
    elif args.command == "actor-status":
        print(json.dumps(store.set_actor_status(args.org, args.id, args.status), sort_keys=True))
    elif args.command == "organization-status":
        store.set_organization_status(args.org, args.status)
        print(json.dumps({"organization": args.org, "status": args.status}, sort_keys=True))
    elif args.command in {"issue-token", "revoke-token"}:
        secret = os.environ.get("MYORG_AUTH_SECRET")
        if not secret:
            raise SystemExit("MYORG_AUTH_SECRET is required")
        tokens = TokenService(store, secret)
        if args.command == "issue-token":
            print(tokens.issue(args.org, args.actor, args.ttl))
        else:
            tokens.revoke(args.token)
            print(json.dumps({"status": "revoked"}))
    elif args.command == "bind-identity":
        if not 3 <= len(args.subject.strip()) <= 320 or "\n" in args.subject or "\r" in args.subject:
            raise SystemExit("identity subject must be 3..320 characters on one line")
        print(json.dumps(store.bind_identity(args.issuer, args.subject, args.org, args.actor), sort_keys=True))
    elif args.command == "register-connector":
        manifest = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
        result = store.register_connector(args.org, manifest)
        result.pop("secret_ref", None)
        print(json.dumps(result, sort_keys=True))
    elif args.command == "backup":
        print(json.dumps(store.backup(args.output), sort_keys=True))
    elif args.command == "restore":
        expected = str(Path(args.db).resolve())
        if args.confirm_target != expected:
            raise SystemExit(f"restore refused: --confirm-target must exactly equal {expected}")
        print(json.dumps(restore_backup(args.input, args.db), sort_keys=True))
    elif args.command == "purge-transient":
        if not 1 <= args.idempotency_days <= 365:
            raise SystemExit("--idempotency-days must be 1..365")
        print(json.dumps(store.purge_transient(idempotency_days=args.idempotency_days), sort_keys=True))
    elif args.command == "verify":
        print(json.dumps(store.verify(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
