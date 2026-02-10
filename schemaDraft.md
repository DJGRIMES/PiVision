-- Enable UUID generation (choose one extension; pgcrypto is common)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -------------------------------------------------------------------
-- 1) Devices
-- -------------------------------------------------------------------
CREATE TABLE devices (
  device_id           TEXT PRIMARY KEY,              -- e.g., "stand-ot-01"
  display_name        TEXT,
  is_enabled          BOOLEAN NOT NULL DEFAULT TRUE,

  -- Auth: store hash of device secret/key (never store raw key)
  device_key_hash     TEXT NOT NULL,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at        TIMESTAMPTZ,

  fw_version          TEXT,
  notes               TEXT
);

CREATE INDEX devices_last_seen_idx ON devices (last_seen_at DESC);


-- -------------------------------------------------------------------
-- 2) Capture log (every received frame gets a row)
-- -------------------------------------------------------------------
-- This is the canonical "image capture logs" table.
CREATE TABLE captures (
  capture_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  device_id           TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,

  -- Device-provided capture timestamp (can be wrong; keep anyway)
  captured_at         TIMESTAMPTZ,
  -- Server receive timestamp (source of truth for ordering)
  received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Monotonic sequence number from device, helps detect gaps/reboots
  device_seq          BIGINT,

  -- Image properties (from device or server)
  content_type        TEXT NOT NULL DEFAULT 'image/jpeg',
  width               INTEGER CHECK (width IS NULL OR width > 0),
  height              INTEGER CHECK (height IS NULL OR height > 0),
  byte_size           BIGINT CHECK (byte_size IS NULL OR byte_size >= 0),

  -- Storage pointer to where the JPEG lives (file://, s3://, http://, etc.)
  storage_uri         TEXT,                           -- may be NULL until persisted
  storage_class       TEXT NOT NULL DEFAULT 'staging', -- staging | event | debug | discarded

  -- Integrity / dedupe (optional but recommended)
  sha256_hex          CHAR(64),

  -- Capture settings (optional)
  jpeg_quality        INTEGER CHECK (jpeg_quality IS NULL OR (jpeg_quality BETWEEN 1 AND 100)),
  rssi_dbm            INTEGER,
  battery_mv          INTEGER,

  -- Pipeline status
  analysis_status     TEXT NOT NULL DEFAULT 'queued',  -- queued | processed | failed | skipped
  analyzed_at         TIMESTAMPTZ,
  analysis_error      TEXT,

  -- Flexible metadata (device extras, future fields)
  meta               JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Useful uniqueness constraint to prevent duplicates on retries:
-- (device_id, device_seq) is a good candidate if device_seq is reliable.
CREATE UNIQUE INDEX captures_device_seq_uniq
  ON captures(device_id, device_seq)
  WHERE device_seq IS NOT NULL;

-- Common query paths
CREATE INDEX captures_device_received_idx ON captures (device_id, received_at DESC);
CREATE INDEX captures_received_idx ON captures (received_at DESC);
CREATE INDEX captures_status_idx ON captures (analysis_status, received_at DESC);
CREATE INDEX captures_storage_class_idx ON captures (storage_class, received_at DESC);

-- Optional: If you’ll search by sha256
CREATE INDEX captures_sha256_idx ON captures (sha256_hex) WHERE sha256_hex IS NOT NULL;


-- -------------------------------------------------------------------
-- 3) Events (interaction / stock_changed / etc.)
-- -------------------------------------------------------------------
CREATE TABLE events (
  event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  device_id           TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,

  event_type          TEXT NOT NULL,                  -- interaction_detected | stock_changed | empty_confirmed | camera_issue
  person_count        INTEGER CHECK (person_count IS NULL OR person_count >= 0), -- MVP: count of detected interactions/persons
  confidence          REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0 AND 1)),

  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at            TIMESTAMPTZ,

  -- A short human-readable summary for UI (optional)
  summary             TEXT,
  -- Operator notes (e.g. "added_items", "took_items")
  operator_note       TEXT,

  -- Scores/thresholds used, ROI IDs, etc.
  details             JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX events_device_started_idx ON events (device_id, started_at DESC);
CREATE INDEX events_type_started_idx ON events (event_type, started_at DESC);


-- -------------------------------------------------------------------
-- 4) Event images (which captures/images belong to an event)
-- -------------------------------------------------------------------
-- This lets you attach pre/post/during frames to an event, and is compatible
-- whether you store images as separate objects or reuse captures.
CREATE TABLE event_images (
  event_image_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  event_id            UUID NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,

  -- Prefer referencing the captures row; gives you metadata + audit trail.
  capture_id          UUID REFERENCES captures(capture_id) ON DELETE SET NULL,

  -- Role within the event
  role                TEXT NOT NULL,                  -- pre | post | during | annotated | thumb

  -- If you generate derived images (annotated/blurred/thumb), point here.
  derived_storage_uri TEXT,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Extra per-image notes
  meta                JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Prevent duplicates like two "pre" images for one event unless you want that
  UNIQUE (event_id, role)
);

CREATE INDEX event_images_event_idx ON event_images (event_id);
CREATE INDEX event_images_capture_idx ON event_images (capture_id);


-- -------------------------------------------------------------------
-- Optional support tables (recommended)
-- -------------------------------------------------------------------

-- 5) ROI presets per device (for delta + interaction zones)
CREATE TABLE device_rois (
  roi_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id           TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,

  roi_name            TEXT NOT NULL,                  -- inventory_roi | interaction_roi | ignore_roi_1 etc.
  -- Coordinates as normalized floats 0..1 so it works across resolutions
  x                  REAL NOT NULL CHECK (x BETWEEN 0 AND 1),
  y                  REAL NOT NULL CHECK (y BETWEEN 0 AND 1),
  w                  REAL NOT NULL CHECK (w BETWEEN 0 AND 1),
  h                  REAL NOT NULL CHECK (h BETWEEN 0 AND 1),

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  meta                JSONB NOT NULL DEFAULT '{}'::jsonb,

  UNIQUE (device_id, roi_name)
);

CREATE INDEX device_rois_device_idx ON device_rois (device_id);


-- 6) Versioned device config (so you can tune from admin panel + audit changes)
CREATE TABLE device_configs (
  config_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  device_id           TEXT NOT NULL REFERENCES devices(device_id) ON DELETE CASCADE,

  -- Incrementing version number per device
  version             INTEGER NOT NULL,

  config              JSONB NOT NULL,                 -- capture_interval_ms, burst_fps, thresholds, etc.
  is_active           BOOLEAN NOT NULL DEFAULT FALSE,

  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by          TEXT,                           -- "admin" or username later

  UNIQUE (device_id, version)
);

-- Only one active config per device
CREATE UNIQUE INDEX device_configs_one_active
  ON device_configs(device_id)
  WHERE is_active = TRUE;

CREATE INDEX device_configs_device_idx ON device_configs (device_id, created_at DESC);
