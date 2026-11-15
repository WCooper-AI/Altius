#!/usr/bin/env bash
# Builds the self-hosted Docker image, starts it on an empty workspace and checks the web interface
# answers: the session endpoint, the page, and a JavaScript module served as JavaScript.
# Run from the repo root (the folder that holds engine/ and selfhost/). Used by CI; safe to run locally.
set -euo pipefail

IMAGE=tradealerts:smoke
NAME=tradealerts-smoke
PORT=${SMOKE_PORT:-18321}
BASE="http://127.0.0.1:$PORT"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker build -q -f selfhost/Dockerfile -t "$IMAGE" . >/dev/null
cleanup
docker run -d --name "$NAME" -p "127.0.0.1:$PORT:8321" -e TRADEALERTS_PASSWORD=smoke-test-only "$IMAGE" >/dev/null

fail() { echo "✕ $1"; echo "--- container log ---"; docker logs "$NAME" 2>&1 | tail -40; exit 1; }

for _ in $(seq 1 60); do
  curl -fsS "$BASE/api/session" >/dev/null 2>&1 && break
  [ "$(docker inspect -f '{{.State.Running}}' "$NAME")" = true ] || fail "container stopped"
  sleep 1
done
curl -fsS "$BASE/api/session" >/dev/null || fail "/api/session did not answer within 60 s"

page=$(curl -fsS "$BASE/") || fail "/ did not load"
grep -q 'type="module"' <<<"$page" || fail "page has no module script"

type=$(curl -fsS -o /dev/null -w '%{content_type}' "$BASE/static/boot.js") || fail "boot.js did not load"
[[ "$type" == *javascript* ]] || fail "boot.js served as '$type', not JavaScript"

code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/strategies")
[ "$code" = 401 ] || fail "/api/strategies answered $code without signing in (expected 401)"

echo "✓ Docker image starts and serves the interface"
