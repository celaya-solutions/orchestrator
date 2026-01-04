"""Server-rendered admin dashboard for telemetry."""

from __future__ import annotations

import csv
import html
import io
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..telemetry import TelemetryService, TelemetryStore


def _require_admin_key(request: Request, admin_key: Optional[str]) -> None:
    provided = request.headers.get("x-admin-key") or request.headers.get("x-api-key")
    if not admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ADMIN_API_KEY is not configured",
        )
    if provided != admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key",
        )


def _resolve_range(range_param: Optional[str], start: Optional[str], end: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a time window from shorthand range or explicit start/end."""
    if start or end:
        start_ts = start
        end_ts = end or datetime.now(timezone.utc).isoformat()
        return start_ts, end_ts

    now = datetime.now(timezone.utc)
    if range_param == "24h":
        start_ts = (now - timedelta(hours=24)).isoformat()
    elif range_param == "30d":
        start_ts = (now - timedelta(days=30)).isoformat()
    else:
        start_ts = (now - timedelta(days=7)).isoformat()
    return start_ts, now.isoformat()


def _html_page(title: str, body: str) -> str:
    return f"""
    <html>
      <head>
        <title>{html.escape(title)}</title>
        <style>
          body {{ font-family: Arial, sans-serif; margin: 2rem; background: #f7f7f9; color: #111; }}
          h1, h2, h3 {{ margin-bottom: 0.2rem; }}
          .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin: 1rem 0; }}
          .card {{ background: #fff; border-radius: 10px; padding: 1rem; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; }}
          th, td {{ padding: 0.5rem; border-bottom: 1px solid #e0e0e0; text-align: left; }}
          th {{ background: #f0f4ff; }}
          a {{ color: #0b63ce; text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}
          .muted {{ color: #666; font-size: 0.9rem; }}
        </style>
      </head>
      <body>
        {body}
      </body>
    </html>
    """


def _render_table(headers: List[str], rows: List[List[Any]]) -> str:
    head_html = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body_html = ""
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        body_html += f"<tr>{cells}</tr>"
    return f"<table><thead><tr>{head_html}</tr></thead><tbody>{body_html}</tbody></table>"


def _kpi_card(label: str, value: Any) -> str:
    return f"<div class='card'><div class='muted'>{html.escape(label)}</div><div style='font-size:1.4rem;font-weight:700;'>{html.escape(str(value))}</div></div>"


def _require_telemetry(telemetry: TelemetryService) -> TelemetryStore:
    if not telemetry.enabled or telemetry.store is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telemetry is disabled")
    return telemetry.store


def build_admin_router(telemetry: TelemetryService, admin_key: Optional[str]) -> APIRouter:
    def admin_guard(request: Request) -> None:
        _require_admin_key(request, admin_key)

    router = APIRouter(dependencies=[Depends(admin_guard)])

    @router.get("/admin", response_class=HTMLResponse)
    async def admin_home(request: Request, range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        """Top-level admin dashboard."""
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        summary = store.summary(start_ts, end_ts)
        kpis = "".join(
            [
                _kpi_card("Requests", summary["requests"]),
                _kpi_card("Unique users", summary["unique_users"]),
                _kpi_card("Unique sessions", summary["unique_sessions"]),
                _kpi_card("Error rate (%)", summary["error_rate"]),
                _kpi_card("Completion rate (%)", summary["completion_rate"]),
                _kpi_card("Median actions/session", summary["actions_per_session_median"]),
                _kpi_card("p50 latency (ms)", summary["p50_latency_ms"]),
                _kpi_card("p95 latency (ms)", summary["p95_latency_ms"]),
            ]
        )
        body = f"""
        <h1>Telemetry Admin</h1>
        <div class='muted'>Range: {html.escape(range or '7d')}</div>
        <div class='kpi-grid'>{kpis}</div>
        <div class='card'>
          <h3>Navigation</h3>
          <a href="/admin/actions">Actions</a> | 
          <a href="/admin/sessions">Sessions</a> | 
          <a href="/admin/runs">Runs</a> | 
          <a href="/admin/events">Events</a>
        </div>
        """
        return HTMLResponse(content=_html_page("Telemetry Admin", body))

    @router.get("/admin/actions", response_class=HTMLResponse)
    async def admin_actions(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        actions = store.actions_breakdown(start_ts, end_ts)
        rows = [[a["action_name"], a["total"], a["ok_count"], a["avg_latency_ms"]] for a in actions]
        table = _render_table(["Action", "Total", "OK", "Avg latency (ms)"], rows)
        return HTMLResponse(content=_html_page("Actions", f"<h2>Actions</h2>{table}"))

    @router.get("/admin/sessions", response_class=HTMLResponse)
    async def admin_sessions(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        sessions = store.sessions(start_ts, end_ts, limit=200)
        rows = [
            [s["session_id"], s["actions"], f"{s['duration_ms']} ms", s["last_seen"]]
            for s in sessions
        ]
        table = _render_table(["Session", "Actions", "Duration", "Last seen"], rows)
        return HTMLResponse(content=_html_page("Sessions", f"<h2>Sessions</h2>{table}"))

    @router.get("/admin/runs", response_class=HTMLResponse)
    async def admin_runs(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        runs = store.runs(start_ts, end_ts)
        rows = [[r["run_id"], r["events"], r["completions"], r["last_ts"]] for r in runs]
        table = _render_table(["Run ID", "Events", "Completions", "Last seen"], rows)
        return HTMLResponse(content=_html_page("Runs", f"<h2>Runs</h2>{table}"))

    @router.get("/admin/events", response_class=HTMLResponse)
    async def admin_events(
        range: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        events = store.fetch_events(start_ts, end_ts, limit=limit, offset=offset)
        rows = [
            [
                e.get("ts"),
                e.get("action_name"),
                e.get("route"),
                e.get("status_code"),
                e.get("latency_ms"),
                json.dumps(e.get("meta", {}))[:80],
            ]
            for e in events
        ]
        table = _render_table(["TS", "Action", "Route", "Status", "Latency", "Meta"], rows)
        return HTMLResponse(content=_html_page("Events", f"<h2>Events</h2>{table}"))

    @router.get("/admin/api/summary")
    async def admin_summary(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        data = store.summary(start_ts, end_ts)
        return JSONResponse(content=data)

    @router.get("/admin/api/actions")
    async def admin_actions_api(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        return JSONResponse(content=store.actions_breakdown(start_ts, end_ts))

    @router.get("/admin/api/sessions")
    async def admin_sessions_api(
        range: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 200,
    ):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        return JSONResponse(content=store.sessions(start_ts, end_ts, limit=limit))

    def _stream_ndjson(items: List[Dict[str, Any]]):
        for item in items:
            yield json.dumps(item) + "\n"

    @router.get("/admin/export/events.ndjson")
    async def export_events_ndjson(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        events = store.fetch_events(start_ts, end_ts, limit=10000)
        return StreamingResponse(_stream_ndjson(events), media_type="application/x-ndjson")

    @router.get("/admin/export/events.csv")
    async def export_events_csv(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        events = store.fetch_events(start_ts, end_ts, limit=10000)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(
            [
                "ts",
                "route",
                "method",
                "action_name",
                "run_id",
                "session_id",
                "user_hash",
                "status_code",
                "ok",
                "latency_ms",
                "req_bytes",
                "resp_bytes",
                "error_type",
                "error_message",
                "meta",
            ]
        )
        for e in events:
            writer.writerow(
                [
                    e.get("ts"),
                    e.get("route"),
                    e.get("method"),
                    e.get("action_name"),
                    e.get("run_id"),
                    e.get("session_id"),
                    e.get("user_hash"),
                    e.get("status_code"),
                    e.get("ok"),
                    e.get("latency_ms"),
                    e.get("req_bytes"),
                    e.get("resp_bytes"),
                    e.get("error_type"),
                    e.get("error_message"),
                    json.dumps(e.get("meta", {})),
                ]
            )
        buffer.seek(0)
        return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")

    @router.get("/admin/export/actions.csv")
    async def export_actions_csv(range: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None):
        store = _require_telemetry(telemetry)
        start_ts, end_ts = _resolve_range(range, start, end)
        actions = store.actions_breakdown(start_ts, end_ts)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["action_name", "total", "ok_count", "avg_latency_ms"])
        for row in actions:
            writer.writerow([row["action_name"], row["total"], row["ok_count"], row["avg_latency_ms"]])
        buffer.seek(0)
        return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv")

    return router
