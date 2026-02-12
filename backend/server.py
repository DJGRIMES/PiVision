#!/usr/bin/env python3
"""PiVision MVP backend scaffold.

This is intentionally lightweight and uses only the Python standard library so it can
run directly on a Raspberry Pi without extra setup during early development.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from datetime import UTC, datetime
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

    def do_POST(self) -> None:  # noqa: N802
        started = datetime.now(UTC)
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/ingest/frame":
            self._handle_ingest_frame(started)
            return
        if parsed.path == "/api/v1/ingest/heartbeat":
            self._handle_heartbeat()
            return
        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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
        authed, msg = self._assert_device_key()
        if not authed:
            self._json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": msg})
            return

        try:
            payload = self._read_json()
        except json.JSONDecodeError:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid json"})
            return

        required = ["device_id", "capture_ts", "seq", "width", "height", "jpeg_quality"]
        valid, error = require_fields(payload, required)
        if not valid:
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": error})
            return

        device_id = payload["device_id"]
        capture_ts = payload["capture_ts"]
        seq = int(payload["seq"])
        image_b64 = payload.get("image_b64")
        image_path = None

        if image_b64:
            image_bytes = base64.b64decode(image_b64)
            image_name = f"{device_id}-{seq}.jpg"
            image_path = STAGING_DIR / image_name
            image_path.write_bytes(image_bytes)

        received_ts = now_iso()
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
                        int(payload["width"]),
                        int(payload["height"]),
                        int(payload["jpeg_quality"]),
                        str(image_path) if image_path else None,
                    ),
                )
            except sqlite3.IntegrityError:
                self._json(HTTPStatus.CONFLICT, {"ok": False, "error": "duplicate device seq"})
                return

            capture_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO jobs (capture_id, status, created_ts, updated_ts) VALUES (?, 'queued', ?, ?)",
                (capture_id, received_ts, received_ts),
            )

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
        limit = int(params.get("limit", [20])[0])
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
                success = conn.execute("SELECT COUNT(*) FROM ingest_audit WHERE ok = 1").fetchone()[0]
                fail = conn.execute("SELECT COUNT(*) FROM ingest_audit WHERE ok = 0").fetchone()[0]
                avg_latency = conn.execute("SELECT COALESCE(AVG(latency_ms), 0) FROM ingest_audit").fetchone()[0]
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "success_total": success,
                        "failure_total": fail,
                        "avg_latency_ms": round(avg_latency, 1),
                    },
                )
                return

            if metric_type == "queue":
                status_rows = conn.execute("SELECT status, COUNT(*) count FROM jobs GROUP BY status").fetchall()
                metrics = {row["status"]: row["count"] for row in status_rows}
                self._json(HTTPStatus.OK, {"ok": True, "queue": metrics})
                return

            if metric_type == "database":
                captures = conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0]
                events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
                self._json(HTTPStatus.OK, {"ok": True, "captures": captures, "events": events, "jobs": jobs})
                return

            if metric_type == "system":
                # Placeholder until we wire actual Pi host metrics.
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "status": "placeholder",
                        "disk_remaining_gb": None,
                        "temp_c": None,
                    },
                )
                return

        self._json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "unknown metrics group"})


def serve(port: int = 8080) -> None:
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), PiVisionHandler)
    print(f"PiVision backend listening on http://0.0.0.0:{port}")
    server.serve_forever()


if __name__ == "__main__":
    serve(int(os.getenv("PORT", "8080")))
