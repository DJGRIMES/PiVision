#!/usr/bin/env python3
"""Simple DB-backed worker loop for PiVision MVP."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime

from backend.server import DB_PATH, connect_db, init_db


POLL_S = 2


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def claim_job(conn: sqlite3.Connection):
    row = conn.execute(
        "SELECT id, capture_id, attempts FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        return None

    conn.execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_ts = ? WHERE id = ?",
        (now_iso(), row["id"]),
    )
    return row


def process_capture(conn: sqlite3.Connection, capture_id: int) -> None:
    capture = conn.execute("SELECT id, device_id, seq FROM captures WHERE id = ?", (capture_id,)).fetchone()
    if not capture:
        raise RuntimeError(f"missing capture: {capture_id}")

    # MVP placeholder heuristic: emit an interaction event every 3rd frame.
    if capture["seq"] % 3 == 0:
        conn.execute(
            """
            INSERT INTO events (capture_id, device_id, event_type, event_ts, confidence, note)
            VALUES (?, ?, 'interaction_detected', ?, ?, ?)
            """,
            (
                capture["id"],
                capture["device_id"],
                now_iso(),
                0.55,
                "MVP placeholder event emitted by worker stub.",
            ),
        )

    conn.execute("UPDATE captures SET processing_status = 'processed' WHERE id = ?", (capture_id,))


def worker_loop() -> None:
    init_db()
    print(f"PiVision worker watching DB: {DB_PATH}")

    while True:
        with connect_db() as conn:
            job = claim_job(conn)
            if not job:
                time.sleep(POLL_S)
                continue

            try:
                process_capture(conn, job["capture_id"])
                conn.execute("UPDATE jobs SET status = 'done', updated_ts = ? WHERE id = ?", (now_iso(), job["id"]))
                print(f"processed job={job['id']} capture={job['capture_id']}")
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    "UPDATE jobs SET status = 'failed', last_error = ?, updated_ts = ? WHERE id = ?",
                    (str(exc), now_iso(), job["id"]),
                )
                print(f"failed job={job['id']}: {exc}")


if __name__ == "__main__":
    worker_loop()
