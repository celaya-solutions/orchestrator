import asyncio
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ralph_orchestrator.web import actions_server


@pytest.fixture(autouse=True)
def clear_api_key(monkeypatch):
    """Ensure API key enforcement is disabled for tests."""
    monkeypatch.setattr(actions_server, "ACTION_API_KEY", None)


@pytest.fixture
def client(monkeypatch):
    """Provide a TestClient with lightweight orchestrator stubs."""
    monkeypatch.setattr(
        actions_server.RalphOrchestrator,
        "_initialize_adapters",
        lambda self: {"auto": object()},
    )
    monkeypatch.setattr(
        actions_server.RalphOrchestrator,
        "_select_adapter",
        lambda self, primary_tool: ("auto", object()),
    )
    # Avoid signal handler registration in tests (non-main thread contexts)
    import ralph_orchestrator.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod.signal, "signal", lambda *_, **__: None)

    def fake_state(self):
        return {
            "iteration": self.metrics.iterations,
            "max_iterations": self.max_iterations,
            "metrics": self.metrics.to_dict(),
            "tasks": {},
            "runtime": 0,
        }

    monkeypatch.setattr(
        actions_server.RalphOrchestrator,
        "get_orchestrator_state",
        fake_state,
        raising=False,
    )

    return TestClient(actions_server.app)


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_run_lifecycle(client, tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("do something simple")

    async def quick_run(self):
        self.metrics.iterations += 1

    monkeypatch.setattr(actions_server.RalphOrchestrator, "arun", quick_run)

    resp = client.post("/runs", json={"prompt_file": str(prompt_file)})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    status = None
    for _ in range(10):
        status_resp = client.get(f"/runs/{run_id}")
        status = status_resp.json()
        if status["state"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.01)

    assert status is not None
    assert status["state"] == "completed"
    assert status["progress"]["iterations"] == 1
    assert status["prompt_file"] == str(prompt_file)


def test_cancel_run(client, tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("long task")

    stop_event = asyncio.Event()

    async def blocking_run(self):
        await stop_event.wait()

    monkeypatch.setattr(actions_server.RalphOrchestrator, "arun", blocking_run)

    resp = client.post("/runs", json={"prompt_file": str(prompt_file)})
    run_id = resp.json()["run_id"]

    cancel_resp = client.delete(f"/runs/{run_id}")
    assert cancel_resp.status_code == 200
    status = cancel_resp.json()

    # Unblock the coroutine to allow clean shutdown in the background task
    stop_event.set()

    assert status["state"] == "cancelled"
    assert status["error"] in (None, "Cancelled by user")
