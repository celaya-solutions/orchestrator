import sqlite3
import time
from pathlib import Path
import sys

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (ROOT, SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ralph_orchestrator.telemetry.core import TelemetryConfig, TelemetryService, TelemetryStore
from ralph_orchestrator.web import actions_server


def test_hash_user_is_salted(tmp_path, monkeypatch):
    db_path = tmp_path / "telemetry.db"
    config = TelemetryConfig(
        enabled=True,
        db_path=db_path,
        telemetry_salt="pepper",
    )
    service = TelemetryService(config)
    hashed = service.hash_user("user-123")
    assert hashed
    assert hashed == service.hash_user("user-123")
    assert hashed != service.hash_user("user-456")


def test_store_schema_initialized(tmp_path):
    db_path = tmp_path / "telemetry.db"
    config = TelemetryConfig(enabled=True, db_path=db_path)
    TelemetryStore(config)
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "telemetry_events" in tables
        assert "telemetry_rollups_daily" in tables


def test_middleware_captures_success_and_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(tmp_path / "events.db"))
    monkeypatch.setenv("ADMIN_TELEMETRY_SALT", "salt")
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    monkeypatch.setattr(actions_server, "ACTION_API_KEY", None)
    monkeypatch.setattr(actions_server, "ADMIN_API_KEY", "secret")

    app = actions_server.create_app()
    with TestClient(app) as client:
        client.get("/healthz")
        client.get("/missing")
        time.sleep(0.6)

    store = app.state.telemetry.store
    events = store.fetch_events(None, None, limit=20, default_days=1)
    assert any(event["route"] == "/healthz" for event in events)
    assert any(event["status_code"] == 404 for event in events)


def test_admin_auth_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_DB_PATH", str(tmp_path / "admin.db"))
    monkeypatch.setenv("ADMIN_TELEMETRY_SALT", "salt")
    monkeypatch.setenv("TELEMETRY_ENABLED", "true")
    monkeypatch.setenv("ADMIN_API_KEY", "secret")
    monkeypatch.setattr(actions_server, "ACTION_API_KEY", None)
    monkeypatch.setattr(actions_server, "ADMIN_API_KEY", "secret")

    app = actions_server.create_app()
    with TestClient(app) as client:
        client.get("/healthz")
        time.sleep(0.6)
        resp = client.get("/admin")
        assert resp.status_code == 401
        ndjson_resp = client.get("/admin/export/events.ndjson", headers={"x-admin-key": "secret"})
        assert ndjson_resp.status_code == 200
        assert "/healthz" in ndjson_resp.text
        csv_resp = client.get("/admin/export/actions.csv", headers={"x-admin-key": "secret"})
        assert csv_resp.status_code == 200
        assert "action_name" in csv_resp.text.splitlines()[0]
