from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict, Optional

from ..logging_config import RalphLogger


class OllamaJSONClient:
    """
    Thin wrapper around `ollama run` that insists on JSON responses.

    This client is intentionally minimal and stateless.
    """

    def __init__(
        self,
        model: str = "gemma:2b",
        command: str = "ollama",
        timeout: int = 120,
        max_attempts: int = 1,
    ) -> None:
        self.model = model
        self.command = command
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.logger = RalphLogger.get_logger(RalphLogger.AGENT_OLLAMA)

    def _run_once(self, prompt: str) -> str:
        cmd = [self.command, "run", self.model]
        try:
            completed = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            self.logger.error("Ollama timed out after %ss", self.timeout)
            raise RuntimeError(f"Ollama timed out after {self.timeout}s") from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            message = stderr.strip() or "Ollama returned a non-zero exit code"
            self.logger.error("Ollama failure: %s", message)
            raise RuntimeError(message)

        if stderr.strip():
            self.logger.warning("Ollama stderr: %s", stderr.strip())
        return stdout.strip()

    def _extract_json(self, raw: str) -> Dict[str, Any]:
        # If the model emits fenced blocks, strip them.
        fenced = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = fenced.group(0) if fenced else raw
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise ValueError("Model response is not valid JSON") from exc

    def run_json(self, prompt: str) -> Dict[str, Any]:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_attempts + 1):
            raw = self._run_once(prompt)
            try:
                return self._extract_json(raw)
            except ValueError as exc:
                last_error = exc
                self.logger.warning("Invalid JSON from Ollama (attempt %s/%s)", attempt, self.max_attempts)
        raise RuntimeError(f"Ollama failed to produce valid JSON: {last_error}") from last_error
