#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
EVIDENCE_DIR="${SCRIPT_DIR}/evidence"
export REPO_ROOT
export EVIDENCE_DIR

required_files=(
  "${EVIDENCE_DIR}/dossier.json"
  "${EVIDENCE_DIR}/hashes.manifest.json"
  "${EVIDENCE_DIR}/ledger_entry.json"
)

for f in "${required_files[@]}"; do
  if [ ! -f "$f" ]; then
    echo "Missing required file: $f" >&2
    exit 1
  fi
done

if [ ! -d "${EVIDENCE_DIR}/ralph" ]; then
  echo "Missing required directory: ${EVIDENCE_DIR}/ralph" >&2
  exit 1
fi

python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

repo_root = Path(os.environ["REPO_ROOT"])
schema_ok = True

# Validate dossier parses
try:
    dossier = json.loads(Path(os.environ["EVIDENCE_DIR"]).joinpath("dossier.json").read_text())
except Exception as exc:
    raise SystemExit(f"dossier.json invalid: {exc}")

manifest_path = Path(os.environ["EVIDENCE_DIR"]).joinpath("hashes.manifest.json")
try:
    manifest = json.loads(manifest_path.read_text())
except Exception as exc:
    raise SystemExit(f"hashes.manifest.json invalid: {exc}")

files = manifest.get("files", {})
if not isinstance(files, dict) or not files:
    raise SystemExit("hashes.manifest.json has no files map")

computed_entries = []
for rel_path, expected_hash in sorted(files.items()):
    path = (repo_root / rel_path).resolve()
    if not path.is_file():
        raise SystemExit(f"Missing file listed in manifest: {rel_path}")
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(f"Hash mismatch for {rel_path}: expected {expected_hash}, got {actual_hash}")
    computed_entries.append(f"{rel_path}:{actual_hash}")

root_hash = hashlib.sha256("\n".join(computed_entries).encode()).hexdigest()
if root_hash != manifest.get("root_hash"):
    raise SystemExit(f"Root hash mismatch: manifest {manifest.get('root_hash')} vs recomputed {root_hash}")

ledger_path = Path(os.environ["EVIDENCE_DIR"]).joinpath("ledger_entry.json")
try:
    ledger = json.loads(ledger_path.read_text())
except Exception as exc:
    raise SystemExit(f"ledger_entry.json invalid: {exc}")

if ledger.get("hashes_manifest_root") != manifest.get("root_hash"):
    raise SystemExit("ledger_entry.json does not reference manifest root hash")

print("All checks passed")
PY
