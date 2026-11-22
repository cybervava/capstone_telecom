#!/usr/bin/env bash
# Starts a release on the demo host and checks it. Runs as root through SSM: the deploy document
# (deploy/aws/terraform/host.tf) unpacks the release bundle into /opt/skylink/releases/<commit>
# and calls `deploy`; deploy/aws/operate.sh calls the rest.
#
#   deploy.sh deploy <commit>    start that release, check it, and make it the current one
#   deploy.sh rollback           start the previous release again, and check it
#   deploy.sh redeploy           start the current release again, from a clean demo state
#   deploy.sh restart <service>  recreate one service with fresh settings; the rest keep running
#   deploy.sh compose <args>     docker compose, for the current release
#
# Every start begins from a clean demo state: the stack is stopped, then the setup service seeds
# the gateway and clears the runtime state before anything else starts (docker-compose.yml).
# A release that does not come up healthy, or fails the smoke checks, is replaced by the release
# that ran before it, and the script exits non-zero so that the pipeline shows the failure.
set -euo pipefail

ROOT=/opt/skylink
# REGION, ENV_SECRET and REPOSITORY, written at first boot (cloud-init.yaml.tftpl).
# shellcheck source=/dev/null
. /etc/skylink/host.conf
export PATH="$PATH:/snap/bin" AWS_REGION="$REGION"

log() { printf '[deploy %s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

compose() { # <release dir> <args...>: docker compose for that release, with the host's settings
  local dir="$1"
  shift
  CARE_IMAGE="$(cat "$dir/IMAGE")" docker compose --project-directory "$dir" \
    -f "$dir/docker-compose.yml" -f "$dir/deploy/aws/compose.aws.yml" \
    --env-file "$ROOT/.env" --profile stack "$@"
}

fetch_settings() { # the host's .env from Secrets Manager; mktemp makes it readable by root only
  local tmp
  tmp="$(mktemp "$ROOT/.env.XXXXXX")"
  # Through a pipe: run by SSM, the snap of the AWS CLI writes nothing at all to a file its
  # output is redirected to, and still exits 0.
  aws secretsmanager get-secret-value --secret-id "$ENV_SECRET" \
    --query SecretString --output text | cat > "$tmp"
  if ! grep -q '^CARE_DB_PASSWORD=.' "$tmp"; then
    rm -f "$tmp"
    log "the settings read from $ENV_SECRET are empty or incomplete"
    return 1
  fi
  mv "$tmp" "$ROOT/.env"
}

pull() { # <release dir>
  aws ecr get-login-password |
    docker login --username AWS --password-stdin "${REPOSITORY%%/*}" > /dev/null
  docker pull --quiet "$(cat "$1/IMAGE")" > /dev/null
}

start() { # <release dir>: stop what runs, then start this release and wait until it is healthy
  compose "$1" down --remove-orphans --timeout 20
  compose "$1" up -d --no-build --wait --wait-timeout 300 --remove-orphans
}

smoke() { # <release dir>: the live checks of `make smoke`, from the host, against loopback
  docker run --rm --network host --env-file "$ROOT/.env" \
    -v "$1/scripts/smoke.py:/smoke.py:ro" "$(cat "$1/IMAGE")" python /smoke.py
}

# -e, not -f: a link that is not there must read as no release, not as a path.
current() { readlink -e "$ROOT/current" 2> /dev/null || true; }
previous() { readlink -e "$ROOT/previous" 2> /dev/null || true; }

make_current() { # <release dir>
  local was
  was="$(current)"
  if [ -n "$was" ] && [ "$was" != "$1" ]; then ln -sfn "$was" "$ROOT/previous"; fi
  ln -sfn "$1" "$ROOT/current"
}

prune() { # keep the five newest releases and the two in use; drop images unused for a day
  local keep_current keep_previous dir
  keep_current="$(current)"
  keep_previous="$(previous)"
  find "$ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn |
    tail -n +6 | cut -d' ' -f2- | while read -r dir; do
    if [ "$dir" != "$keep_current" ] && [ "$dir" != "$keep_previous" ]; then rm -rf -- "$dir"; fi
  done
  docker image prune --all --force --filter "until=24h" > /dev/null || true
}

deploy() { # <commit>
  local release="$1" dir="$ROOT/releases/$1" good
  [[ "$release" =~ ^[0-9a-f]{8,40}$ ]] || {
    log "not a commit: $release"
    return 2
  }
  # The runner packs the files as it checked them out, writable by all.
  chmod -R go-w "$dir"
  echo "$REPOSITORY:$release" > "$dir/IMAGE"
  good="$(current)"
  log "release $release: fetching its image and the host's settings"
  fetch_settings
  pull "$dir"
  log "starting it"
  if start "$dir" && log "checking it" && smoke "$dir"; then
    make_current "$dir"
    prune
    log "release $release is live"
    return 0
  fi
  log "release $release failed"
  if [ -n "$good" ] && [ "$good" != "$dir" ]; then
    log "starting $(basename "$good"), the release before it, again"
    if pull "$good" && start "$good"; then
      log "$(basename "$good") is running again"
    else
      log "that failed too: the host needs a look (operate.sh status, operate.sh logs <service>)"
    fi
  fi
  return 1
}

rollback() {
  local prev
  prev="$(previous)"
  if [ -z "$prev" ] || [ ! -d "$prev" ]; then
    log "there is no previous release on this host"
    return 1
  fi
  log "rolling back to $(basename "$prev")"
  fetch_settings
  pull "$prev"
  start "$prev"
  smoke "$prev"
  make_current "$prev"
  log "release $(basename "$prev") is live"
}

redeploy() {
  local dir
  dir="$(current)"
  [ -n "$dir" ] || {
    log "nothing has been deployed yet"
    return 1
  }
  fetch_settings
  pull "$dir"
  start "$dir"
  log "release $(basename "$dir") started again, from a clean demo state"
}

restart() { # <service>
  local dir
  dir="$(current)"
  fetch_settings
  compose "$dir" up -d --no-build --no-deps --force-recreate "$1"
}

case "${1:-}" in
  deploy) deploy "${2:?usage: deploy.sh deploy <commit>}" ;;
  rollback) rollback ;;
  redeploy) redeploy ;;
  restart) restart "${2:?usage: deploy.sh restart <service>}" ;;
  compose)
    shift
    dir="$(current)"
    [ -n "$dir" ] || {
      log "nothing has been deployed yet"
      exit 1
    }
    compose "$dir" "$@"
    ;;
  *)
    sed -n '2,15p' "$0" >&2
    exit 2
    ;;
esac
