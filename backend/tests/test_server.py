from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from pathlib import Path

from backend import server


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


if __name__ == "__main__":
    unittest.main()
