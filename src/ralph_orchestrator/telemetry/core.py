"""Telemetry capture and storage for GPT Actions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SAFE_META_KEYS = {
    "client",
    "version",
    "model_hint",
    "orchestrator_status",
    "final_status",
    "duration_ms",
    "error",
}


def utc_now_iso() -> str:
    """Return the current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _bool_env(var_name: str, default: bool) -> bool:
    """Parse boolean-ish environment variables."""
    raw = os.getenv(var_name)
    if raw is None:
        return default
    return raw.lower() not in {"0", "false", "off", "no"}


@dataclass
class TelemetryConfig:
    """Runtime configuration for telemetry."""

    enabled: bool = True
    db_path: Path = Path("./data/telemetry.db")
    retention_days: int = 30
    rollup_interval_minutes: int = 10
    rollup_horizon_days: int = 365
    telemetry_salt: str = ""
    admin_api_key: Optional[str] = None
    batch_size: int = 50

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        """Load configuration from environment variables."""
        return cls(
            enabled=_bool_env("TELEMETRY_ENABLED", True),
            db_path=Path(os.getenv("TELEMETRY_DB_PATH", "./data/telemetry.db")),
            retention_days=int(os.getenv("TELEMETRY_RETENTION_DAYS", "30")),
            rollup_interval_minutes=int(os.getenv("TELEMETRY_ROLLUP_INTERVAL_MINUTES", "10")),
            rollup_horizon_days=int(os.getenv("TELEMETRY_ROLLUP_HORIZON_DAYS", "365")),
            telemetry_salt=os.getenv("ADMIN_TELEMETRY_SALT", ""),
            admin_api_key=os.getenv("ADMIN_API_KEY"),
            batch_size=int(os.getenv("TELEMETRY_BATCH_SIZE", "50")),
        )


class TelemetryStore:
    """SQLite-backed persistence for telemetry events and rollups."""

    def __init__(self, config: TelemetryConfig):
        self.config = config
        self.db_path = self.config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY,
                    ts TEXT NOT NULL,
                    route TEXT,
                    method TEXT,
                    action_name TEXT,
                    run_id TEXT,
                    session_id TEXT,
                    user_hash TEXT,
                    status_code INTEGER,
                    ok INTEGER,
                    latency_ms INTEGER,
                    req_bytes INTEGER,
                    resp_bytes INTEGER,
                    error_type TEXT,
                    error_message TEXT,
                    meta_json TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS telemetry_rollups_daily (
                    day TEXT PRIMARY KEY,
                    requests INTEGER,
                    ok_requests INTEGER,
                    error_requests INTEGER,
                    unique_users INTEGER,
                    unique_sessions INTEGER,
                    p50_latency_ms INTEGER,
                    p95_latency_ms INTEGER,
                    actions_json TEXT
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON telemetry_events(ts)")
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_action_ts ON telemetry_events(action_name, ts)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session_ts ON telemetry_events(session_id, ts)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_user_ts ON telemetry_events(user_hash, ts)"
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_run_ts ON telemetry_events(run_id, ts)")
            conn.commit()

    def insert_events(self, events: List[Dict[str, Any]]) -> None:
        """Insert a batch of events."""
        if not events:
            return
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO telemetry_events (
                    ts, route, method, action_name, run_id, session_id, user_hash,
                    status_code, ok, latency_ms, req_bytes, resp_bytes,
                    error_type, error_message, meta_json
                ) VALUES (
                    :ts, :route, :method, :action_name, :run_id, :session_id, :user_hash,
                    :status_code, :ok, :latency_ms, :req_bytes, :resp_bytes,
                    :error_type, :error_message, :meta_json
                )
                """,
                events,
            )
            conn.commit()

    def cleanup_events(self, retention_days: Optional[int] = None) -> None:
        """Remove raw events older than the retention window."""
        keep_days = retention_days or self.config.retention_days
        cutoff = utc_now_iso_for_cutoff(days=keep_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM telemetry_events WHERE ts < ?", (cutoff,))
            conn.commit()

    def _latency_percentiles(self, latencies: List[int]) -> Tuple[int, int]:
        if not latencies:
            return 0, 0
        latencies = sorted(latencies)
        p50 = latencies[int(0.5 * (len(latencies) - 1))]
        p95 = latencies[int(0.95 * (len(latencies) - 1))]
        return p50, p95

    def compute_rollups(self, days: int = 7) -> None:
        """Compute daily rollups for the recent period."""
        today = date.today()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            for offset in range(days):
                day = today - timedelta(days=offset)
                start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).isoformat()
                end = (day + timedelta(days=1))
                end_ts = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc).isoformat()

                cursor.execute(
                    """
                    SELECT action_name, ok, latency_ms, session_id, user_hash
                    FROM telemetry_events
                    WHERE ts >= ? AND ts < ?
                    """,
                    (start, end_ts),
                )
                rows = cursor.fetchall()
                requests = len(rows)
                if requests == 0:
                    continue

                actions: Dict[str, List[int]] = {}
                action_counts: Dict[str, int] = {}
                ok_counts: Dict[str, int] = {}
                latencies: List[int] = []
                sessions = set()
                users = set()
                error_requests = 0

                for row in rows:
                    action = row["action_name"] or "unknown"
                    latency = row["latency_ms"] or 0
                    actions.setdefault(action, []).append(latency)
                    action_counts[action] = action_counts.get(action, 0) + 1
                    if row["ok"]:
                        ok_counts[action] = ok_counts.get(action, 0) + 1
                    else:
                        error_requests += 1
                    latencies.append(latency)
                    if row["session_id"]:
                        sessions.add(row["session_id"])
                    if row["user_hash"]:
                        users.add(row["user_hash"])

                p50, p95 = self._latency_percentiles(latencies)
                actions_summary = {
                    name: {
                        "count": count,
                        "ok": ok_counts.get(name, 0),
                        "p95_latency_ms": self._latency_percentiles(lats)[1],
                    }
                    for name, (count, lats) in (
                        (action, (action_counts[action], actions[action])) for action in actions
                    )
                }

                cursor.execute(
                    """
                    INSERT INTO telemetry_rollups_daily (
                        day, requests, ok_requests, error_requests, unique_users,
                        unique_sessions, p50_latency_ms, p95_latency_ms, actions_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(day) DO UPDATE SET
                        requests=excluded.requests,
                        ok_requests=excluded.ok_requests,
                        error_requests=excluded.error_requests,
                        unique_users=excluded.unique_users,
                        unique_sessions=excluded.unique_sessions,
                        p50_latency_ms=excluded.p50_latency_ms,
                        p95_latency_ms=excluded.p95_latency_ms,
                        actions_json=excluded.actions_json
                    """,
                    (
                        day.isoformat(),
                        requests,
                        sum(ok_counts.values()),
                        error_requests,
                        len(users),
                        len(sessions),
                        p50,
                        p95,
                        json.dumps(actions_summary),
                    ),
                )
            conn.commit()

    def _range_filter(self, start: Optional[str], end: Optional[str], default_days: int) -> Tuple[str, str]:
        """Compute ISO start/end bounds."""
        end_dt = datetime.fromisoformat(end) if end else datetime.now(timezone.utc)
        start_dt = datetime.fromisoformat(start) if start else end_dt - timedelta(days=default_days)
        return start_dt.isoformat(), end_dt.isoformat()

    def fetch_events(
        self,
        start: Optional[str],
        end: Optional[str],
        limit: int = 200,
        offset: int = 0,
        default_days: int = 7,
    ) -> List[Dict[str, Any]]:
        """Return raw events for admin views."""
        start_ts, end_ts = self._range_filter(start, end, default_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM telemetry_events
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts DESC
                LIMIT ? OFFSET ?
                """,
                (start_ts, end_ts, limit, offset),
            )
            rows = cursor.fetchall()
            return [self._normalize_row(row) for row in rows]

    def _normalize_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        record = dict(row)
        if record.get("meta_json"):
            try:
                record["meta"] = json.loads(record["meta_json"])
            except json.JSONDecodeError:
                record["meta"] = {}
        else:
            record["meta"] = {}
        record.pop("meta_json", None)
        return record

    def summary(self, start: Optional[str], end: Optional[str], default_days: int = 7) -> Dict[str, Any]:
        """Aggregate key metrics for the admin dashboard."""
        start_ts, end_ts = self._range_filter(start, end, default_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) as ok_count,
                    SUM(CASE WHEN ok = 0 THEN 1 ELSE 0 END) as error_count,
                    COUNT(DISTINCT session_id) as sessions,
                    COUNT(DISTINCT user_hash) as users
                FROM telemetry_events
                WHERE ts >= ? AND ts <= ?
                """,
                (start_ts, end_ts),
            )
            row = cursor.fetchone()
            total = row["total"] or 0
            ok_count = row["ok_count"] or 0
            error_count = row["error_count"] or 0
            cursor.execute(
                """
                SELECT latency_ms FROM telemetry_events
                WHERE ts >= ? AND ts <= ? AND latency_ms IS NOT NULL
                ORDER BY latency_ms
                """,
                (start_ts, end_ts),
            )
            latencies = [r[0] for r in cursor.fetchall()]
            p50, p95 = self._latency_percentiles(latencies)
            actions_per_session = self._actions_per_session(cursor, start_ts, end_ts)
            completion_rate = self._completion_rate(cursor, start_ts, end_ts)
            return {
                "requests": total,
                "ok_requests": ok_count,
                "error_rate": round((error_count / total) * 100, 2) if total else 0,
                "unique_sessions": row["sessions"] or 0,
                "unique_users": row["users"] or 0,
                "actions_per_session_avg": actions_per_session[0],
                "actions_per_session_median": actions_per_session[1],
                "p50_latency_ms": p50,
                "p95_latency_ms": p95,
                "completion_rate": completion_rate,
            }

    def _actions_per_session(
        self, cursor: sqlite3.Cursor, start: str, end: str
    ) -> Tuple[float, float]:
        cursor.execute(
            """
            SELECT session_id, COUNT(*) as c FROM telemetry_events
            WHERE ts >= ? AND ts <= ? AND session_id IS NOT NULL
            GROUP BY session_id
            """,
            (start, end),
        )
        counts = [row["c"] for row in cursor.fetchall()]
        if not counts:
            return 0.0, 0.0
        avg = sum(counts) / len(counts)
        counts.sort()
        median = counts[len(counts) // 2]
        if len(counts) % 2 == 0:
            median = (counts[len(counts) // 2 - 1] + counts[len(counts) // 2]) / 2
        return round(avg, 2), round(median, 2)

    def _completion_rate(self, cursor: sqlite3.Cursor, start: str, end: str) -> float:
        cursor.execute(
            """
            SELECT meta_json FROM telemetry_events
            WHERE ts >= ? AND ts <= ? AND action_name = 'run_complete'
            """,
            (start, end),
        )
        rows = cursor.fetchall()
        if not rows:
            return 0.0
        total = len(rows)
        success = 0
        for row in rows:
            try:
                meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
            except json.JSONDecodeError:
                meta = {}
            if meta.get("final_status") == "completed":
                success += 1
        return round((success / total) * 100, 2)

    def actions_breakdown(
        self, start: Optional[str], end: Optional[str], default_days: int = 7
    ) -> List[Dict[str, Any]]:
        start_ts, end_ts = self._range_filter(start, end, default_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT action_name, COUNT(*) as total,
                    SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) as ok_count,
                    AVG(latency_ms) as avg_latency
                FROM telemetry_events
                WHERE ts >= ? AND ts <= ?
                GROUP BY action_name
                ORDER BY total DESC
                """,
                (start_ts, end_ts),
            )
            rows = cursor.fetchall()
            actions = []
            for row in rows:
                actions.append(
                    {
                        "action_name": row["action_name"] or "unknown",
                        "total": row["total"] or 0,
                        "ok_count": row["ok_count"] or 0,
                        "avg_latency_ms": int(row["avg_latency"] or 0),
                    }
                )
            return actions

    def sessions(
        self, start: Optional[str], end: Optional[str], limit: int = 100, default_days: int = 7
    ) -> List[Dict[str, Any]]:
        start_ts, end_ts = self._range_filter(start, end, default_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT session_id,
                    COUNT(*) as total,
                    MIN(ts) as first_ts,
                    MAX(ts) as last_ts
                FROM telemetry_events
                WHERE ts >= ? AND ts <= ? AND session_id IS NOT NULL
                GROUP BY session_id
                ORDER BY last_ts DESC
                LIMIT ?
                """,
                (start_ts, end_ts, limit),
            )
            rows = cursor.fetchall()
            sessions = []
            for row in rows:
                duration_ms = 0
                try:
                    start_dt = datetime.fromisoformat(row["first_ts"])
                    end_dt = datetime.fromisoformat(row["last_ts"])
                    duration_ms = int((end_dt - start_dt).total_seconds() * 1000)
                except Exception:
                    duration_ms = 0
                sessions.append(
                    {
                        "session_id": row["session_id"],
                        "actions": row["total"] or 0,
                        "duration_ms": duration_ms,
                        "last_seen": row["last_ts"],
                    }
                )
            return sessions

    def runs(
        self, start: Optional[str], end: Optional[str], default_days: int = 7
    ) -> List[Dict[str, Any]]:
        start_ts, end_ts = self._range_filter(start, end, default_days)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT run_id,
                    SUM(CASE WHEN action_name = 'run_complete' THEN 1 ELSE 0 END) as completes,
                    SUM(CASE WHEN ok = 1 THEN 1 ELSE 0 END) as ok_events,
                    MIN(ts) as first_ts,
                    MAX(ts) as last_ts
                FROM telemetry_events
                WHERE ts >= ? AND ts <= ? AND run_id IS NOT NULL
                GROUP BY run_id
                ORDER BY last_ts DESC
                """,
                (start_ts, end_ts),
            )
            rows = cursor.fetchall()
            runs: List[Dict[str, Any]] = []
            for row in rows:
                runs.append(
                    {
                        "run_id": row["run_id"],
                        "events": row["ok_events"] or 0,
                        "completions": row["completes"] or 0,
                        "last_ts": row["last_ts"],
                    }
                )
            return runs


def utc_now_iso_for_cutoff(days: int) -> str:
    """Return ISO timestamp for a cutoff days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TelemetryService:
    """Coordinates telemetry ingestion and rollups."""

    def __init__(self, config: Optional[TelemetryConfig] = None):
        self.config = config or TelemetryConfig.from_env()
        self.enabled = self.config.enabled
        self.store = TelemetryStore(self.config) if self.enabled else None
        self.queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._rollup: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start background ingestion and rollup loops."""
        if not self.enabled or self.store is None:
            return
        if self._worker:
            return
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._rollup = threading.Thread(target=self._rollup_loop, daemon=True)
        self._rollup.start()
        logger.info("Telemetry service started at %s", self.config.db_path)

    def stop(self, timeout: float = 2.0) -> None:
        """Gracefully stop background workers."""
        if not self.enabled:
            return
        self._stop.set()
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        if self._worker:
            self._worker.join(timeout=timeout)
        if self._rollup:
            self._rollup.join(timeout=timeout)
        logger.info("Telemetry service stopped")

    def hash_user(self, raw_user: Optional[str]) -> Optional[str]:
        """Return a salted hash for an ephemeral user identifier."""
        if not raw_user:
            return None
        if not self.config.telemetry_salt:
            logger.debug("No ADMIN_TELEMETRY_SALT configured; skipping user hash")
            return None
        digest = hashlib.sha256()
        digest.update((self.config.telemetry_salt + raw_user).encode("utf-8"))
        return digest.hexdigest()

    def enqueue(self, event: Dict[str, Any]) -> None:
        """Queue an event for async persistence."""
        if not self.enabled or self.store is None:
            return
        try:
            self.queue.put_nowait(event)
        except queue.Full:
            logger.warning("Telemetry queue full; dropping event for %s", event.get("route"))

    def log_event(
        self,
        *,
        route: Optional[str],
        method: Optional[str],
        action_name: Optional[str],
        status_code: int,
        ok: bool,
        latency_ms: int,
        run_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_hash: Optional[str] = None,
        req_bytes: Optional[int] = None,
        resp_bytes: Optional[int] = None,
        error_type: Optional[str] = None,
        error_message: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Build and queue a telemetry event."""
        cleaned_meta = None
        if meta:
            cleaned_meta = {key: value for key, value in meta.items() if key in SAFE_META_KEYS}
        event = {
            "ts": utc_now_iso(),
            "route": route,
            "method": method,
            "action_name": action_name,
            "run_id": run_id,
            "session_id": session_id,
            "user_hash": user_hash,
            "status_code": status_code,
            "ok": 1 if ok else 0,
            "latency_ms": latency_ms,
            "req_bytes": req_bytes,
            "resp_bytes": resp_bytes,
            "error_type": error_type,
            "error_message": (error_message or "")[:200] if error_message else None,
            "meta_json": json.dumps(cleaned_meta) if cleaned_meta else None,
        }
        self.enqueue(event)

    def _worker_loop(self) -> None:
        buffer: List[Dict[str, Any]] = []
        while not self._stop.is_set() or not self.queue.empty():
            try:
                item = self.queue.get(timeout=0.5)
            except queue.Empty:
                item = None
            if item is None:
                if buffer:
                    self._flush(buffer)
                    buffer = []
                continue
            buffer.append(item)
            if len(buffer) >= self.config.batch_size:
                self._flush(buffer)
                buffer = []
        if buffer:
            self._flush(buffer)

    def _flush(self, events: List[Dict[str, Any]]) -> None:
        if not events or not self.store:
            return
        try:
            self.store.insert_events(events)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to persist telemetry batch: %s", exc)

    def _rollup_loop(self) -> None:
        """Periodically compute rollups and enforce retention."""
        if not self.store:
            return
        while not self._stop.is_set():
            try:
                self.store.compute_rollups()
                self.store.cleanup_events()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Telemetry rollup failed: %s", exc)
            self._stop.wait(self.config.rollup_interval_minutes * 60)
