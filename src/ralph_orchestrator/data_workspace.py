from __future__ import annotations

"""Data workspace management for Ralph Orchestrator."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional


SYSTEM_OBJECTIVE_VNEXT = """The system writes and maintains artifacts under /data only.

Workspace rules
- Create and maintain this structure under /data:
  - prompts/, docs/, datasets/raw/, datasets/derived/, checklists/, logs/
- Treat /data/prompts/*.md as folder directives.
  - Always apply prompts/ROOT.md.
  - Also apply the directive for the folder you are writing into.

Outputs under /data/docs
- Maintain: README.md, API_REFERENCE.md, TECH_SPEC.md, USAGE_EXAMPLES.md, CHANGELOG.md
- Do not invent APIs, performance numbers, or license terms. Use TODO when missing evidence.

Dataset handling under /data/datasets
- Store raw uploads under /data/datasets/raw as immutable originals.
- Store derived/normalized datasets under /data/datasets/derived.
- Never rewrite or corrupt raw originals.

Self-referencing checklist (required)
- Maintain /data/checklists/TODO_CHECKLIST.md.
- When a TODO is introduced anywhere in /data/docs, add a matching unchecked checklist item.
- When a TODO disappears from /data/docs, mark its checklist item checked with a timestamp and iteration.
- Use the checklist as a driver for next actions: open checklist items must increase priority.

Loop behavior
- Each iteration performs exactly one mutation under /data (one write or one patch).
- After initial docs exist, run in maintenance mode: re-validate, patch inconsistencies, update TODOs, update changelog.
- Never declare SUCCESS from completion. Terminate only on external halt, limits, or invariant violation.
"""


PROMPT_DEFAULTS: Dict[str, str] = {
    "ROOT.md": "\n".join(
        [
            "You are writing artifacts under /data only.",
            "Output must be concrete file content or unified diffs applied to /data files.",
            "Minimal wording. No narratives. No completion language.",
            "When unsure, write TODO with the missing evidence needed.",
        ]
    ),
    "docs.md": "\n".join(
        [
            "Write Markdown suitable for direct use.",
            "Prefer short sections and examples based on real symbols found in inputs.",
            'Keep TODOs explicit and searchable using "TODO: ...".',
        ]
    ),
    "datasets.md": "\n".join(
        [
            "Prefer JSONL or Parquet for derived outputs.",
            "Include a small schema header in derived outputs (fields, types).",
            "Compute basic stats (record_count, date range, missingness) without guessing.",
        ]
    ),
    "healthkit.md": "\n".join(
        [
            "Treat HealthKit exports as read-only personal data.",
            "Extract only required fields for derived signals (timestamp, bpm, motion_context when present).",
            "Do not produce medical advice or diagnoses.",
            "Focus on time-series features (baseline, delta, volatility, recovery slope).",
        ]
    ),
    "checklist.md": "\n".join(
        [
            "Keep TODO_CHECKLIST.md as the canonical list.",
            "Ensure stable todo_id generation.",
            "Reconcile checklist vs docs every cycle before choosing the next mutation target.",
        ]
    ),
}


DOC_DEFAULTS: Dict[str, str] = {
    "README.md": "\n".join(
        [
            "# /data/docs README",
            "",
            "Workspace for orchestrator-managed documentation.",
            "TODO: Fill project overview and link to source evidence.",
        ]
    ),
    "API_REFERENCE.md": "\n".join(
        [
            "# API Reference",
            "",
            "TODO: Document available APIs with verified symbols and parameters.",
        ]
    ),
    "TECH_SPEC.md": "\n".join(
        [
            "# Technical Specification",
            "",
            "TODO: Capture architecture, data flow, and guardrails backed by sources.",
        ]
    ),
    "USAGE_EXAMPLES.md": "\n".join(
        [
            "# Usage Examples",
            "",
            "TODO: Add executable examples validated against real code paths.",
        ]
    ),
    "CHANGELOG.md": "\n".join(
        [
            "# Changelog",
            "## Unreleased",
            "- TODO: Record changes per iteration with evidence references.",
        ]
    ),
}


@dataclass
class ChecklistStatus:
    """Represent the current TODO/checklist state."""

    todo_count: int
    open_count: int
    todos: Dict[str, Dict[str, str]]


class DataWorkspaceManager:
    """Manage the /data workspace structure, directives, and checklist."""

    def __init__(self, data_root: Path | str = Path("data"), snapshot_interval: int = 5):
        self.data_root = Path(data_root)
        self.prompts_dir = self.data_root / "prompts"
        self.docs_dir = self.data_root / "docs"
        self.datasets_dir = self.data_root / "datasets"
        self.checklists_dir = self.data_root / "checklists"
        self.logs_dir = self.data_root / "logs"
        self.iteration_log = self.logs_dir / "iterations.jsonl"
        self.snapshot_log = self.logs_dir / "snapshots.jsonl"
        self.project_metadata = self.data_root / "PROJECT_METADATA.json"
        self.todo_checklist = self.checklists_dir / "TODO_CHECKLIST.md"
        self.snapshot_interval = snapshot_interval
        self.system_objective = SYSTEM_OBJECTIVE_VNEXT.strip()
        self.directives: Dict[str, str] = {}
        self._last_status: Optional[ChecklistStatus] = None

        self.ensure_structure()
        self.load_directives()

    def ensure_structure(self) -> None:
        """Create required /data structure and defaults if missing."""
        for path in [
            self.prompts_dir,
            self.docs_dir,
            self.datasets_dir / "raw",
            self.datasets_dir / "derived",
            self.checklists_dir,
            self.logs_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

        if not self.project_metadata.exists():
            default_metadata = {
                "status": "PROVISIONAL",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.project_metadata.write_text(json.dumps(default_metadata, indent=2))

        for name, content in PROMPT_DEFAULTS.items():
            target = self.prompts_dir / name
            if not target.exists():
                target.write_text(content)

        for name, content in DOC_DEFAULTS.items():
            target = self.docs_dir / name
            if not target.exists():
                target.write_text(content)

        if not self.todo_checklist.exists():
            header = [
                "# TODO Checklist",
                "Canonical self-updating TODO list. Use TODO lines in /data/docs to update.",
                "",
            ]
            self.todo_checklist.write_text("\n".join(header))

        # Touch log files to ensure they exist
        for log_path in [self.iteration_log, self.snapshot_log]:
            if not log_path.exists():
                log_path.touch()

    def load_directives(self) -> None:
        """Load prompt directives into memory keyed by filename stem."""
        self.directives = {}
        for prompt_path in sorted(self.prompts_dir.glob("*.md")):
            try:
                self.directives[prompt_path.stem.lower()] = prompt_path.read_text().strip()
            except OSError:
                continue

    def _normalize_path(self, target_path: Path | str) -> str:
        target = Path(target_path)
        try:
            return str(target.relative_to(self.data_root))
        except ValueError:
            return str(target)

    def _directive_key_for_target(self, target_path: Path | str) -> Optional[str]:
        """Map target paths to a directive key (docs, datasets, healthkit, checklist)."""
        target = Path(target_path)
        try:
            rel = target.relative_to(self.data_root)
        except ValueError:
            rel = target
        parts = [p.lower() for p in rel.parts]
        if not parts:
            return None
        if parts[0] == "docs":
            return "docs"
        if parts[0] == "datasets":
            if any("healthkit" in p for p in parts):
                return "healthkit"
            return "datasets"
        if parts[0] == "checklists":
            return "checklist"
        return None

    def compose_prompt(
        self,
        base_prompt: str,
        target_path: Path | str,
        checklist_status: Optional[ChecklistStatus],
        iteration: int,
    ) -> str:
        """Assemble the iteration prompt with system objective and directives."""
        root_directive = self.directives.get("root", "")
        folder_key = self._directive_key_for_target(target_path)
        folder_directive = self.directives.get(folder_key or "", "")
        checklist_open = checklist_status.open_count if checklist_status else 0
        todo_count = checklist_status.todo_count if checklist_status else 0

        sections: List[str] = [
            self.system_objective,
            f"# Target\nPath: {self._normalize_path(target_path)}\nIteration: {iteration}",
            f"Open checklist items: {checklist_open}\nTODO lines in docs: {todo_count}",
        ]

        if root_directive:
            sections.append(f"# Root Directive\n{root_directive}")
        if folder_directive:
            sections.append(f"# Folder Directive ({folder_key})\n{folder_directive}")

        priority_note = (
            "Priority: reduce open TODOs and perform a single mutation on the target path this cycle."
        )
        sections.append(priority_note)
        sections.append("# Prompt\n" + base_prompt.strip())

        return "\n\n".join(sections)

    def scan_docs_for_todos(self) -> Dict[str, Dict[str, str]]:
        """Return TODO entries discovered in /data/docs/*.md."""
        todos: Dict[str, Dict[str, str]] = {}
        todo_pattern = re.compile(r"TODO[:\s]+(.+)", re.IGNORECASE)
        for doc_path in sorted(self.docs_dir.glob("*.md")):
            try:
                lines = doc_path.read_text().splitlines()
            except OSError:
                continue
            for idx, line in enumerate(lines, start=1):
                match = todo_pattern.search(line)
                if not match:
                    continue
                todo_text = match.group(1).strip()
                rel_path = self._normalize_path(doc_path)
                todo_id = hashlib.sha1(
                    (todo_text.lower() + "|" + rel_path).encode("utf-8")
                ).hexdigest()
                todos[todo_id] = {
                    "text": todo_text,
                    "source": f"{rel_path}:{idx}",
                }
        return todos

    def _parse_checklist(self) -> tuple[List[str], Dict[str, Dict[str, str]]]:
        """Parse existing checklist lines and return preamble plus entries keyed by id."""
        preamble: List[str] = []
        entries: Dict[str, Dict[str, str]] = {}
        if not self.todo_checklist.exists():
            return preamble, entries
        try:
            lines = self.todo_checklist.read_text().splitlines()
        except OSError:
            return preamble, entries

        in_items = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- ["):
                in_items = True
                mark = "x" in stripped[:4].lower()
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 3:
                    continue
                todo_id = parts[0].split("]")[-1].strip()
                if not todo_id:
                    continue
                if mark:
                    if len(parts) < 4:
                        continue
                    closed_at = parts[1]
                    source = parts[2]
                    todo_text = "|".join(parts[3:]).strip()
                    entries[todo_id] = {
                        "checked": True,
                        "closed_at": closed_at,
                        "source": source,
                        "text": todo_text,
                        "line": line,
                    }
                else:
                    source = parts[1]
                    todo_text = "|".join(parts[2:]).strip()
                    entries[todo_id] = {
                        "checked": False,
                        "source": source,
                        "text": todo_text,
                        "line": line,
                    }
            else:
                if not in_items:
                    preamble.append(line)
        return preamble, entries

    def reconcile_todos(self, iteration: int) -> ChecklistStatus:
        """Sync checklist with current TODOs found in docs."""
        current_todos = self.scan_docs_for_todos()
        preamble, checklist_entries = self._parse_checklist()
        timestamp = datetime.now(timezone.utc).isoformat()

        ordered_ids: List[str] = list(checklist_entries.keys())
        new_lines: List[str] = []

        open_count = 0
        for todo_id in ordered_ids:
            entry = checklist_entries[todo_id]
            if todo_id in current_todos:
                if entry.get("checked"):
                    new_lines.append(entry["line"])
                else:
                    open_count += 1
                    source = entry.get("source", current_todos[todo_id]["source"])
                    todo_text = entry.get("text", current_todos[todo_id]["text"])
                    new_lines.append(f"- [ ] {todo_id} | {source} | {todo_text}")
                continue

            if entry.get("checked"):
                new_lines.append(entry["line"])
            else:
                source = entry.get("source", "unknown")
                todo_text = entry.get("text", "")
                closed_line = (
                    f"- [x] {todo_id} | {timestamp} iter={iteration} | {source} | {todo_text}"
                )
                new_lines.append(closed_line)

        for todo_id, todo in current_todos.items():
            if todo_id in checklist_entries:
                continue
            open_count += 1
            new_lines.append(f"- [ ] {todo_id} | {todo['source']} | {todo['text']}")

        content_lines = preamble or [
            "# TODO Checklist",
            "Canonical self-updating TODO list. Use TODO lines in /data/docs to update.",
        ]
        content_lines = [line for line in content_lines if line is not None]
        if content_lines and content_lines[-1].strip() != "":
            content_lines.append("")
        content_lines.extend(new_lines)
        self.todo_checklist.write_text("\n".join(content_lines) + "\n")

        status = ChecklistStatus(
            todo_count=len(current_todos),
            open_count=open_count,
            todos=current_todos,
        )
        self._last_status = status
        return status

    def choose_target_path(self, status: Optional[ChecklistStatus] = None) -> Path:
        """Pick a target path for the next mutation, prioritizing open TODOs."""
        status = status or self._last_status
        if status and status.todos:
            first_todo = next(iter(status.todos.values()))
            source = first_todo.get("source", "docs/README.md").split(":")[0]
            return self.data_root / source
        return self.docs_dir / "README.md"

    def log_iteration(
        self,
        iteration: int,
        target_path: Path | str,
        mutation_type: str,
        todo_count: int,
        checklist_open_count: int,
        doc_consistency_score: float = 0.0,
        schema_coverage: float = 0.0,
        dataset_influence: float = 0.0,
        drift_metric: float = 0.0,
        entropy_delta: float = 0.0,
    ) -> None:
        """Append per-iteration summary to iterations.jsonl."""
        entry = {
            "iteration": iteration,
            "target_path": self._normalize_path(target_path),
            "mutation_type": mutation_type,
            "todo_count": todo_count,
            "checklist_open_count": checklist_open_count,
            "doc_consistency_score": doc_consistency_score,
            "schema_coverage": schema_coverage,
            "dataset_influence": dataset_influence,
            "drift_metric": drift_metric,
            "entropy_delta": entropy_delta,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self.iteration_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def maybe_snapshot(
        self,
        iteration: int,
        metrics: Dict[str, object],
        checklist_status: Optional[ChecklistStatus],
    ) -> None:
        """Append snapshot at configured intervals."""
        if self.snapshot_interval <= 0:
            return
        if iteration % self.snapshot_interval != 0:
            return
        entry = {
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "checklist_open_count": checklist_status.open_count if checklist_status else 0,
            "todo_count": checklist_status.todo_count if checklist_status else 0,
        }
        with self.snapshot_log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

