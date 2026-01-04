# ABOUTME: Ollama adapter implementation
# ABOUTME: Adds local model support with configurable defaults

"""Ollama adapter for Ralph Orchestrator."""

import os
import signal
import subprocess
import threading
import re
from typing import Dict, Optional

from .base import ToolAdapter, ToolResponse
from ..logging_config import RalphLogger


class OllamaAdapter(ToolAdapter):
    """Adapter for the Ollama CLI."""

    DEFAULT_MODEL = "gemma3:1b"

    def __init__(self, default_model: Optional[str] = None, default_timeout: int = 600):
        self.command = os.getenv("RALPH_OLLAMA_COMMAND", "ollama")
        self.default_model = (
            os.getenv("RALPH_OLLAMA_MODEL", default_model or self.DEFAULT_MODEL)
        )
        self.default_timeout = int(
            os.getenv("RALPH_OLLAMA_TIMEOUT", str(default_timeout))
        )

        # Track the running process for graceful shutdown
        self.current_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._original_sigint = None
        self._original_sigterm = None

        super().__init__("ollama")
        self._register_signal_handlers()

        self.logger = RalphLogger.get_logger(RalphLogger.ADAPTER_OLLAMA)
        self.logger.info(
            "Ollama adapter initialized - Command: %s, Default model: %s",
            self.command,
            self.default_model,
        )

    def _register_signal_handlers(self) -> None:
        """Register signal handlers to terminate the subprocess if needed."""
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint is not None:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm is not None:
            signal.signal(signal.SIGTERM, self._original_sigterm)

    def _signal_handler(self, signum, frame) -> None:
        """Handle shutdown signals and terminate running subprocess."""
        with self._lock:
            process = self.current_process

        if process and process.poll() is None:
            self.logger.warning("Received signal %s, terminating Ollama process...", signum)
            try:
                process.terminate()
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.logger.warning("Force killing Ollama process...")
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.logger.warning("Process may still be running after force kill")

    def kill_subprocess_sync(self) -> None:
        """Allow orchestrator to synchronously kill the running subprocess."""
        with self._lock:
            process = self.current_process

        if process and process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass

    def check_availability(self) -> bool:
        """Check if Ollama CLI is available."""
        try:
            result = subprocess.run(
                [self.command, "--version"],
                capture_output=True,
                timeout=5,
                text=True,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape sequences."""
        return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text or "")

    def execute(self, prompt: str, **kwargs) -> ToolResponse:
        """Execute Ollama with the given prompt."""
        if not self.available:
            return ToolResponse(
                success=False,
                output="",
                error="Ollama CLI is not available",
            )

        model = kwargs.get("model") or self.default_model
        timeout = kwargs.get("timeout", self.default_timeout)
        env: Dict[str, str] = os.environ.copy()
        env.update(kwargs.get("env", {}))

        enhanced_prompt = self._enhance_prompt_with_instructions(prompt)
        cmd = [self.command, "run", model]

        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )

            with self._lock:
                self.current_process = process

            stdout, stderr = process.communicate(enhanced_prompt, timeout=timeout)

            if process.returncode == 0:
                return ToolResponse(
                    success=True,
                    output=stdout,
                    metadata={"model": model},
                )

            stderr_clean = self._strip_ansi(stderr).strip()
            error_lines = [line for line in stderr_clean.splitlines() if line.strip()]
            error_message = error_lines[-1] if error_lines else "Ollama command failed"

            # Provide a clearer hint when the model is missing
            if "model manifest" in stderr_clean or "not exist" in stderr_clean:
                error_message = (
                    f"Model '{model}' not available. Pull it first: ollama pull {model}"
                )

            self.logger.error("Ollama error: %s", error_message)
            return ToolResponse(
                success=False,
                output=stdout,
                error=error_message,
                metadata={"model": model, "stderr": stderr_clean},
            )
        except subprocess.TimeoutExpired:
            return ToolResponse(
                success=False,
                output="",
                error=f"Ollama command timed out after {timeout}s",
                metadata={"model": model},
            )
        except Exception as exc:  # pragma: no cover - defensive
            return ToolResponse(
                success=False,
                output="",
                error=str(exc),
                metadata={"model": model},
            )
        finally:
            with self._lock:
                self.current_process = None

    def estimate_cost(self, prompt: str) -> float:
        """Ollama runs locally; treat cost as zero."""
        return 0.0
