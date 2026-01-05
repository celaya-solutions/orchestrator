# Ralph ISO-Style Evidence Pack

This guide summarizes the `ralph-iso.md` pattern: treat each Ralph run as a self-contained, evidence-grade “Proof & Provenance Pack” with deterministic hashing and an append-only ledger entry.

## Why
- Reproducibility: prompt archives, state snapshots, git checkpoints.
- Integrity: sha256 manifest + ledger chaining (optional external anchoring).
- Safety: ACP allowlist and loop detection.
- Observability: metrics, logs, prompt archives for audits.

## Recommended Layout
```
repo_root/
  runs/{run_id}/
    PROMPT.md
    outputs/
    evidence/
      dossier.json
      hashes.manifest.json
      ledger_entry.json
      ralph/        # copied .agent prompts/metrics/logs
    verify.sh
  tools/
    pack_run.py     # copy .agent → evidence/ralph, build dossier/manifest/ledger
    hash_manifest.py
    append_ledger.py
  EVIDENCE_LEDGER/
    ledger.jsonl
    ledger.head
```

## Prompt Template (per run)
Use this in `runs/{run_id}/PROMPT.md`:
```
# Run: {run_id}
## Objective
Produce an inspection-ready “Proof & Provenance Pack” for this run under runs/{run_id}/evidence/.
## Context
- Research-first, non-commercial.
- Frontier models only if justified with expected outcomes and cost/benefit.
- Keep PII segregated; no exfiltration.
## Required Outputs (must exist)
1) dossier.json
2) hashes.manifest.json (sha256 for every artifact in this run folder)
3) ledger_entry.json (references manifest root hash)
4) ralph/ folder copied from .agent (prompts, metrics, logs)
## Verification
Create/update verify.sh to:
- check required files exist
- validate JSON parses
- recompute sha256 and match hashes.manifest.json
- exit 0 on success
## Success Criteria
- verify.sh exits 0
- dossier.json: model IDs, prompts/system policy, sampling params, tool usage summary, git commit hash, timestamps
- ledger_entry.json references hashes.manifest.json root hash
## Progress Log / Checkpoints
- [ ] Generate dossier.json skeleton
- [ ] Collect Ralph artifacts from .agent
- [ ] Produce hashes.manifest.json
- [ ] Produce ledger_entry.json
- [ ] verify.sh passes
- [ ] CHECKPOINT_1: dossier schema complete
- [ ] CHECKPOINT_2: hashes + ledger entry created
- [ ] CHECKPOINT_3: verify.sh green
- [ ] All criteria verified
```

## ACP Allowlist (tight mode)
Example snippet for `ralph.yml`:
```yaml
adapters:
  acp:
    enabled: true
    timeout: 300
    tool_permissions:
      agent_command: gemini
      permission_mode: allowlist
      permission_allowlist:
        - "fs/read_text_file:*"
        - "fs/write_text_file:runs/*"
        - "terminal/create:bash runs/*/verify.sh"
        - "terminal/create:python tools/*"
        - "terminal/create:git status*"
        - "terminal/create:git rev-parse*"
```

## Run Command (evidence-focused)
```bash
ralph run -a acp --acp-agent gemini --acp-permission-mode allowlist \
  --prompt runs/{run_id}/PROMPT.md \
  --checkpoint-interval 1 --metrics-interval 1 --verbose
```

## Packager Step (after run)
- Copy `.agent/` artifacts into `runs/{run_id}/evidence/ralph/`.
- Generate `dossier.json`, `hashes.manifest.json`, `ledger_entry.json`.
- Append ledger entry to `EVIDENCE_LEDGER/ledger.jsonl`; update `ledger.head`; optionally anchor externally.

## Loop Detection
Ralph stops on repetitive outputs (default similarity threshold ~0.9). Ensure prompts are explicit and finite to avoid loop stops.
