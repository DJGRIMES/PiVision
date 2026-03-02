#!/usr/bin/env bash
set -euo pipefail

API_BASE=${API_BASE:-http://localhost:8080/api/v1}
DEVICE_ID=${DEVICE_ID:-smoke-client}
DEVICE_KEY=${DEVICE_KEY:-${PIVISION_DEVICE_KEY:-dev-key}}
API_ROOT=${API_ROOT:-$API_BASE}
if [[ "$API_ROOT" == */api/v1 ]]; then
  API_ROOT=${API_ROOT%/api/v1}
fi

echo "[check] hitting admin metrics/system"
resp="$(curl -sSf "${API_BASE}/admin/metrics/system")"
CHECK_RESP="$resp" python3 - <<'PY'
import json, os
data = json.loads(os.environ["CHECK_RESP"])
if not data.get("ok"):
    raise SystemExit("system metrics endpoint returned false ok")
print("[check] system metrics ok")
PY

echo "[check] hitting admin metrics/ingest"
resp="$(curl -sSf "${API_BASE}/admin/metrics/ingest")"
CHECK_RESP="$resp" python3 - <<'PY'
import json, os
data = json.loads(os.environ["CHECK_RESP"])
if not data.get("ok"):
    raise SystemExit("ingest metrics endpoint returned false ok")
print("[check] ingest metrics ok")
PY

echo "[check] hitting /health"
resp="$(curl -sSf "${API_ROOT}/health")"
CHECK_RESP="$resp" python3 - <<'PY'
import json, os
data = json.loads(os.environ["CHECK_RESP"])
health_ok = data.get("ok") is True
if not health_ok:
    raise SystemExit("health endpoint reported !ok")
db_info = data.get("db", {})
if not db_info.get("connected"):
    raise SystemExit("health endpoint cannot reach database")
for entry in data.get("directories", []):
    if not entry.get("exists"):
        raise SystemExit(f"missing directory: {entry.get('path')}")
    if not entry.get("writable"):
        raise SystemExit(f"directory not writable: {entry.get('path')}")
print("[check] health ok")
PY

echo "[check] ingesting a tiny frame to /ingest/frame"
IMAGE_B64=$(python3 - <<'PY'
import base64
from io import BytesIO
from PIL import Image

img = Image.new("RGB", (2, 2), (255, 0, 0))
buf = BytesIO()
img.save(buf, format="JPEG")
print(base64.b64encode(buf.getvalue()).decode("ascii"))
PY
)

PAYLOAD=$(cat <<EOF
{
"device_id": "${DEVICE_ID}",
"capture_ts": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
"seq": 1,
"width": 2,
"height": 2,
"jpeg_quality": 50,
"image_b64": "${IMAGE_B64}"
}
EOF
)

curl -sSf -X POST "${API_BASE}/ingest/frame" \
  -H "Content-Type: application/json" \
  -H "X-DEVICE-KEY: ${DEVICE_KEY}" \
  -d "${PAYLOAD}" \
  >/dev/null

echo "[check] ingest accepted"
