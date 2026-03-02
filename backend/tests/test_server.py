from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from backend import server, worker


class ServerRobustnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._orig_data_dir = server.DATA_DIR
        cls._orig_staging_dir = server.STAGING_DIR
        cls._orig_db_path = server.DB_PATH

    @classmethod
    def tearDownClass(cls) -> None:
        server.DATA_DIR = cls._orig_data_dir
        server.STAGING_DIR = cls._orig_staging_dir
        server.DB_PATH = cls._orig_db_path

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_root = Path(self.temp_dir.name)
        server.DATA_DIR = data_root / "data"
        server.STAGING_DIR = server.DATA_DIR / "staging"
        server.DB_PATH = server.DATA_DIR / "pivision.db"
        server.init_db()

        self.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.PiVisionHandler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.05)

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=1)
        self.temp_dir.cleanup()

    def _post(self, path: str, payload: dict, key: str = "dev-key"):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        headers = {"Content-Type": "application/json", "X-DEVICE-KEY": key}
        conn.request("POST", path, body=json.dumps(payload), headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def _get(self, path: str):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=2)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def test_ingest_rejects_non_integer_seq(self) -> None:
        status, payload = self._post(
            "/api/v1/ingest/frame",
            {
                "device_id": "camera-01",
                "capture_ts": "2026-02-12T00:00:00Z",
                "seq": "not-an-int",
                "width": 640,
                "height": 480,
                "jpeg_quality": 12,
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid integer field: seq")

    def test_ingest_rejects_invalid_base64_image(self) -> None:
        status, payload = self._post(
            "/api/v1/ingest/frame",
            {
                "device_id": "camera-01",
                "capture_ts": "2026-02-12T00:00:00Z",
                "seq": 1,
                "width": 640,
                "height": 480,
                "jpeg_quality": 12,
                "image_b64": "***not-base64***",
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "invalid image_b64")

    def test_admin_events_rejects_invalid_limit(self) -> None:
        status, payload = self._get("/api/v1/admin/events?limit=abc")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "limit must be an integer")

    def test_ingest_duplicate_sequence_conflict(self) -> None:
        body = {
            "device_id": "camera-01",
            "capture_ts": "2026-02-12T00:00:00Z",
            "seq": 1,
            "width": 640,
            "height": 480,
            "jpeg_quality": 12,
        }
        first_status, _ = self._post("/api/v1/ingest/frame", body)
        second_status, second_payload = self._post("/api/v1/ingest/frame", body)

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 409)
        self.assertEqual(second_payload["error"], "duplicate device seq")

    def test_worker_process_capture_emits_event(self) -> None:
        device_id = "camera-01"
        seq = 42
        capture_ts = server.now_iso()
        received_ts = server.now_iso()
        with server.connect_db() as conn:
            conn.execute(
                "INSERT INTO devices (device_id, device_key, last_seen) VALUES (?, ?, ?)",
                (device_id, server.DEFAULT_DEVICE_KEY, server.now_iso()),
            )
            cursor = conn.execute(
                """
                INSERT INTO captures (device_id, capture_ts, received_ts, seq, width, height, jpeg_quality)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (device_id, capture_ts, received_ts, seq, 640, 480, 70),
            )
            capture_id = cursor.lastrowid

        with server.connect_db() as conn:
            event_id, event_image = worker.process_capture(conn, capture_id)

        with server.connect_db() as conn:
            event_row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            capture_row = conn.execute("SELECT processing_status FROM captures WHERE id = ?", (capture_id,)).fetchone()

        self.assertIsNotNone(event_row)
        self.assertEqual(event_row["device_id"], device_id)
        self.assertEqual(event_row["event_type"], "interaction_detected")
        self.assertEqual(capture_row["processing_status"], "processed")
        self.assertIsNone(event_image)


if __name__ == "__main__":
    unittest.main()
