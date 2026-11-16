#!/usr/bin/env bash
# Start or stop the local processes (mock gateway, API, orchestrator, executor, feed consumer) on
# this machine. PostgreSQL comes from `make up`. Logs go to var/log, PIDs to var/run.
#
# Each process is started with only the variables it needs, taken from .env:
# the API gets no gateway key, the orchestrator gets the read-only agent keys, the feed consumer
# gets the one key that can read the event feed, and the write key goes to the executor alone.
# The same split is in docker-compose.yml.
#
# DEMO_HOLD_SECONDS=20 ./scripts/dev_stack.sh start  (or `make demo`) shortens the wait before a
# technician visit is offered, so that the whole journey can be shown in a minute. It changes
# two timings for the processes that use them and nothing else.
#
# SIMULATED_MODEL_SECONDS=1.5 ./scripts/dev_stack.sh start  gives the orchestrator a stand-in
# for a language model: the rules' answer after that many seconds. It is for load tests, to put
# a model's cost on every turn without calling one.
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077

SERVICES=(mock-oss care-api care-worker care-executor care-ingest)
mkdir -p var/log var/run

# The two timings a demo shortens, for the two processes that judge the hold.
demo_timings() {
  case "$1" in
    care-worker|care-executor)
      printf 'CARE_DISPATCH_HOLD_SECONDS=%s\nCARE_NETWORK_RECHECK_SECONDS=%s\n' \
        "$DEMO_HOLD_SECONDS" "$(( DEMO_HOLD_SECONDS / 2 > 5 ? DEMO_HOLD_SECONDS / 2 : 5 ))" ;;
  esac
}

# The stand-in model, for the one process that reads customers' messages.
simulated_model() {
  case "$1" in
    care-worker)
      printf 'CARE_LLM_PROVIDER=simulated\nCARE_LLM_SIMULATED_LATENCY_SECONDS=%s\n' \
        "$SIMULATED_MODEL_SECONDS" ;;
  esac
}

allowed_variables() {
  case "$1" in
    mock-oss)      echo '^(MOCK_OSS_|OSS_KEY_)' ;;
    care-api)      echo '^(CARE_ENV|CARE_DB_|CARE_JWT_SECRET|CARE_DEV_STAFF_KEY|CARE_API_|CARE_SURGE_|CARE_WORKER_CONCURRENCY=|CARE_STATUS_MAX_AGE_SECONDS=)' ;;
    care-worker)   echo '^(CARE_ENV|CARE_DB_|CARE_OSS_BASE_URL|CARE_OSS_BREAKER_|CARE_OSS_READ_RETRIES|CARE_LLM_|CARE_SURGE_|CARE_WORKER_CONCURRENCY=|CARE_IDENTITY_|CARE_STATUS_|CARE_FEED_|CARE_KNOWLEDGE_|CARE_LINE_|CARE_DISPATCH_|CARE_VISIT_|CARE_CREDIT_|CARE_APPROVAL_|CARE_NETWORK_|CARE_SUSPECTED_|CARE_OUTAGE_|CARE_CLOSE_|ANTHROPIC_API_KEY|OSS_KEY_(TRIAGE|DIAGNOSTICS|RESOLUTION|DISPATCH|NOTIFICATION)=)' ;;
    care-executor) echo '^(CARE_ENV|CARE_DB_|CARE_OSS_BASE_URL|CARE_OSS_BREAKER_|CARE_OSS_READ_RETRIES|CARE_CREDIT_|CARE_STATUS_|CARE_FEED_|CARE_LINE_|CARE_DISPATCH_|CARE_VISIT_|OSS_KEY_EXECUTOR=)' ;;
    care-ingest)   echo '^(CARE_ENV|CARE_DB_|CARE_OSS_BASE_URL|CARE_OSS_BREAKER_|CARE_OSS_READ_RETRIES|CARE_NOTIFY_|OSS_KEY_INGEST=)' ;;
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
    if [ -n "${DEMO_HOLD_SECONDS:-}" ]; then
      case "$DEMO_HOLD_SECONDS" in
        ''|*[!0-9]*) echo "DEMO_HOLD_SECONDS must be a whole number of seconds" >&2; exit 2 ;;
      esac
      grep -Ev '^(CARE_DISPATCH_HOLD_SECONDS|CARE_NETWORK_RECHECK_SECONDS)=' \
        "var/run/$service.env" >"var/run/$service.env.tmp" || true
      demo_timings "$service" >>"var/run/$service.env.tmp"
      mv "var/run/$service.env.tmp" "var/run/$service.env"
    fi
    if [ -n "${SIMULATED_MODEL_SECONDS:-}" ]; then
      if ! [[ "$SIMULATED_MODEL_SECONDS" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
        echo "SIMULATED_MODEL_SECONDS must be a number of seconds" >&2; exit 2
      fi
      grep -Ev '^(CARE_LLM_PROVIDER|CARE_LLM_SIMULATED_LATENCY_SECONDS)=' \
        "var/run/$service.env" >"var/run/$service.env.tmp" || true
      simulated_model "$service" >>"var/run/$service.env.tmp"
      mv "var/run/$service.env.tmp" "var/run/$service.env"
    fi
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
