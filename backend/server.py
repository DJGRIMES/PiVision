#!/usr/bin/env python3
"""PiVision MVP backend scaffold.

This is intentionally lightweight and uses only the Python standard library so it can
run directly on a Raspberry Pi without extra setup during early development.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import sqlite3
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STAGING_DIR = DATA_DIR / "staging"
DB_PATH = DATA_DIR / "pivision.db"
SCHEMA_PATH = ROOT / "schema.sql"
DEFAULT_DEVICE_KEY = os.getenv("PIVISION_DEVICE_KEY", "dev-key")

_INGEST_WINDOW_MINUTES = 60
_INGEST_BUCKET_COUNT = 12
_INGEST_BUCKET_MINUTES = 5



def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_uptime(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    days, remainder = divmod(total, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _read_uptime_seconds() -> float | None:
    uptime_path = Path("/proc/uptime")
    if not uptime_path.exists():
        return None
    try:
        with uptime_path.open() as fh:
            parts = fh.readline().split()
        return float(parts[0]) if parts else None
    except (ValueError, OSError):
        return None


def _read_memory_percent() -> float | None:
    meminfo_path = Path("/proc/meminfo")
    if not meminfo_path.exists():
        return None
    info: dict[str, int] = {}
    try:
        for line in meminfo_path.read_text().splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            info[key.strip()] = int(value.strip().split()[0])
    except ValueError:
        return None

    total = info.get("MemTotal")
    available = info.get("MemAvailable")
    if not total or not available:
        return None
    used = total - available
    return round((used / total) * 100, 1)


def _read_cpu_percent() -> float | None:
    if not hasattr(os, "getloadavg"):
        return None
    try:
        load = os.getloadavg()[0]
    except OSError:
        return None
    cpus = os.cpu_count() or 1
    return round(min(100.0, (load / cpus) * 100.0), 1)


def _read_temp_c() -> float | None:
    for zone in ("thermal_zone0", "thermal_zone1"):
        path = Path(f"/sys/class/thermal/{zone}/temp")
        if not path.exists():
            continue
        try:
            raw = int(path.read_text().strip())
        except ValueError:
            continue
        if raw > 1_000:
            return round(raw / 1_000, 1)
        return float(raw)
    return None


def _system_metrics() -> dict:
    disk_target = DATA_DIR if DATA_DIR.exists() else Path("/")
    try:
        disk_usage = shutil.disk_usage(disk_target)
        disk_remaining_gb = round(disk_usage.free / 1_073_741_824, 1)
    except OSError:
        disk_remaining_gb = 0.0

    return {
        "cpu": _read_cpu_percent(),
        "memory": _read_memory_percent(),
        "diskRemainingGb": disk_remaining_gb,
        "tempC": _read_temp_c(),
        "uptime": _format_uptime(_read_uptime_seconds()),
    }


def _collect_ingest_metrics(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT ok, latency_ms, request_ts FROM ingest_audit").fetchall()
    now = datetime.now(UTC)
    window_start = now - timedelta(minutes=_INGEST_WINDOW_MINUTES)
    bucket_start = now - timedelta(minutes=_INGEST_BUCKET_MINUTES * _INGEST_BUCKET_COUNT)
    series = [0] * _INGEST_BUCKET_COUNT

    success_total = failure_total = 0
    success_60m = failure_60m = 0
    latency_samples: list[int] = []
    for row in rows:
        ts = _parse_iso_ts(row["request_ts"])
        if not ts:
            continue

        if row["ok"]:
            success_total += 1
        else:
            failure_total += 1

        if ts >= window_start:
            if row["ok"]:
                success_60m += 1
            else:
                failure_60m += 1
            latency_samples.append(row["latency_ms"])

        if ts >= bucket_start:
            bucket_index = int((ts - bucket_start).total_seconds() // (_INGEST_BUCKET_MINUTES * 60))
            if 0 <= bucket_index < len(series):
                series[bucket_index] += 1

    avg_latency = round(sum(latency_samples) / len(latency_samples), 1) if latency_samples else 0
    return {
        "success_total": success_total,
        "failure_total": failure_total,
        "success_60m": success_60m,
        "failure_60m": failure_60m,
        "avg_latency_ms": avg_latency,
        "series": series,
    }


_TABLE_LAST_ACTIVITY_COLUMNS: dict[str, str] = {
    "captures": "MAX(received_ts)",
    "events": "MAX(event_ts)",
    "jobs": "MAX(updated_ts)",
    "devices": "MAX(last_seen)",
    "ingest_audit": "MAX(request_ts)",
}


def _table_last_activity(conn: sqlite3.Connection, table: str) -> str | None:
    column = _TABLE_LAST_ACTIVITY_COLUMNS.get(table)
    if not column:
        return None
    query = f"SELECT {column} as ts FROM {table}"
    row = conn.execute(query).fetchone()
    return row["ts"] if row and row["ts"] else None


def _collect_database_metrics(conn: sqlite3.Connection) -> dict:
    tables = ["captures", "events", "jobs", "devices", "ingest_audit"]
    counts: dict[str, int] = {}
    for table in tables:
        counts[table] = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()["cnt"]

    total_rows = sum(counts.values())
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    approx_per_row = (db_size / max(total_rows, 1)) if total_rows else 0

    table_details: list[dict[str, str | int]] = []
    for table in tables:
        last_write = _table_last_activity(conn, table)
        size_mb = round((approx_per_row * counts[table]) / 1_048_576, 2) if approx_per_row else 0
        table_details.append(
            {
                "name": table,
                "rows": counts[table],
                "lastWrite": last_write or "N/A",
                "size": f"{size_mb:.2f} MB",
            }
        )

    version = conn.execute("SELECT sqlite_version() as version").fetchone()["version"]
    return {
        "connected": True,
        "version": version,
        "dbSizeMb": round(db_size / 1_048_576, 2),
        "captures": counts["captures"],
        "events": counts["events"],
        "jobs": counts["jobs"],
        "devices": counts["devices"],
        "ingestAudit": counts["ingest_audit"],
        "tables": table_details,
    }

def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    with connect_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text())


def require_fields(payload: dict, fields: list[str]) -> tuple[bool, str]:
    for field in fields:
        if field not in payload:
            return False, f"missing required field: {field}"
    return True, ""


def parse_int_field(payload: dict, field: str) -> tuple[bool, int | None, str]:
    try:
        return True, int(payload[field]), ""
    except (TypeError, ValueError):
        return False, None, f"invalid integer field: {field}"


class PiVisionHandler(BaseHTTPRequestHandler):
    server_version = "PiVisionHTTP/0.1"

    def log_message(self, fmt: str, *args) -> None:
        # keep default behavior but with compact tag
        super().log_message(f"[pivision] {fmt}", *args)

    def _json(self, code: int, payload: dict) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def _record_ingest_audit(self, endpoint: str, ok: bool, latency_ms: int) -> None:
        with connect_db() as conn:
            conn.execute(
                "INSERT INTO ingest_audit (request_ts, endpoint, ok, latency_ms) VALUES (?, ?, ?, ?)",
                (now_iso(), endpoint, int(ok), latency_ms),
            )

    def _assert_device_key(self) -> tuple[bool, str]:
        key = self.headers.get("X-DEVICE-KEY", "")
        if key != DEFAULT_DEVICE_KEY:
            return False, "invalid device key"
        return True, ""


    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if parsed.path == "/api/v1/device/config":
            self._handle_device_config(parsed)
            return
        if parsed.path == "/api/v1/admin/events":
            self._handle_admin_events(parsed)
            return
        if parsed.path == "/api/v1/admin/devices":
            self._handle_admin_devices()
            return
        if parsed.path.startswith("/api/v1/admin/metrics/"):
            self._handle_admin_metrics(parsed.path)
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def _handle_ingest_frame(self, started: datetime) -> None:
        def fail(code: HTTPStatus, error: str) -> None:
            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            self._record_ingest_audit("/api/v1/ingest/frame", False, latency_ms)
            self._json(code, {"ok": False, "error": error})

        authed, msg = self._assert_device_key()
        if not authed:
            fail(HTTPStatus.UNAUTHORIZED, msg)
            return

        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            fail(HTTPStatus.BAD_REQUEST, "invalid json")
            return

        required = ["device_id", "capture_ts", "seq", "width", "height", "jpeg_quality"]
        valid, error = require_fields(payload, required)
        if not valid:
            fail(HTTPStatus.BAD_REQUEST, error)
            return

        device_id = payload["device_id"]
        capture_ts = payload["capture_ts"]
        ok, seq, error = parse_int_field(payload, "seq")
        if not ok:
            fail(HTTPStatus.BAD_REQUEST, error)
            return

        parsed_fields: dict[str, int] = {}
        for field in ["width", "height", "jpeg_quality"]:
            ok, value, error = parse_int_field(payload, field)
            if not ok:
                fail(HTTPStatus.BAD_REQUEST, error)
                return
            parsed_fields[field] = value

        image_b64 = payload.get("image_b64")
        image_path = None

        if image_b64:
            try:
                image_bytes = base64.b64decode(image_b64, validate=True)
            except (binascii.Error, ValueError):
                fail(HTTPStatus.BAD_REQUEST, "invalid image_b64")
                return
            image_name = f"{device_id}-{seq}.jpg"
            image_path = STAGING_DIR / image_name
            image_path.write_bytes(image_bytes)

        received_ts = now_iso()
        duplicate_seq = False
        capture_id = None
        with connect_db() as conn:
            conn.execute(
                """
                INSERT INTO devices (device_id, device_key, last_seen)
                VALUES (?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (device_id, DEFAULT_DEVICE_KEY, received_ts),
            )
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO captures (device_id, capture_ts, received_ts, seq, width, height, jpeg_quality, storage_uri)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        capture_ts,
                        received_ts,
                        seq,
                        parsed_fields["width"],
                        parsed_fields["height"],
                        parsed_fields["jpeg_quality"],
                        str(image_path) if image_path else None,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                duplicate_seq = True
            else:
                capture_id = int(cursor.lastrowid)
                conn.execute(
                    "INSERT INTO jobs (capture_id, status, created_ts, updated_ts) VALUES (?, 'queued', ?, ?)",
                    (capture_id, received_ts, received_ts),
                )

        if duplicate_seq:
            fail(HTTPStatus.CONFLICT, "duplicate device seq")
            return

        latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
        self._record_ingest_audit("/api/v1/ingest/frame", True, latency_ms)
        self._json(HTTPStatus.OK, {"ok": True, "frame_id": capture_id, "received_ts": received_ts})

    def _handle_heartbeat(self) -> None:
        authed, msg = self._assert_device_key()
        if not authed:
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": msg})
            return

        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid json"})
            return

        valid, error = require_fields(payload, ["device_id"])
        if not valid:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": error})
            return

        with connect_db() as conn:
            conn.execute(
                """
                INSERT INTO devices (device_id, device_key, last_seen, rssi, battery_mv, fw_version)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                  last_seen=excluded.last_seen,
                  rssi=excluded.rssi,
                  battery_mv=excluded.battery_mv,
                  fw_version=excluded.fw_version
                """,
                (
                    payload["device_id"],
                    DEFAULT_DEVICE_KEY,
                    now_iso(),
                    payload.get("rssi"),
                    payload.get("battery_mv"),
                    payload.get("fw_version"),
                ),
            )

        self._json(HTTPStatus.OK, {"ok": True, "last_seen": now_iso()})

    def _handle_device_config(self, parsed) -> None:
        params = parse_qs(parsed.query)
        device_id = params.get("device_id", [None])[0]
        if not device_id:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "device_id query param required"})
            return

        with connect_db() as conn:
            row = conn.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()

        config = {
            "capture_interval_s": row["capture_interval_s"] if row else 30,
            "burst_fps": row["burst_fps"] if row else 2,
            "burst_duration_s": row["burst_duration_s"] if row else 15,
            "burst_cooldown_s": row["burst_cooldown_s"] if row else 60,
            "interaction_threshold": row["interaction_threshold"] if row else 0.3,
            "interaction_min_frames": row["interaction_min_frames"] if row else 3,
            "interaction_end_timeout_s": row["interaction_end_timeout_s"] if row else 3,
        }
        self._json(HTTPStatus.OK, {"ok": True, "device_id": device_id, "config": config})

    def _handle_admin_events(self, parsed) -> None:
        params = parse_qs(parsed.query)
        try:
            limit = int(params.get("limit", [20])[0])
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "limit must be an integer"})
            return

        if limit < 1:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "limit must be positive"})
            return
        with connect_db() as conn:
            rows = conn.execute(
                """
                SELECT e.id, e.device_id, e.event_type, e.event_ts, e.note, c.storage_uri
                FROM events e
                JOIN captures c ON c.id = e.capture_id
                ORDER BY e.event_ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        events = [dict(row) for row in rows]
        self._json(HTTPStatus.OK, {"ok": True, "events": events})

    def _handle_admin_devices(self) -> None:
        with connect_db() as conn:
            rows = conn.execute(
                "SELECT device_id, last_seen, rssi, battery_mv, fw_version FROM devices ORDER BY device_id"
            ).fetchall()
        self._json(HTTPStatus.OK, {"ok": True, "devices": [dict(row) for row in rows]})

    def _handle_admin_metrics(self, path: str) -> None:
        metric_type = path.split("/")[-1]
        with connect_db() as conn:
            if metric_type == "ingest":
                ingest_data = _collect_ingest_metrics(conn)
                self._json(HTTPStatus.OK, {"ok": True, **ingest_data})
                return

            if metric_type == "queue":
                status_rows = conn.execute("SELECT status, COUNT(*) count FROM jobs GROUP BY status").fetchall()
                metrics = {row["status"]: row["count"] for row in status_rows}
                depth = sum(metrics.get(status, 0) for status in ("queued", "running", "failed", "dead"))
                self._json(HTTPStatus.OK, {"ok": True, "queue": metrics, "depth": depth})
                return

            if metric_type == "database":
                db_metrics = _collect_database_metrics(conn)
                self._json(HTTPStatus.OK, {"ok": True, **db_metrics})
                return

            if metric_type == "system":
                self._json(HTTPStatus.OK, {"ok": True, **_system_metrics()})
                return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown metrics group"})


def serve(port: int = 8080) -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), PiVisionHandler)
    print(f"PiVision backend listening on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    serve(int(os.getenv("PORT", "8080")))
