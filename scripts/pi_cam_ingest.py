#!/usr/bin/env python3
"""Capture frames from the Raspberry Pi camera and POST to PiVision ingest."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

try:
    from picamera2 import Picamera2
except ImportError:
    raise SystemExit("Picamera2 is required. Install it via 'sudo apt install python3-picamera2'.")

try:
    import cv2
except ImportError:
    raise SystemExit("opencv-python is required; run 'pip install opencv-python'.")


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_seq(path: Path) -> int:
    if not path.exists():
        return 1
    try:
        return int(path.read_text().strip())
    except ValueError:
        return 1


def persist_seq(path: Path, seq: int) -> None:
    path.write_text(str(seq))


def encode_frame(frame, quality: int) -> str:
    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not success:
        raise RuntimeError("failed to encode JPEG")
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


def send_frame(session: requests.Session, base_url: str, device_key: str, payload: dict) -> dict:
    headers = {"X-DEVICE-KEY": device_key, "Content-Type": "application/json"}
    resp = session.post(f"{base_url}/ingest/frame", headers=headers, json=payload, timeout=5)
    resp.raise_for_status()
    return resp.json()


def configure_camera(width: int, height: int) -> Picamera2:
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"format": "RGB888", "size": (width, height)})
    picam2.configure(config)
    picam2.start()
    return picam2


def main() -> None:
    parser = argparse.ArgumentParser(description="Pi camera ingest client for PiVision.")
    parser.add_argument("--device-id", default="pi-camera", help="Device identifier included in the payload.")
    parser.add_argument("--api-base", default="http://localhost:8080/api/v1", help="Base URL for the ingest API.")
    parser.add_argument("--device-key", default="dev-key", help="X-DEVICE-KEY header value.")
    parser.add_argument("--capture-interval", type=float, default=1.0, help="Seconds between captures.")
    parser.add_argument("--jpeg-quality", type=int, default=70, help="JPEG encoding quality.")
    parser.add_argument("--seq-file", type=Path, default=Path("backend/data/pi-camera.seq"), help="File that tracks the seq counter.")
    parser.add_argument("--resolution", type=str, default="640x480", help="Frame resolution WxH.")
    parser.add_argument("--max-frames", type=int, default=0, help="Stop after N captures (0 = run forever).")
    args = parser.parse_args()

    seq_path = args.seq_file
    seq_path.parent.mkdir(parents=True, exist_ok=True)
    seq = load_seq(seq_path)

    width, height = map(int, args.resolution.split("x"))
    picam2 = configure_camera(width, height)
    session = requests.Session()

    print(f"Starting PiVision camera client device={args.device_id} interval={args.capture_interval}s")
    try:
        frame_count = 0
        while True:
            array = picam2.capture_array()
            image_b64 = encode_frame(array, max(10, min(100, args.jpeg_quality)))
            payload = build_payload(args.device_id, seq, width, height, args.jpeg_quality, image_b64)
            try:
                response = send_frame(session, args.api_base, args.device_key, payload)
                print(f"[ingest] seq={seq} frame_id={response.get('frame_id')} ok")
            except requests.RequestException as exc:
                print(f"[ingest] seq={seq} failed: {exc}", flush=True)
            seq += 1
            persist_seq(seq_path, seq)
            frame_count += 1
            if args.max_frames and frame_count >= args.max_frames:
                break
            time.sleep(args.capture_interval)
    finally:
        picam2.stop()


if __name__ == "__main__":
    main()
