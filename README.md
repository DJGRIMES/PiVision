# PiVision

AI Foodstand Pi Gateway Spec (MVP)
1. Goal

Build a Raspberry Pi–hosted system that:

Receives JPEG frames from an ESP32 camera (push model).

Detects interaction + inventory change using delta methods.

Stores event frames only for 7 days (rolling retention).

Provides a local admin panel to tune thresholds and behavior without reflashing the ESP.

Runs fully local (no cloud dependencies for MVP).

Is designed so Coral inference can be added later, but not required for MVP correctness.

2. Non-goals (MVP)

No identity tracking.

No face recognition.

No cloud dashboards, no remote access.

No “true video” storage/streaming. Frames only.

3. System overview
3.1 Data flow

ESP32 camera → (Wi-Fi) → Pi Ingest API → store frame (temporary staging) → analysis job queue → delta analysis → if event:

persist event record

persist event frame(s)

show in admin timeline

If no event: discard staging frame (or keep minimal diagnostics if enabled).

3.2 Capture policy (MVP)

Baseline capture: 2 frames/min (~every 30s).

Burst on interaction: configurable in Admin Panel:

Burst FPS (default 2 fps)

Burst duration (default 10–20s)

Cooldown between bursts (default 60s)

Note: Burst is triggered server-side by analysis (Pi), communicated to ESP via Pi-hosted config (device pulls config).

4. Hardware assumptions

Raspberry Pi (Pi 4 recommended).

Coral USB Accelerator available (optional for MVP; leave hooks).

Storage: strongly recommend USB SSD, but must work on SD (with warnings).

5. Pi services to implement
5.1 Ingest API service

Responsibilities

Accept authenticated uploads from ESP.

Validate request and metadata.

Write incoming JPEG to a staging area.

Create DB record for the received frame.

Enqueue analysis job.

Return success quickly.

Endpoints (local LAN)

POST /api/v1/ingest/frame

Auth: X-DEVICE-KEY header or HMAC signature (choose one; see Decisions)

Body: multipart/form-data with:

image: JPEG file

meta: JSON string (device metadata)

Required fields in meta:

device_id (string)

capture_ts (ISO8601 or epoch)

seq (int monotonically increasing)

width, height (ints)

jpeg_quality (int 1–100)

Optional fields:

battery_mv, rssi, fw_version, notes

Response:

{ ok: true, frame_id: <id>, received_ts: <iso> }

POST /api/v1/ingest/heartbeat (optional, can be same as frame cadence)

Receives health ping and stores last_seen.

GET /api/v1/device/config?device_id=...

ESP pulls config periodically.

Returns capture + burst settings currently defined in Admin Panel.

5.2 Analysis worker

Responsibilities

Consume jobs from queue.

Load staging frame + relevant prior reference(s).

Run delta-based analysis.

Decide whether an event occurred.

If event: persist event + save event frames.

If not event: delete staging frame.

5.3 Queue / job system

MVP: DB-backed job table (simple + reliable on Pi).

Job statuses: queued, running, done, failed, dead

Retry: up to N retries with backoff.

5.4 Database

MVP acceptable: SQLite (single-node local).
Preferred upgrade path: Postgres with same schema.

5.5 Admin Panel (local web UI)

Responsibilities

Configure detection thresholds and burst settings.

View event timeline with thumbnails.

View latest state (stock status, last interaction, last seen).

Device management (device key, last seen, config).

6. Core detection logic (delta MVP)
6.1 Regions of interest (ROI)

Admin Panel must allow configuring ROIs (rectangles) in image coordinates:

inventory_roi (main shelf/bin area)

interaction_roi (reach zone near opening)

optional ignore_rois (trees, sky, moving background)

ROIs stored per device (even though MVP is one camera).

UI requirement:

Show a recent frame

Let admin draw/adjust ROIs

Save ROI config

6.2 Interaction detection (tunable)

Interaction is a motion/occlusion event in the interaction ROI.

MVP algorithm outline (no ML required):

Compute frame-to-frame difference within interaction_roi.

Smooth / threshold to get “motion score”.

If motion score exceeds interaction_threshold for K frames → interaction starts.

Interaction ends after interaction_end_timeout seconds below threshold.

Expose in Admin Panel:

interaction_threshold

interaction_min_frames (K)

interaction_end_timeout

6.3 Inventory change detection (tunable, delta)

We care about change in inventory ROI around interactions.

Algorithm outline:

Maintain a rolling “baseline” reference frame for inventory ROI:

updated slowly when no interaction is happening (to adapt to lighting).

When interaction happens:

capture pre frame (closest stable frame before interaction)

capture post frame (first stable frame after interaction)

Compute delta metrics between pre and post within inventory_roi:

pixel diff score

structural similarity / histogram delta (pick 1–2 simple metrics)

If delta exceeds inventory_change_threshold → emit stock_changed event.

Expose in Admin Panel:

inventory_change_threshold

baseline_update_rate (how quickly baseline adapts)

stability_frames_required (what “stable” means)

6.4 Event types (MVP)

Persist events with confidence score (0–1):

interaction_detected

stock_changed

camera_obstructed (optional MVP)

scene_shifted (optional MVP; detect if camera moved)

MVP event emission rules:

Always emit interaction_detected when interaction starts.

Emit stock_changed only if inventory delta passes threshold after interaction.

6.5 Burst control

When interaction begins, system should request burst mode by updating device config state:

capture_interval_ms baseline (default 30000)

burst_fps (default 2)

burst_duration_ms (default 15000)

burst_cooldown_ms (default 60000)

Device pulls config:

On boot

Every config_poll_interval_ms (default 10s–30s)

7. Storage rules (event frames only)
7.1 Frame classes

Staging frames: short-lived, deleted after analysis unless used.

Event frames: retained for 7 days.

7.2 What to store per event

For each event, store:

pre.jpg (closest stable frame before interaction)

post.jpg (closest stable frame after interaction)

optional during.jpg (peak motion frame)

Store thumbnails for UI (optional but recommended).

7.3 Folder layout

All paths relative to configured DATA_DIR (default /data/foodstand):

staging/<device_id>/<date>/<frame_id>.jpg

events/<device_id>/<date>/<event_id>/{pre,post,during}.jpg

thumbs/<device_id>/<date>/<event_id>/*.jpg (optional)

db/foodstand.db

7.4 Retention

A scheduled cleanup job runs every night:

delete event folders older than 7 days

delete any leftover staging older than 24 hours

vacuum DB if SQLite (optional)

8. Face censoring (optional, low priority)

Add a feature flag:

censor_faces_enabled (default false)

If enabled:

apply a censor step only to stored event frames (never to incoming frames)

acceptable MVP censor: simple blur box over upper portion of frame or a detected face region if/when ML is available

ensure ROI-based censor option: “censor everything outside inventory ROI” (this is easiest + privacy-forward)

Admin controls:

toggle censor

choose method: blur_outside_inventory_roi (default) or face_detect_blur (future)

9. Coral integration (hooks, not required)

MVP must run without Coral.

Add an abstraction:

PresenceDetector interface with implementations:

NoopPresenceDetector (default)

CoralPersonDetector (future)

Potential future use:

improve interaction detection using “person present near stand”

reduce false positives from shadows/wind

Do not block MVP delivery on Coral.

10. Security (local-only)

Device authentication required even on LAN.

Store device keys hashed in DB.

Rate limit per device to avoid runaway spam.

Decisions
Pick one:

Simple: X-DEVICE-KEY shared secret per device.

Better: HMAC signature of payload using device secret.

MVP can ship with (1), but structure code so (2) can be added.

11. Admin Panel requirements
11.1 Pages

Dashboard

device online/offline (last seen)

last event time + type

storage usage + retention status

Timeline

list events (most recent first)

each event card shows thumbnails (pre/post)

filter by type/date

ROI Editor

show latest frame

draw/edit ROIs

save

Settings

interaction thresholds

inventory delta thresholds

baseline update behavior

burst behavior

retention days (default 7)

Device

device_id

rotate device key

show current config JSON served to device

11.2 Admin tuning expectations

All settings must be editable without restart, applied immediately to new frames/jobs.

12. Database schema (MVP)
12.1 Tables

devices

id (pk, string)

key_hash

created_at

last_seen_at

fw_version (optional)

config_json (current effective config)

roi_json

frames

id (pk)

device_id (fk)

capture_ts

received_ts

seq

width, height

staging_path

analyzed_at

analysis_status

jobs

id (pk)

type (analyze_frame)

payload_json (frame_id)

status

attempts

last_error

created_at, updated_at

events

id (pk)

device_id (fk)

type

started_at

ended_at (nullable)

confidence (0–1)

meta_json (thresholds used, scores)

pre_path, post_path, during_path (nullable)

created_at

13. Repo structure (recommended)

apps/ingest-api/ (HTTP server)

apps/admin-ui/ (served by ingest-api or separate)

workers/analyzer/ (job worker)

lib/ (shared logic: config, roi, delta metrics, storage paths)

migrations/ (db schema)

scripts/ (setup, retention cleanup, health checks)

docs/ (SPEC.md, deployment notes)

config/ (default config templates)

14. Deployment requirements (Pi)
14.1 Must support

Run on boot (systemd services)

Configurable DATA_DIR

Configurable listen port (default 8080)

Local LAN access only (bind to 0.0.0.0 is fine; user controls network)

14.2 Services (systemd)

foodstand-ingest.service

foodstand-worker.service

foodstand-retention.timer (nightly cleanup)

15. Acceptance criteria (definition of done)
Ingest

ESP can upload frames successfully with device auth.

Frames appear in DB as received.

Analysis jobs are created.

Analysis

With ROI configured, system detects interaction and emits events.

When interaction causes visible inventory change, stock_changed event created.

When no event, staging frame is removed.

Admin

Can tune thresholds and burst settings in UI.

Can define ROIs via UI.

Timeline shows events with thumbnails.

Retention

Only event frames are kept.

Event frames older than 7 days are deleted automatically.

16. Defaults (use these unless overridden)

Baseline capture interval: 30000 ms (2/min)

Burst fps: 2

Burst duration: 15000 ms

Burst cooldown: 60000 ms

Retention days: 7

Config poll interval: 15000 ms

interaction_min_frames: 2

interaction_end_timeout: 5s

17. Open decisions (confirm or assume)

Codex should assume defaults unless told otherwise:

Auth method: X-DEVICE-KEY (MVP)

DB: SQLite (MVP)

Admin auth: none (LAN-only MVP) or simple password gate

If you want, I can also produce the two “Codex seed files” that tend to w
