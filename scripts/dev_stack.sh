#!/usr/bin/env bash
# Start or stop the local processes (mock gateway, API, orchestrator, executor) on this machine.
# PostgreSQL comes from `make up`. Logs go to var/log, PIDs to var/run.
#
# Each process is started with only the variables it needs, taken from .env:
# the API gets no gateway key, the orchestrator gets the read-only agent keys, and the write key
# goes to the executor alone. The same split is in docker-compose.yml.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

SERVICES=(mock-oss care-api care-worker care-executor)
mkdir -p var/log var/run

allowed_variables() {
  case "$1" in
    mock-oss)      echo '^(MOCK_OSS_|OSS_KEY_)' ;;
    care-api)      echo '^(CARE_ENV|CARE_DB_|CARE_JWT_SECRET|CARE_API_)' ;;
    care-worker)   echo '^(CARE_ENV|CARE_DB_|CARE_OSS_BASE_URL|CARE_LLM_|CARE_STATUS_|CARE_FEED_|CARE_CREDIT_|ANTHROPIC_API_KEY|OSS_KEY_(TRIAGE|DIAGNOSTICS|RESOLUTION|DISPATCH|NOTIFICATION)=)' ;;
    care-executor) echo '^(CARE_ENV|CARE_DB_|CARE_OSS_BASE_URL|OSS_KEY_EXECUTOR=)' ;;
  esac
}

start() {
  [ -f .env ] || { echo "No .env found. Run: make init" >&2; exit 1; }
  for service in "${SERVICES[@]}"; do
    if [ -f "var/run/$service.pid" ] && kill -0 "$(cat "var/run/$service.pid")" 2>/dev/null; then
      echo "$service already running (pid $(cat "var/run/$service.pid"))"
      continue
    fi
    grep -E "$(allowed_variables "$service")" .env >"var/run/$service.env"
    nohup env -i PATH="$PATH" HOME="$HOME" \
      uv run --env-file "var/run/$service.env" "$service" >"var/log/$service.log" 2>&1 &
    echo $! >"var/run/$service.pid"
    echo "started $service (pid $!)"
  done
}

stop() {
  for service in "${SERVICES[@]}"; do
    if [ -f "var/run/$service.pid" ]; then
      pid="$(cat "var/run/$service.pid")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "stopped $service (pid $pid)"
      fi
    fi
  done
}

status() {
  for service in "${SERVICES[@]}"; do
    if [ -f "var/run/$service.pid" ] && kill -0 "$(cat "var/run/$service.pid")" 2>/dev/null; then
      echo "$service: running (pid $(cat "var/run/$service.pid"))"
    else
      echo "$service: stopped"
    fi
  done
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|restart|status}" >&2; exit 2 ;;
esac
