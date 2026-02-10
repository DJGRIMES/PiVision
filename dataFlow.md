Yes — here’s a developer-facing technical writeup you can drop into `docs/activity-logging.md` (or fold into your main spec). It explicitly answers: **what logs exist, where they’re captured, where they’re stored, and retention rules**.

---

# Activity Logging & Analysis Outputs (Developer Spec)

## 1) Purpose

Define how the system records:

* **what the camera saw** (images)
* **what analysis produced** (scores/deltas/detections)
* **what the system decided happened** (activity events)
* **what the software did** (service/app logs)

MVP goals:

* Local-only on Raspberry Pi.
* Postgres is system-of-record for structured logs.
* Store **event frames only** for 7 days.
* Keep activity logs useful for tuning without hoarding raw frames.

Non-goals:

* No identity tracking.
* No continuous video storage.

---

## 2) Log types and where they live

### 2.1 Image assets (frames)

**Definition:** JPEGs captured by ESP camera(s) and uploaded to Pi.

**Capture point**

* Ingest API receives upload: `POST /api/v1/ingest/frame`

**Storage**

* Images are stored as files (filesystem or S3-compatible store later).
* Postgres stores pointers/URIs only.

**Classes**

1. **Staging frames** (ephemeral)

   * Written immediately on ingest.
   * Deleted after analysis unless promoted to event frames.

2. **Event frames** (retained)

   * Saved only when an event is emitted.
   * Typically: `pre`, `post`, optional `during`.

**Retention**

* Event frames retained **7 days** (rolling).
* Staging frames retained **< 24 hours** (should be deleted aggressively; ideally minutes).

**File layout (filesystem example)**

* `DATA_DIR/staging/<device_id>/<YYYY-MM-DD>/<capture_id>.jpg`
* `DATA_DIR/events/<device_id>/<YYYY-MM-DD>/<event_id>/{pre,post,during}.jpg`
* Optional thumbs:

  * `DATA_DIR/thumbs/<device_id>/<YYYY-MM-DD>/<event_id>/*.jpg`

---

### 2.2 Analysis outputs (low-level “why”)

**Definition:** Metrics generated during processing used for tuning/debugging:

* motion scores in interaction ROI
* delta scores in inventory ROI
* thresholds used
* baseline/stability status
* camera/scene health signals

**Capture point**

* Analysis Worker, per analyzed unit (single frame or pre/post pair).

**Storage**

* Stored in Postgres as JSONB:

  * Minimal per-capture fields in `captures.*`
  * Primary debug/tuning payload per event in `events.details`

**Design intent**

* Avoid storing detailed analysis outputs for *every* non-event frame.
* Preserve enough context for each event to explain “why it triggered.”

---

### 2.3 Activity events (high-level logs)

**Definition:** The canonical system record of real-world activity inferred from images.

**Examples**

* `interaction_detected`
* `stock_changed`
* `empty_confirmed`
* `camera_obstructed`
* `scene_shifted`

**Capture point**

* Rule engine / event aggregator (usually in Analysis Worker for MVP).

**Storage**

* Stored as rows in Postgres `events` table.
* Linked to saved images via `event_images`.

**Retention**

* Events can be retained indefinitely (tiny footprint), even if event frames are deleted after 7 days.

---

### 2.4 Application/service logs (software-level)

**Definition:** HTTP request logs, worker errors, retries, internal debug logs.

**Capture point**

* Ingest API and worker processes.

**Storage**

* MVP: systemd journal logs (`journalctl`) on Pi.
* No requirement to persist to Postgres.

**Retention**

* OS-managed; developer can configure journald limits.

---

## 3) Postgres schema responsibilities (source of truth)

### 3.1 `captures` (image capture log)

One row per received frame.

**Required fields**

* `capture_id` (UUID)
* `device_id`
* `received_at` (server time; ordering truth)
* `device_seq` (optional but strongly recommended)
* `storage_uri` (nullable until persisted)
* `storage_class`: `staging | event | debug | discarded`
* `analysis_status`: `queued | processed | failed | skipped`
* `analyzed_at`, `analysis_error` (nullable)

**Optional debug fields**

* `sha256_hex` (dedupe/integrity)
* `byte_size`, `width`, `height`
* `meta` JSONB (keep small)

**Guidance**

* Do **not** store full detection traces here for all frames.
* Prefer to store most tuning details under `events.details`.

---

### 3.2 `events` (activity log)

One row per activity event.

**Required fields**

* `event_id`
* `device_id`
* `event_type`
* `started_at` (+ `ended_at` nullable)
* `confidence` (0..1, optional for MVP)
* `details` JSONB (primary tuning context)

**`details` JSONB recommended shape**

```json
{
  "roi_version": 3,
  "thresholds": {
    "interaction_threshold": 0.18,
    "inventory_change_threshold": 0.22
  },
  "scores": {
    "motion_peak": 0.31,
    "inventory_delta": 0.27
  },
  "captures": {
    "pre_capture_id": "uuid",
    "post_capture_id": "uuid",
    "during_capture_id": "uuid"
  },
  "baseline": {
    "age_seconds": 420,
    "stability_frames": 3
  },
  "decision_trace": [
    "interaction_start",
    "burst_requested",
    "post_stable_found",
    "delta_exceeded_threshold",
    "event_emitted"
  ]
}
```

**Notes**

* Keep `decision_trace` short; it’s for explainability.
* Store exact thresholds used (important for auditing tuning changes).

---

### 3.3 `event_images` (links events ↔ images)

Links an event to one or more images and their role.

**Required fields**

* `event_id`
* `role`: `pre | post | during | annotated | thumb`
* `capture_id` (preferred) OR `derived_storage_uri` for generated images

**Rules**

* Enforce `UNIQUE(event_id, role)` for MVP.
* `derived_storage_uri` used for:

  * blurred versions
  * annotated debug overlays
  * thumbnails

---

## 4) Retention & cleanup (must implement)

### 4.1 Image retention job

A scheduled job (nightly) must:

* delete event image folders older than **7 days**
* delete staging frames older than **24 hours** (ideally much sooner)
* update `captures.storage_class` to `discarded` where appropriate (optional)
* optional: delete old thumbnails with event frames

### 4.2 Database retention

* Do not delete `events` by default (small; enables long-term analytics).
* Optionally add a policy later: keep events forever, but purge `captures` after N days if desired.

---

## 5) Where logging happens in the pipeline (implementation guidance)

### Ingest API (synchronous, fast)

* Authenticate request.
* Write staging image to disk.
* Insert `captures` row with `storage_class='staging'` and `analysis_status='queued'`.
* Enqueue analysis job.

### Analysis Worker (async)

* Load staging image + needed references.
* Compute interaction + delta logic.
* If **no event**:

  * mark capture processed/skipped
  * delete staging image
  * optionally set `storage_class='discarded'`
* If **event**:

  * create `events` row with `details`
  * persist event images (pre/post/during) and set `captures.storage_class='event'` for those captures
  * insert `event_images` rows linking roles → capture_id (and derived URIs if generated)
  * delete any leftover staging that isn’t referenced

### Admin UI

* Reads from Postgres:

  * timeline = `events` + `event_images`
  * tuning diagnostics = `events.details`
* Should not require access to staging frames.

---

## 6) Privacy posture (MVP)

* Do not store non-event frames.
* Prefer camera placement that minimizes faces.
* Optional feature: “blur outside inventory ROI” applied **only to stored event frames** (not required for MVP).

---

If you want, I can also add a short “**API contract**” section for the ingest endpoint (headers, required meta fields, error codes) so Codex builds the ESP + Pi handshake consistently.
