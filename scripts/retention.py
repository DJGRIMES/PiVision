#!/usr/bin/env python3
"""Nightly retention job that prunes staging frames and event folders."""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.server import DATA_DIR, STAGING_DIR


EVENTS_DIR = DATA_DIR / "events"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_STAGING_HOURS = 24


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prune_staging(max_age_hours: int) -> int:
    cutoff = _now() - timedelta(hours=max_age_hours)
    removed = 0
    if not STAGING_DIR.exists():
        return removed

    for path in STAGING_DIR.rglob("*"):
        if not path.is_file():
            continue
        if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
            path.unlink()
            removed += 1

    # clean empty directories left behind
    for path in sorted(STAGING_DIR.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


def prune_events(retention_days: int) -> int:
    cutoff_date = (_now() - timedelta(days=retention_days)).date()
    removed = 0
    if not EVENTS_DIR.exists():
        return removed

    for device_dir in EVENTS_DIR.iterdir():
        if not device_dir.is_dir():
            continue
        for date_dir in device_dir.iterdir():
            if not date_dir.is_dir():
                continue
            try:
                entry_date = datetime.fromisoformat(date_dir.name).date()
            except ValueError:
                continue

            if entry_date <= cutoff_date:
                shutil.rmtree(date_dir)
                removed += 1
    return removed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PiVision retention cleanup job.")
    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="How many days to keep event frames.",
    )
    parser.add_argument(
        "--staging-hours",
        type=int,
        default=DEFAULT_STAGING_HOURS,
        help="How many hours to keep staging frames.",
    )
    return parser.parse_args()


def main() -> None:
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    args = parse_args()

    staging_removed = prune_staging(args.staging_hours)
    event_removed = prune_events(args.retention_days)

    print(
        f"[retention] removed {staging_removed} staging files older than {args.staging_hours}h "
        f"and {event_removed} date folders older than {args.retention_days}d."
    )


if __name__ == "__main__":
    main()
