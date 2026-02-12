# PiVision Ops Dashboard Prototype

This is a lightweight local dashboard prototype intended to become the operational cockpit for PiVision while building, troubleshooting, and deploying.

## Views (tabbed)
- **System**: Pi runtime details (CPU, memory, disk, temperature, uptime), ingest throughput, queue health.
- **Database**: quick DB health snapshot and table-level viewer.
- **Events**: timeline cards with image previews.
- **Devices**: heartbeat/status table for all cameras.
- **Alerts**: suggested operational alert rules (recommended additional view).

## Live prototype controls
- **Refresh now**: manually re-renders and simulates a fresh metrics pull.
- **Auto-refresh**: runs every 5 seconds by default and can be toggled off.
- **Overall status pill**: automatically shifts between nominal/watchlist/needs-attention based on temperature, disk, ingest failures, and dead jobs.

## Run locally
Because this is static HTML/CSS/JS, you can open it directly:

```bash
xdg-open dashboard/index.html
```

Or serve it via a tiny HTTP server:

```bash
python3 -m http.server 4173
# then open http://localhost:4173/dashboard/
```

## Next integration step
Replace the mock `dashboardData` object in `app.js` with API reads from PiVision admin endpoints, such as:
- `GET /api/v1/admin/metrics/system`
- `GET /api/v1/admin/metrics/ingest`
- `GET /api/v1/admin/metrics/queue`
- `GET /api/v1/admin/metrics/database`
- `GET /api/v1/admin/devices`
- `GET /api/v1/admin/events?limit=20`

If polling, start with a 5s interval and exponential backoff on request failure.
