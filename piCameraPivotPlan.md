# Pi Camera Pivot Plan (Use Pi as Camera Until SBC + Dedicated Camera Arrive)

This plan keeps the **same ingest contract and schema** as the ESP path, while swapping the image producer to a Raspberry Pi camera process for testing.

## 1) Compatibility goal

Treat the Pi camera process as a normal device client that calls:
- `POST /api/v1/ingest/frame`
- `GET /api/v1/device/config?device_id=...`
- optional `POST /api/v1/ingest/heartbeat`

This preserves the existing `captures`, `events`, `event_images`, and queue/worker flow.

## 2) Minimal architecture for pivot mode

### New component: `pi-camera-client`
A lightweight process running on the Pi that:
1. Captures JPEG frames from `libcamera`/Picamera2.
2. Builds `meta` with the same fields expected from ESP (`device_id`, `capture_ts`, `seq`, `width`, `height`, `jpeg_quality`, optional `rssi/fw_version/notes`).
3. Uploads frames via multipart to `/api/v1/ingest/frame` using the same device auth header strategy.
4. Polls `/api/v1/device/config` on an interval and applies baseline/burst settings dynamically.
5. Sends heartbeat and local health stats.

### Keep existing backend responsibilities unchanged
- Ingest service still validates and stores staging frame + capture row.
- Worker still performs delta logic and event emission.
- Admin panel remains source of truth for thresholds and ROIs.

## 3) Build checklist to be Pi-ready end-to-end

## A. Device-side work (new for pivot)
- Implement camera capture loop with monotonic `seq` persistence across restarts.
- Implement config polling + runtime capture-mode switching (baseline vs burst).
- Implement retry strategy (network failure, 5xx, timeouts) with bounded queue on device.
- Add local spool directory for offline buffering with size limit.
- Add systemd service unit for auto-start and restart.

## B. Backend hardening needed before field-like testing
- Finish ingest endpoint contract enforcement (`meta` validation + auth check + idempotency rule around `device_id,seq`).
- Implement DB-backed analysis job queue lifecycle (`queued/running/done/failed/dead` + retries).
- Implement delta worker state machine (interaction start/end, pre/post frame selection, inventory delta decision).
- Implement retention jobs (staging cleanup + 7-day event-image deletion).
- Expose admin metrics endpoints for ingest success/failure, queue depth, and disk health.

## C. Admin/ops needs
- ROI editor must be functional against current frame for the Pi camera device.
- Device page should show last_seen, current capture mode, and last config fetch time.
- Add simple runbook: start/stop services, validate camera, inspect queue backlog, inspect disk.

## 4) Suggested implementation order

1. Build `pi-camera-client` that can post a single frame successfully.
2. Add continuous baseline capture every ~30s.
3. Wire config polling and burst mode behavior from backend config.
4. Add device spool + retry/offline recovery.
5. Complete worker + retention + dashboard metrics so timeline is trustworthy.

## 5) Definition of done for this pivot

You are ready to test as if ESP existed when:
- Pi camera client obeys the exact ingest/config contract.
- Captures appear with correct `device_id` + increasing `seq`.
- Worker emits `interaction_detected` events with linked images.
- Admin ROI + thresholds materially change event behavior.
- Retention job removes old event images and stale staging files.
- On reboot/network interruption, device recovers without manual DB cleanup.

## 6) What still remains before dedicated hardware swap

If the Pi client follows the same API contract as ESP, hardware swap later is mostly replacing the client implementation.
Remaining work after pivot is mostly:
- ESP-specific capture and network reliability tuning.
- ESP auth hardening (HMAC/replay window if required).
- Camera/ROI recalibration for new lens/FOV.
