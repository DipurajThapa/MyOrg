#!/usr/bin/env python3
"""Create reproducible source checksums, a minimal SBOM, and a secret-scan record."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", "node_modules", "dist", ".next", ".vinext", ".wrangler", ".sites-runtime",
                  "__pycache__", "artifacts", "release-evidence"}
SECRET_PATTERNS = {
    "private_key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "github_token": re.compile(rb"gh[pousr]_[A-Za-z0-9_]{32,}"),
    "openai_key": re.compile(rb"sk-[A-Za-z0-9_-]{32,}"),
}


def included_files() -> list[Path]:
    files = []
    for path in ROOT.rglob("*"):
        if path.is_file() and not any(part in EXCLUDED_PARTS for part in path.relative_to(ROOT).parts):
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    manifest = []
    findings = []
    for path in included_files():
        raw = path.read_bytes()
        relative = path.relative_to(ROOT).as_posix()
        manifest.append({"path": relative, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(raw):
                findings.append({"path": relative, "pattern": name})
    (output / "source-manifest.json").write_text(
        json.dumps({"version": 1, "created_at": timestamp, "files": manifest}, indent=2) + "\n", encoding="utf-8")
    (output / "secret-scan.json").write_text(
        json.dumps({"version": 1, "created_at": timestamp, "patterns": sorted(SECRET_PATTERNS),
                    "status": "pass" if not findings else "fail", "findings": findings}, indent=2) + "\n",
        encoding="utf-8")
    serial = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/DipurajThapa/MyOrg")
    sbom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial}",
            "version": 1, "metadata": {"timestamp": timestamp,
            "component": {"type": "application", "name": "myorg-runtime", "version": "0.2.0"}},
            "components": [{"type": "framework", "name": "Python standard library", "version": "3.12",
                            "scope": "required"}]}
    (output / "runtime-sbom.cdx.json").write_text(json.dumps(sbom, indent=2) + "\n", encoding="utf-8")
    evidence_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(output.iterdir())}
    (output / "evidence-checksums.json").write_text(
        json.dumps({"version": 1, "created_at": timestamp, "sha256": evidence_hashes}, indent=2) + "\n",
        encoding="utf-8")
    if findings:
        raise SystemExit("release evidence failed: possible credential patterns found")
    print(json.dumps({"status": "pass", "files": len(manifest), "output": str(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
