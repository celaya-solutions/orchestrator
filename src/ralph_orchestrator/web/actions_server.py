# ABOUTME: FastAPI server exposing Ralph orchestration as CustomGPT-style actions
# ABOUTME: Provides endpoints to start runs, check status, and cancel runs for GPT integrations

"""CustomGPT actions server for Ralph Orchestrator."""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from fastapi.middleware.cors import CORSMiddleware
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
)
from ..orchestrator import RalphOrchestrator

logger = logging.getLogger(__name__)


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

    prompt_file: str = Field(..., description="Path to an existing prompt file")
    config: ConfigOverrides = Field(default_factory=ConfigOverrides)
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Opaque metadata forwarded to status responses",
    )


class ActionRunStatus(BaseModel):
    """Serialized status for a single run."""

    run_id: str
    state: str
    agent: str
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

        artifacts = [self.prompt_file]

        return ActionRunStatus(
            run_id=self.run_id,
            state=self.state,
            agent=str(
                self.request.config.agent.value
                if isinstance(self.request.config.agent, AgentType)
                else self.request.config.agent
            ),
            prompt_file=self.prompt_file,
            started_at=self.started_at,
            completed_at=self.completed_at,
            progress=progress,
            artifacts=artifacts,
            error=self.error,
            metadata=self.request.metadata,
        )


class ActionRunManager:
    """Coordinates orchestration runs for the actions API."""

    def __init__(self):
        self.runs: Dict[str, ActionRunState] = {}
        self._lock = asyncio.Lock()

    async def start_run(self, request: ActionRunRequest) -> ActionRunState:
        """Start a new orchestrator run in the background."""
        run_id = str(uuid4())
        prompt_path = os.path.abspath(request.prompt_file)
        if not os.path.exists(prompt_path):
            raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
        if not os.path.isfile(prompt_path):
            raise FileExistsError(f"Prompt path is not a file: {prompt_path}")

        try:
            config = RalphConfig(
                agent=request.config.agent,
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
        )

        async with self._lock:
            self.runs[run_id] = state
        return state

    async def _execute(self, run_id: str, orchestrator: RalphOrchestrator) -> None:
        """Run the orchestrator loop and update status."""
        async with self._lock:
            state = self.runs.get(run_id)
        if not state:
            return

        try:
            await orchestrator.arun()
            state.state = "completed" if not orchestrator.stop_requested else "cancelled"
        except asyncio.CancelledError:
            state.state = "cancelled"
            state.error = "Cancelled by user"
            raise
        except Exception as exc:  # pragma: no cover - runtime failures
            logger.error("Run %s failed: %s", run_id, exc)
            state.state = "failed"
            state.error = str(exc)
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
