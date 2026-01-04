# ABOUTME: FastAPI server exposing Ralph orchestration as CustomGPT-style actions
# ABOUTME: Provides endpoints to start runs, check status, and cancel runs for GPT integrations

"""CustomGPT actions server for Ralph Orchestrator."""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Security,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field, field_validator

from ..main import (
    AgentType,
    DEFAULT_CHECKPOINT_INTERVAL,
    DEFAULT_MAX_COST,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_MAX_RUNTIME,
    DEFAULT_OLLAMA_MODEL,
    RalphConfig,
    RunType,
)
from ..orchestrator import RalphOrchestrator

logger = logging.getLogger(__name__)

FORBIDDEN_COMPENSATION_TERMS = {"token", "praise", "points", "reputation"}
FORBIDDEN_OUTPUT_PHRASES = [
    "unpaid",
    "volunteer",
    "token compensation",
    "performance-based pay only",
    "on-call without pay",
    "equity instead of wages",
]
COMPLIANCE_MANIFEST_NAME = "compliance_manifest.json"
ILLEGAL_STATE_ERROR = "illegal_state"


class RunValidationError(Exception):
    """Raised when a run request violates classification guardrails."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ConfigOverrides(BaseModel):
    """Safe subset of RalphConfig overrides allowed over HTTP."""

    agent: AgentType = Field(default=AgentType.AUTO, description="Which agent adapter to use")
    max_iterations: Optional[int] = Field(
        default=None,
        ge=1,
        le=DEFAULT_MAX_ITERATIONS,
        description="Maximum iterations before stopping",
    )
    max_runtime: Optional[int] = Field(
        default=None,
        ge=60,
        le=DEFAULT_MAX_RUNTIME,
        description="Maximum runtime in seconds",
    )
    checkpoint_interval: Optional[int] = Field(
        default=None,
        ge=1,
        description="How often to checkpoint iterations",
    )
    max_cost: Optional[float] = Field(
        default=None,
        ge=0,
        description="Maximum spend allowed for the run (USD)",
    )
    ollama_model: Optional[str] = Field(
        default=None,
        description="Optional Ollama model override when agent=ollama",
    )

    @field_validator("ollama_model")
    def strip_blank(cls, value: Optional[str]) -> Optional[str]:
        return value if value is None or value.strip() else None


class ActionRunRequest(BaseModel):
    """Payload for starting a new orchestration run."""

    classification: RunType = Field(..., description="Run classification (ai_only, w2_employee, contractor_1099)")
    prompt_file: str = Field(..., description="Path to an existing prompt file")
    pay: Optional[Any] = Field(
        default=None, description="Monetary compensation information (required for human classifications)"
    )
    pay_type: Optional[str] = Field(
        default=None, description="Compensation type for W2 employees (hourly or salary)"
    )
    compensation: List[str] = Field(
        default_factory=list,
        description="Non-cash or supplemental compensation descriptors",
    )
    schedule: Optional[str] = Field(
        default=None, description="Schedule description, used to detect on-call scenarios"
    )
    human_indicators: List[str] = Field(
        default_factory=list,
        description="Signals that a human is involved (e.g., resume, SSN, background check)",
    )
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque metadata forwarded to status responses",
    )

    @field_validator("pay_type")
    def normalize_pay_type(cls, value: Optional[str]) -> Optional[str]:
        return value.lower() if isinstance(value, str) else value

    @field_validator("compensation", "human_indicators", mode="before")
    def coerce_to_list(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            return [value]
        return [str(value)]


class ActionRunStatus(BaseModel):
    """Serialized status for a single run."""

    run_id: str
    state: str
    agent: str
    run_type: RunType
    prompt_file: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    progress: Dict[str, Any] = Field(default_factory=dict)
    artifacts: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionStartResponse(BaseModel):
    """Response returned when a run is accepted."""

    run_id: str
    status_url: str


class ActionListResponse(BaseModel):
    """Response for listing runs."""

    runs: List[ActionRunStatus]


@dataclass
class ActionRunState:
    """Internal tracking for an orchestration run."""

    run_id: str
    orchestrator: RalphOrchestrator
    prompt_file: str
    task: asyncio.Task
    request: ActionRunRequest
    run_type: RunType
    artifacts: List[str] = field(default_factory=list)
    generated_artifacts: List[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    state: str = "running"
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    def to_status(self) -> ActionRunStatus:
        """Project internal state into a response model."""
        progress: Dict[str, Any] = {}
        try:
            orch_state = self.orchestrator.get_orchestrator_state()
            progress = {
                "iterations": orch_state.get("iteration"),
                "max_iterations": orch_state.get("max_iterations"),
                "metrics": orch_state.get("metrics", {}),
                "tasks": orch_state.get("tasks", {}),
                "runtime": orch_state.get("runtime"),
            }
        except Exception:  # pragma: no cover - defensive
            try:
                progress = {
                    "iterations": self.orchestrator.metrics.iterations,
                    "metrics": self.orchestrator.metrics.to_dict(),
                }
            except Exception:
                progress = {}

        artifacts = self.artifacts or [self.prompt_file]

        return ActionRunStatus(
            run_id=self.run_id,
            state=self.state,
            agent=str(
                self.request.config.agent.value
                if isinstance(self.request.config.agent, AgentType)
                else self.request.config.agent
            ),
            run_type=self.run_type,
            prompt_file=self.prompt_file,
            started_at=self.started_at,
            completed_at=self.completed_at,
            progress=progress,
            artifacts=artifacts,
            error=self.error,
            metadata=self.request.metadata,
        )


def _normalize_terms(values: List[str]) -> List[str]:
    """Normalize string values for comparison."""
    normalized: List[str] = []
    for value in values:
        text = str(value).strip().lower()
        if text:
            normalized.append(text)
    return normalized


def validate_run_inputs(request: ActionRunRequest) -> None:
    """Preflight validator to enforce classification safety before orchestration starts."""
    classification = request.classification
    if classification is None:
        raise RunValidationError("classification is required")

    pay_type = request.pay_type.lower() if isinstance(request.pay_type, str) else request.pay_type
    comp_terms = _normalize_terms(request.compensation)
    schedule_text = (request.schedule or "").lower()
    human_indicators = request.human_indicators or []

    if classification == RunType.W2_EMPLOYEE and request.pay is None:
        raise RunValidationError("w2_employee classification requires pay")

    if classification == RunType.W2_EMPLOYEE and pay_type not in {"hourly", "salary"}:
        raise RunValidationError("pay_type must be hourly or salary for w2_employee classification")

    if classification == RunType.CONTRACTOR_1099 and request.pay is None:
        raise RunValidationError("contractor_1099 classification requires pay")

    if any(
        forbidden in entry
        for entry in comp_terms
        for forbidden in FORBIDDEN_COMPENSATION_TERMS
    ):
        raise RunValidationError("non-monetary compensation for human labor")

    if "on call" in schedule_text and classification != RunType.AI_ONLY:
        raise RunValidationError("on-call scheduling is only allowed for ai_only classification")

    if human_indicators and classification == RunType.AI_ONLY:
        raise RunValidationError("human indicators provided for ai_only classification")


def illegal_state(reason: str) -> Dict[str, str]:
    """Consistent error envelope for illegal run requests."""
    return {"error": ILLEGAL_STATE_ERROR, "reason": reason}


class ActionRunManager:
    """Coordinates orchestration runs for the actions API."""

    def __init__(self):
        self.runs: Dict[str, ActionRunState] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, request: ActionRunRequest) -> ActionRunState:
        """Start a new orchestrator run in the background."""
        validate_run_inputs(request)
        run_id = str(uuid4())
        prompt_path = os.path.abspath(request.prompt_file)
        if not os.path.exists(prompt_path):
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        if not os.path.isfile(prompt_path):
            raise FileExistsError(f"Prompt path is not a file: {prompt_path}")

        try:
            config = RalphConfig(
                agent=request.config.agent,
                run_type=request.classification,
                prompt_file=prompt_path,
                max_iterations=request.config.max_iterations or DEFAULT_MAX_ITERATIONS,
                max_runtime=request.config.max_runtime or DEFAULT_MAX_RUNTIME,
                checkpoint_interval=request.config.checkpoint_interval or DEFAULT_CHECKPOINT_INTERVAL,
                max_cost=request.config.max_cost or DEFAULT_MAX_COST,
                ollama_model=request.config.ollama_model or DEFAULT_OLLAMA_MODEL,
                dry_run=False,
            )
            orchestrator = RalphOrchestrator(
                config,
                primary_tool=config.agent.value if hasattr(config.agent, "value") else str(config.agent),
                max_iterations=config.max_iterations,
                max_runtime=config.max_runtime,
                checkpoint_interval=config.checkpoint_interval,
                max_cost=config.max_cost,
                ollama_model=config.ollama_model,
            )
        except Exception as exc:  # pragma: no cover - validation handled by FastAPI
            logger.error("Failed to start run: %s", exc)
            raise

        task = asyncio.create_task(self._execute(run_id, orchestrator))
        state = ActionRunState(
            run_id=run_id,
            orchestrator=orchestrator,
            prompt_file=prompt_path,
            task=task,
            request=request,
            run_type=request.classification,
            artifacts=[prompt_path],
        )

        async with self._lock:
            self.runs[run_id] = state
        return state

    def _collect_generated_texts(self, state: ActionRunState) -> List[str]:
        """Gather generated outputs for forbidden phrase scanning."""
        texts: List[str] = []
        if getattr(state.orchestrator, "last_response_output", None):
            texts.append(state.orchestrator.last_response_output)

        iteration_stats = getattr(state.orchestrator, "iteration_stats", None)
        if iteration_stats and getattr(iteration_stats, "iterations", None):
            for iteration in iteration_stats.iterations:
                preview = iteration.get("output_preview")
                if preview:
                    texts.append(preview)

        dynamic_context = getattr(state.orchestrator.context_manager, "dynamic_context", None)
        if dynamic_context:
            texts.extend([ctx for ctx in dynamic_context if ctx])
        return texts

    def _scan_forbidden_output(self, state: ActionRunState) -> Optional[str]:
        """Return the offending phrase if any forbidden output is detected."""
        for text in self._collect_generated_texts(state):
            lower_text = text.lower()
            for phrase in FORBIDDEN_OUTPUT_PHRASES:
                if phrase in lower_text:
                    return phrase
        return None

    def _record_metrics_artifact(self, state: ActionRunState) -> None:
        """Track metrics files produced by the orchestrator."""
        metrics_path = getattr(state.orchestrator, "last_metrics_file", None)
        if not metrics_path:
            return
        metrics_str = str(metrics_path)
        if metrics_str not in state.artifacts:
            state.artifacts.append(metrics_str)
        if metrics_str not in state.generated_artifacts:
            state.generated_artifacts.append(metrics_str)

    def _purge_generated_artifacts(self, state: ActionRunState) -> None:
        """Delete generated artifacts when a run is invalidated."""
        to_remove = set(state.generated_artifacts)
        for path_str in list(to_remove):
            try:
                path = Path(path_str)
                if path.exists():
                    path.unlink()
            except Exception as exc:  # pragma: no cover - defensive cleanup
                logger.warning("Failed to delete artifact %s: %s", path_str, exc)
        state.generated_artifacts = []
        if to_remove:
            state.artifacts = [path for path in state.artifacts if path not in to_remove]

    def _generate_compliance_manifest(self, state: ActionRunState) -> Optional[Path]:
        """Create a compliance manifest for successful runs."""
        request = state.request
        classification = state.run_type
        comp_terms = _normalize_terms(request.compensation)
        schedule_text = (request.schedule or "").lower()
        pay_type = request.pay_type.lower() if isinstance(request.pay_type, str) else request.pay_type
        pay_present = request.pay is not None
        human_labor = classification != RunType.AI_ONLY

        if human_labor and not pay_present:
            return None
        if classification == RunType.W2_EMPLOYEE and pay_type not in {"hourly", "salary"}:
            return None
        if human_labor and "on call" in schedule_text:
            return None
        if classification == RunType.AI_ONLY and request.human_indicators:
            return None

        manifest = {
            "classification": classification.value,
            "monetary_compensation_confirmed": bool(pay_present) if human_labor else False,
            "on_call": "on call" in schedule_text,
            "non_cash_compensation": bool(comp_terms),
            "human_labor": human_labor,
        }

        manifest_path = Path(state.prompt_file).resolve().parent / COMPLIANCE_MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest, indent=2))
        manifest_str = str(manifest_path)
        if manifest_str not in state.artifacts:
            state.artifacts.append(manifest_str)
        if manifest_str not in state.generated_artifacts:
            state.generated_artifacts.append(manifest_str)
        return manifest_path

    def _fail_run(self, state: ActionRunState, reason: str, purge_artifacts: bool = False) -> None:
        """Mark a run as failed with an optional cleanup step."""
        state.state = "failed"
        state.error = reason
        if purge_artifacts:
            self._purge_generated_artifacts(state)

    async def _execute(self, run_id: str, orchestrator: RalphOrchestrator) -> None:
        """Run the orchestrator loop and update status."""
        async with self._lock:
            state = self.runs.get(run_id)
        if not state:
            return

        try:
            orchestrator.enforce_run_type(state.run_type)
            await orchestrator.arun()
            self._record_metrics_artifact(state)
            state.state = "completed" if not orchestrator.stop_requested else "cancelled"
            if state.state == "completed":
                violation = self._scan_forbidden_output(state)
                if violation:
                    self._fail_run(
                        state,
                        f"Forbidden output phrase detected: {violation}",
                        purge_artifacts=True,
                    )
                    return

                manifest_path = self._generate_compliance_manifest(state)
                if manifest_path is None:
                    self._fail_run(
                        state,
                        "Unable to generate compliance manifest truthfully",
                        purge_artifacts=True,
                    )
                    return
        except asyncio.CancelledError:
            state.state = "cancelled"
            state.error = "Cancelled by user"
            raise
        except Exception as exc:  # pragma: no cover - runtime failures
            logger.error("Run %s failed: %s", run_id, exc)
            state.state = "failed"
            state.error = str(exc)
            self._record_metrics_artifact(state)
        finally:
            state.completed_at = datetime.now(timezone.utc)

    async def cancel_run(self, run_id: str) -> ActionRunState:
        """Request cancellation of a running orchestrator."""
        async with self._lock:
            state = self.runs.get(run_id)
        if not state:
            raise KeyError(run_id)

        if state.state in {"completed", "failed", "cancelled"}:
            return state

        state.state = "cancelling"
        state.orchestrator.stop_requested = True
        if state.task and not state.task.done():
            state.task.cancel()
            try:
                await state.task
            except asyncio.CancelledError:
                pass

        state.state = "cancelled"
        state.completed_at = datetime.now(timezone.utc)
        return state

    async def get_run(self, run_id: str) -> ActionRunState:
        """Fetch a run by ID."""
        async with self._lock:
            state = self.runs.get(run_id)
        if not state:
            raise KeyError(run_id)
        return state

    async def list_runs(self) -> List[ActionRunState]:
        """Return all known runs (newest first)."""
        async with self._lock:
            states = list(self.runs.values())
        return sorted(states, key=lambda s: s.started_at, reverse=True)


api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)
ACTION_API_KEY = os.getenv("RALPH_ACTIONS_API_KEY")


async def require_api_key(api_key: Optional[str] = Security(api_key_header)) -> None:
    """Simple API key guard to keep the actions endpoint private."""
    if ACTION_API_KEY and api_key != ACTION_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def create_app() -> FastAPI:
    """Construct the FastAPI application."""
    app = FastAPI(
        title="Ralph CustomGPT Actions",
        description="Lightweight API surface for integrating Ralph with GPT Actions.",
        version="0.1.0",
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
        """Normalize validation errors to 400 responses."""
        reason = "invalid request payload"
        for error in exc.errors():
            if "classification" in error.get("loc", []):
                reason = "classification is required"
                break
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=illegal_state(reason),
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    manager = ActionRunManager()
    # Expose manager for tests/ops without changing core orchestration
    app.state.actions_manager = manager

    @app.get("/healthz", tags=["system"])
    async def health_check():
        """Basic health probe."""
        return {"status": "ok"}

    @app.post(
        "/runs",
        response_model=ActionStartResponse,
        dependencies=[Depends(require_api_key)],
        tags=["actions"],
    )
    async def start_action_run(payload: ActionRunRequest, request: Request):
        """Start an orchestration run and return a polling URL."""
        try:
            state = await manager.start_run(payload)
        except RunValidationError as exc:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content=illegal_state(exc.reason),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to start run: {exc}",
            ) from exc

        status_url = str(request.url_for("get_action_run", run_id=state.run_id))
        return ActionStartResponse(run_id=state.run_id, status_url=status_url)

    @app.get(
        "/runs/{run_id}",
        response_model=ActionRunStatus,
        dependencies=[Depends(require_api_key)],
        name="get_action_run",
        tags=["actions"],
    )
    async def get_action_run(run_id: str):
        """Return current status for a run."""
        try:
            state = await manager.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
        return state.to_status()

    @app.delete(
        "/runs/{run_id}",
        response_model=ActionRunStatus,
        dependencies=[Depends(require_api_key)],
        tags=["actions"],
    )
    async def cancel_action_run(run_id: str):
        """Cancel a running orchestration."""
        try:
            state = await manager.cancel_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found") from exc
        return state.to_status()

    @app.get(
        "/runs",
        response_model=ActionListResponse,
        dependencies=[Depends(require_api_key)],
        tags=["actions"],
    )
    async def list_action_runs():
        """List all tracked runs (newest first)."""
        runs = await manager.list_runs()
        return ActionListResponse(runs=[state.to_status() for state in runs])

    return app


app = create_app()


def main():
    """Start the actions server via `python -m ralph_orchestrator.web.actions_server`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    host = os.getenv("RALPH_ACTIONS_HOST", "0.0.0.0")
    port = int(os.getenv("RALPH_ACTIONS_PORT", "8081"))
    logger.info("Starting CustomGPT actions server on %s:%s", host, port)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
