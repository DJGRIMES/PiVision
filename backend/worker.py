#!/usr/bin/env python3
"""Simple DB-backed worker loop for PiVision MVP."""

from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from server import DB_PATH, EVENTS_DIR, connect_db, init_db, record_system_health


POLL_S = 2


EVENT_TYPE_INTERACTION = "interaction_detected"
EVENT_IMAGE_FILENAME = "pre.jpg"


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


def promote_capture_to_event_image(capture: sqlite3.Row, event_id: int, event_ts: str) -> str | None:
    storage_uri = capture["storage_uri"]
    if not storage_uri:
        return None

    source = Path(storage_uri)
    if not source.exists():
        return None

    event_date = event_ts.split("T", 1)[0]
    dest_dir = EVENTS_DIR / capture["device_id"] / event_date / str(event_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / EVENT_IMAGE_FILENAME
    shutil.move(str(source), str(dest))
    return str(dest)


def process_capture(conn: sqlite3.Connection, capture_id: int) -> tuple[int, str | None]:
    capture = conn.execute(
        "SELECT id, device_id, seq, storage_uri FROM captures WHERE id = ?",
        (capture_id,),
    ).fetchone()
    if not capture:
        raise RuntimeError(f"missing capture: {capture_id}")

    event_ts = now_iso()
    note = f"auto {EVENT_TYPE_INTERACTION} seq={capture['seq']}"
    cursor = conn.execute(
        "INSERT INTO events (capture_id, device_id, event_type, event_ts, note) VALUES (?, ?, ?, ?, ?)",
        (capture_id, capture["device_id"], EVENT_TYPE_INTERACTION, event_ts, note),
    )
    conn.execute("UPDATE captures SET processing_status = 'processed' WHERE id = ?", (capture_id,))
    event_id = int(cursor.lastrowid)
    promoted_uri = promote_capture_to_event_image(capture, event_id, event_ts)
    if promoted_uri:
        conn.execute("UPDATE captures SET storage_uri = ? WHERE id = ?", (promoted_uri, capture_id))
    return event_id, promoted_uri


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

            job_metadata = {
                "id": job["id"],
                "capture_id": job["capture_id"],
                "event_id": None,
                "event_image_uri": None,
            }
            try:
                event_id, event_image_uri = process_capture(conn, job["capture_id"])
                job_metadata["event_id"] = event_id
                job_metadata["event_image_uri"] = event_image_uri
                conn.execute("UPDATE jobs SET status = 'done', updated_ts = ? WHERE id = ?", (now_iso(), job["id"]))
                job_success = True
                print(f"processed job={job['id']} capture={job['capture_id']} event={event_id}")
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
                    "event_id": job_metadata["event_id"],
                    "event_image_uri": job_metadata["event_image_uri"],
                },
                error=job_error,
            )


if __name__ == "__main__":
    worker_loop()
