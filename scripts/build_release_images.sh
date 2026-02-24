#!/usr/bin/env bash
set -euo pipefail

REGISTRY=${REGISTRY:-docker.io/pivision}
TAG=${TAG:-latest}
PLATFORMS=${PLATFORMS:-linux/amd64,linux/arm/v7}
PUSH=${PUSH:-true}
DOCKERFILE=${DOCKERFILE:-Dockerfile}

SERVICES=(backend)

if ! docker buildx inspect --bootstrap >/dev/null 2>&1; then
  echo "[build] creating buildx builder"
  docker buildx create --use --name pivision-builder >/dev/null
fi

echo "[build] building services on ${PLATFORMS}"
for service in "${SERVICES[@]}"; do
  image="${REGISTRY}/${service}:${TAG}"
  echo "[build] ${service} -> ${image}"
  cmd=(docker buildx build --file "${DOCKERFILE}" --platform "${PLATFORMS}" -t "${image}" .)
  if [[ "${PUSH}" == "true" ]]; then
    cmd+=(--push)
  else
    cmd+=(--load)
  fi
  "${cmd[@]}"
  if [[ "${PUSH}" == "true" ]]; then
    echo "[build] pushed ${image}"
  else
    echo "[build] built ${image} (not pushed)"
  fi
done
