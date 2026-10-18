#!/usr/bin/env bash
# Build (if needed) and run FinAlly in Docker. Idempotent.
# Usage: start_mac.sh [--build] [--open] [--mock]   (--mock forces LLM_MOCK=true)
set -euo pipefail
cd "$(dirname "$0")/.."

IMAGE=finally
NAME=finally
PORT=8000
BUILD=0; OPEN=0; MOCK=0
for a in "$@"; do
  case "$a" in --build) BUILD=1 ;; --open) OPEN=1 ;; --mock) MOCK=1 ;; esac
done

if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env && echo "Created .env from .env.example"
fi

if [ "$BUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  docker build -t "$IMAGE" .
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

args=(-d --name "$NAME" -p "127.0.0.1:$PORT:8000" -v finally-data:/app/db)
[ -f .env ] && args+=(--env-file .env)
# -e after --env-file overrides the file value
[ "$MOCK" = 1 ] && args+=(-e LLM_MOCK=true)
docker run "${args[@]}" "$IMAGE" >/dev/null

echo "Waiting for health..."
for _ in $(seq 1 60); do
  if curl -fs "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "FinAlly running at http://localhost:$PORT"
    if [ "$OPEN" = 1 ]; then
      (open "http://localhost:$PORT" 2>/dev/null || xdg-open "http://localhost:$PORT" 2>/dev/null) || true
    fi
    exit 0
  fi
  sleep 1
done
echo "FinAlly did not become healthy in 60s; recent logs:" >&2
docker logs --tail 30 "$NAME" >&2 || true
exit 1
