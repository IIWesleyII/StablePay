#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
FRONTEND_DIRECTORY="${FRONTEND_DIRECTORY:-frontend}"
START_DATABASE="${START_DATABASE:-true}"

API_PID=""
FRONTEND_PID=""

log() {
  printf '[StablePay] %s\n' "$1"
}

fail() {
  printf '[StablePay] Error: %s\n' "$1" >&2
  exit 1
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM

  log "Stopping development servers..."
  if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [[ -n "$API_PID" ]] && kill -0 "$API_PID" 2>/dev/null; then
    kill "$API_PID" 2>/dev/null || true
  fi

  [[ -z "$FRONTEND_PID" ]] || wait "$FRONTEND_PID" 2>/dev/null || true
  [[ -z "$API_PID" ]] || wait "$API_PID" 2>/dev/null || true
  exit "$exit_code"
}

trap cleanup EXIT INT TERM

if [[ -x "./venv/Scripts/python.exe" ]]; then
  PYTHON="./venv/Scripts/python.exe"
elif [[ -x "./venv/bin/python" ]]; then
  PYTHON="./venv/bin/python"
else
  fail "Python virtual environment not found at ./venv"
fi

[[ -f ".env" ]] || fail ".env is missing; copy .env.example and configure it"

if [[ "$START_DATABASE" == "true" ]]; then
  command -v docker >/dev/null 2>&1 || fail "Docker is not installed or not on PATH"
  log "Starting PostgreSQL..."
  docker compose up -d db

  database_ready="false"
  for _ in {1..30}; do
    if docker compose exec -T db pg_isready -U postgres -d stablepay \
      >/dev/null 2>&1; then
      database_ready="true"
      break
    fi
    sleep 1
  done
  [[ "$database_ready" == "true" ]] || fail "PostgreSQL did not become ready"
fi

log "Applying database migrations..."
"$PYTHON" -m alembic -c backend/alembic.ini upgrade head

log "Starting FastAPI at http://${API_HOST}:${API_PORT}"
"$PYTHON" -m uvicorn main:app \
  --app-dir backend/app \
  --host "$API_HOST" \
  --port "$API_PORT" \
  --reload &
API_PID=$!

if [[ -f "$FRONTEND_DIRECTORY/package.json" ]]; then
  command -v npm >/dev/null 2>&1 || fail "npm is required to start React"
  log "Starting React from ${FRONTEND_DIRECTORY}/"
  (
    cd "$FRONTEND_DIRECTORY"
    npm run dev
  ) &
  FRONTEND_PID=$!
else
  log "React not started: ${FRONTEND_DIRECTORY}/package.json does not exist yet"
fi

log "Development environment is running. Press Ctrl+C to stop."

if [[ -n "$FRONTEND_PID" ]]; then
  wait -n "$API_PID" "$FRONTEND_PID"
else
  wait "$API_PID"
fi
