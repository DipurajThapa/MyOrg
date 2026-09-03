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


# What one operator needs on day one: answer the gates, and prepare work to be gated.
# Anything wider -- connectors, other people's identities, recovery -- is a deliberate
# second step, not something a first run hands out quietly.
STARTING_ROLES = ("decision-owner", "maker")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="MyOrg offline administration (requires host access)")
    result.add_argument("--db", default=os.environ.get("MYORG_DB", "runtime/data/myorg.db"))
    commands = result.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--org", required=True, type=identifier)
    init.add_argument("--name", required=True)
    boot = commands.add_parser("bootstrap", help="stand a new company up: store, organization, first operator, token")
    boot.add_argument("--org", required=True, type=identifier)
    boot.add_argument("--name", required=True)
    boot.add_argument("--operator", required=True, type=identifier)
    boot.add_argument("--operator-name", required=True)
    boot.add_argument("--role", action="append", choices=sorted(ROLES))
    boot.add_argument("--ttl", type=int, default=900)
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
    if args.command == "bootstrap":
        secret = os.environ.get("MYORG_AUTH_SECRET")
        if not secret:
            raise SystemExit(
                "MYORG_AUTH_SECRET is required before bootstrapping: the store would be "
                "created with nobody able to sign in. Generate one with "
                "`python -c \"import secrets;print(secrets.token_hex(32))\"` and export it.")
        roles = sorted(set(args.role or STARTING_ROLES))
        # Every step is idempotent, so a half-finished bootstrap is safe to run again.
        store.bootstrap_organization(args.org, args.name)
        store.upsert_actor(args.org, args.operator, "human", args.operator_name, roles)
        token = TokenService(store, secret).issue(args.org, args.operator, args.ttl)
        database = str(Path(args.db).resolve())
        print(json.dumps({"organization": args.org, "operator": args.operator,
                          "roles": roles, "database": database, "token": token,
                          "token_ttl_seconds": args.ttl}, sort_keys=True))
        print()
        print("The company now exists. To run it:")
        print(f"  export MYORG_DB={database}")
        print("  export MYORG_AUTH_SECRET=<the same secret you just used>")
        print(f"  export MYORG_CONSOLE_ACTOR={args.operator}")
        print("  python -m runtime.api                 # the governed API and the console")
        print("  python -m runtime.projection          # mirror runs into the read model")
        print("  python -m runtime.scheduler --once    # drive whatever can move")
        print()
        # Without this line a new operator has a running company and no idea it has a face:
        # the console was reachable only from a runbook section nobody opens first.
        print("Then open http://127.0.0.1:8080/ -- that is the console: ask for work,")
        print("read what came out, and answer anything waiting on you.")
        print()
        print(f"The token above expires in {args.ttl}s and is a bearer credential: keep it out")
        print("of shell history, logs and tickets. Issue another with `issue-token`.")
    elif args.command == "init":
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
