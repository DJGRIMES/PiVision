#!/usr/bin/env python3
"""Simple webcam-based client that posts frames to PiVision's ingest endpoint."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import requests


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_seq(path: Path) -> int:
    if not path.exists():
        return 1
    try:
        return int(path.read_text())
    except ValueError:
        return 1


def persist_seq(path: Path, seq: int) -> None:
    path.write_text(str(seq))


def get_config(session: requests.Session, base_url: str, device_id: str, poll_interval: int) -> dict[str, float]:
    try:
        resp = session.get(f"{base_url}/device/config", params={"device_id": device_id}, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("ok"):
            return payload["config"]
    except requests.RequestException as exc:
        print(f"[config] request failed: {exc}", flush=True)
    return {}


def encode_frame(frame, quality: int) -> str:
    _, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return base64.b64encode(buffer).decode("ascii")


def build_payload(device_id: str, seq: int, width: int, height: int, quality: int, image_b64: str) -> dict:
    return {
        "device_id": device_id,
        "capture_ts": iso_now(),
        "seq": seq,
        "width": width,
        "height": height,
        "jpeg_quality": quality,
        "image_b64": image_b64,
    }


def send_frame(session, base_url, device_key, payload):
    headers = {"X-DEVICE-KEY": device_key, "Content-Type": "application/json"}
    resp = session.post(f"{base_url}/ingest/frame", headers=headers, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Stream webcam frames to PiVision ingest API.")
    parser.add_argument("--device-id", default="laptop-cam", help="Device id sent in the ingest payload.")
    parser.add_argument("--camera", type=int, default=0, help="OpenCV camera index.")
    parser.add_argument("--api-base", default="http://localhost:8080/api/v1", help="Base URL for PiVision API.")
    parser.add_argument("--device-key", default="dev-key", help="X-DEVICE-KEY header value.")
    parser.add_argument("--jpeg-quality", type=int, default=70, help="JPEG quality for captured frames.")
    parser.add_argument("--config-poll-interval", type=int, default=15, help="Seconds between config polls.")
    parser.add_argument("--default-interval", type=float, default=30.0, help="Fallback capture interval (seconds).")
    parser.add_argument("--seq-file", type=Path, default=Path(".laptop-cam.seq"), help="File that keeps seq across restarts.")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N frames (0 = run forever).")
    args = parser.parse_args()

    seq_path = args.seq_file
    seq = load_seq(seq_path)
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print("failed to open camera", file=sys.stderr)
        raise SystemExit(1)

    session = requests.Session()
    last_config = timezone.utc
    next_config_at = time.time()
    config = {}

    try:
        while True:
            if time.time() >= next_config_at:
                config = get_config(session, args.api_base, args.device_id, args.config_poll_interval)
                next_config_at = time.time() + args.config_poll_interval

            interval = config.get("capture_interval_s", args.default_interval)
            ret, frame = cap.read()
            if not ret:
                print("camera read failed, retrying", flush=True)
                time.sleep(1)
                continue

            height, width = frame.shape[:2]
            image_b64 = encode_frame(frame, max(10, min(100, args.jpeg_quality)))
            payload = build_payload(args.device_id, seq, width, height, args.jpeg_quality, image_b64)

            try:
                response = send_frame(session, args.api_base, args.device_key, payload)
                print(f"[ingest] seq={seq} frame_id={response.get('frame_id')} status=ok", flush=True)
            except requests.RequestException as exc:
                print(f"[ingest] seq={seq} failed: {exc}", flush=True)

            seq += 1
            persist_seq(seq_path, seq)

            if args.max_frames and seq >= args.max_frames:
                break

            time.sleep(interval)
    finally:
        cap.release()


if __name__ == "__main__":
    main()
