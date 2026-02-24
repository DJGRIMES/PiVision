#!/usr/bin/env python3
"""Simple DB-backed worker loop for PiVision MVP."""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime

from backend.server import DB_PATH, connect_db, init_db, record_system_health


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

    conn.execute("UPDATE captures SET processing_status = 'processed' WHERE id = ?", (capture_id,))


def worker_loop() -> None:
    init_db()
    print(f"PiVision worker watching DB: {DB_PATH}")

    while True:
        job_metadata = None
        job_success = False
        job_error: str | None = None
        with connect_db() as conn:
            job = claim_job(conn)
            if not job:
                time.sleep(POLL_S)
                continue

            job_metadata = {"id": job["id"], "capture_id": job["capture_id"]}
            try:
                process_capture(conn, job["capture_id"])
                conn.execute("UPDATE jobs SET status = 'done', updated_ts = ? WHERE id = ?", (now_iso(), job["id"]))
                job_success = True
                print(f"processed job={job['id']} capture={job['capture_id']}")
            except Exception as exc:  # noqa: BLE001
                job_error = str(exc)
                conn.execute(
                    "UPDATE jobs SET status = 'failed', last_error = ?, updated_ts = ? WHERE id = ?",
                    (job_error, now_iso(), job["id"]),
                )
                print(f"failed job={job['id']}: {exc}")

        if job_metadata:
            record_system_health(
                "worker",
                job_success,
                details={
                    "job_id": job_metadata["id"],
                    "capture_id": job_metadata["capture_id"],
                },
                error=job_error,
            )


if __name__ == "__main__":
    worker_loop()
