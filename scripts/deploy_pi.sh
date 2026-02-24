#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE=docker-compose.deploy.yml
PI_COMPOSE_FILE=docker-compose.pi.yml
ENV_FILE=${ENV_FILE:-.env.deploy}

# override defaults via environment variables
DATA_DIR=${DATA_DIR:-/mnt/pivision/data}
DASHBOARD_DIR=${DASHBOARD_DIR:-/mnt/pivision/dashboard}
REGISTRY=${REGISTRY:-docker.io/pivision}
TAG=${TAG:-latest}
BACKEND_IMAGE=${BACKEND_IMAGE:-${REGISTRY}/backend:${TAG}}
WORKER_IMAGE=${WORKER_IMAGE:-$BACKEND_IMAGE}
RETENTION_IMAGE=${RETENTION_IMAGE:-$BACKEND_IMAGE}
CAMERA_IMAGE=${CAMERA_IMAGE:-$BACKEND_IMAGE}
DASHBOARD_IMAGE=${DASHBOARD_IMAGE:-python:3.12-slim}
DEVICE_KEY=${DEVICE_KEY:-pi-device-key}

mkdir -p "$DATA_DIR" "$DASHBOARD_DIR"

echo "[deploy] writing env file ${ENV_FILE}"
cat <<ENV >"${ENV_FILE}"
PIVISION_DATA_DIR=${DATA_DIR}
PIVISION_BACKEND_IMAGE=${BACKEND_IMAGE}
PIVISION_WORKER_IMAGE=${WORKER_IMAGE}
PIVISION_RETENTION_IMAGE=${RETENTION_IMAGE}
PIVISION_CAMERA_IMAGE=${CAMERA_IMAGE}
PIVISION_DASHBOARD_IMAGE=${DASHBOARD_IMAGE}
PIVISION_DASHBOARD_DIR=${DASHBOARD_DIR}
PIVISION_DEVICE_KEY=${DEVICE_KEY}
ENV

echo "[deploy] pulling images"
docker compose -f "${COMPOSE_FILE}" -f "${PI_COMPOSE_FILE}" --env-file "${ENV_FILE}" pull

echo "[deploy] bringing services up"
docker compose -f "${COMPOSE_FILE}" -f "${PI_COMPOSE_FILE}" --env-file "${ENV_FILE}" up -d

echo "[deploy] status"
docker compose -f "${COMPOSE_FILE}" -f "${PI_COMPOSE_FILE}" --env-file "${ENV_FILE}" ps

echo "[deploy] run scripts/check_backend.sh if you want to validate health"
