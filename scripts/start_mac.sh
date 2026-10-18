#!/usr/bin/env bash
# Build (if needed) and run FinAlly in Docker. Idempotent. Usage: start_mac.sh [--build] [--open]
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=finally
NAME=finally
BUILD=0; OPEN=0
for a in "$@"; do
  case "$a" in --build) BUILD=1 ;; --open) OPEN=1 ;; esac
done

[ -f .env ] || { [ -f .env.example ] && cp .env.example .env && echo "Created .env from .env.example"; }

if [ "$BUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -t "$IMAGE" .
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 8000:8000 -v finally-data:/app/db --env-file .env "$IMAGE" >/dev/null

echo "FinAlly running at http://localhost:8000"
if [ "$OPEN" = 1 ]; then
  (open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null) || true
fi
