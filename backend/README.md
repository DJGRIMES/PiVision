# PiVision Backend Scaffold (MVP Start)

This is the first runnable backend slice so we can start building outside the static dashboard.

## Included pieces
- `server.py`: HTTP API scaffold with SQLite persistence.
- `worker.py`: DB-backed queue worker stub.
- `schema.sql`: initial schema for devices, captures, jobs, events, and ingest audit logs.

## Endpoints (first pass)
- `POST /api/v1/ingest/frame` (JSON + optional `image_b64`, requires `X-DEVICE-KEY`)
- `POST /api/v1/ingest/heartbeat`
- `GET /api/v1/device/config?device_id=...`
- `GET /api/v1/admin/events?limit=20`
- `GET /api/v1/admin/devices`
- `GET /api/v1/admin/metrics/{system|ingest|queue|database}`

## Run backend
```bash
python3 backend/server.py
```

## Run worker
```bash
python3 backend/worker.py
```

## Quick ingest test
```bash
curl -sS -X POST http://127.0.0.1:8080/api/v1/ingest/frame \
  -H 'Content-Type: application/json' \
  -H 'X-DEVICE-KEY: dev-key' \
  -d '{"device_id":"camera-01","capture_ts":"2026-02-12T00:00:00Z","seq":1,"width":640,"height":480,"jpeg_quality":12}'
```

## Notes
- This is deliberately simple and local-only for MVP iteration speed.
- `POST /ingest/frame` currently accepts JSON for simplicity; multipart support can be added next.
