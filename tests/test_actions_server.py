import asyncio
import json
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

    resp = client.post(
        "/runs",
        json={"prompt_file": str(prompt_file), "classification": "ai_only"},
    )
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
    assert status["run_type"] == "ai_only"


def test_cancel_run(client, tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("long task")

    stop_event = asyncio.Event()

    async def blocking_run(self):
        await stop_event.wait()

    monkeypatch.setattr(actions_server.RalphOrchestrator, "arun", blocking_run)

    resp = client.post(
        "/runs",
        json={"prompt_file": str(prompt_file), "classification": "ai_only"},
    )
    run_id = resp.json()["run_id"]

    cancel_resp = client.delete(f"/runs/{run_id}")
    assert cancel_resp.status_code == 200
    status = cancel_resp.json()

    # Unblock the coroutine to allow clean shutdown in the background task
    stop_event.set()

    assert status["state"] == "cancelled"
    assert status["error"] in (None, "Cancelled by user")


def test_missing_classification_returns_400(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("missing classification")

    resp = client.post("/runs", json={"prompt_file": str(prompt_file)})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "illegal_state"
    assert "classification" in body["reason"]


def test_w2_missing_pay_rejected(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("w2 check")

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "w2_employee",
            "pay_type": "hourly",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "illegal_state"
    assert "requires pay" in body["reason"]


def test_invalid_pay_type_rejected(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("bad pay type")

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "w2_employee",
            "pay": 100,
            "pay_type": "stipend",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["reason"] == "pay_type must be hourly or salary for w2_employee classification"


def test_forbidden_compensation_rejected(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("forbidden comp")

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "contractor_1099",
            "pay": 100,
            "compensation": ["token compensation"],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["reason"] == "non-monetary compensation for human labor"


def test_on_call_requires_ai_only(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("on call schedule")

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "w2_employee",
            "pay": 100,
            "pay_type": "hourly",
            "schedule": "Participates in on call rotation",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "on-call" in body["reason"]


def test_human_indicators_block_ai_only(client, tmp_path):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("ai only")

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "ai_only",
            "human_indicators": ["resume"],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "illegal_state"
    assert "human indicators" in body["reason"]


def test_forbidden_output_blocks_run(client, tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("run with forbidden output")

    async def forbidden_run(self):
        self.metrics.iterations += 1
        self.last_response_output = "This expects unpaid volunteer work"

    monkeypatch.setattr(actions_server.RalphOrchestrator, "arun", forbidden_run)

    resp = client.post(
        "/runs",
        json={"prompt_file": str(prompt_file), "classification": "ai_only"},
    )
    run_id = resp.json()["run_id"]

    status = None
    for _ in range(10):
        status_resp = client.get(f"/runs/{run_id}")
        status = status_resp.json()
        if status["state"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.01)

    assert status is not None
    assert status["state"] == "failed"
    assert "Forbidden output phrase detected" in status["error"]
    manifest_path = prompt_file.parent / "compliance_manifest.json"
    assert not manifest_path.exists()


def test_compliance_manifest_created(client, tmp_path, monkeypatch):
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("w2 run")

    async def quick_run(self):
        self.metrics.iterations += 1
        self.last_response_output = "All requirements satisfied."

    monkeypatch.setattr(actions_server.RalphOrchestrator, "arun", quick_run)

    resp = client.post(
        "/runs",
        json={
            "prompt_file": str(prompt_file),
            "classification": "w2_employee",
            "pay": 120000,
            "pay_type": "salary",
        },
    )
    run_id = resp.json()["run_id"]

    status = None
    for _ in range(10):
        status_resp = client.get(f"/runs/{run_id}")
        status = status_resp.json()
        if status["state"] in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.01)

    manifest_path = prompt_file.parent / "compliance_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["classification"] == "w2_employee"
    assert manifest["monetary_compensation_confirmed"] is True
    assert manifest["human_labor"] is True
    assert status is not None
    assert str(manifest_path) in status["artifacts"]
