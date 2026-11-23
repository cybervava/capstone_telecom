#!/usr/bin/env bash
# Runs the AWS demo from a laptop. Needs the AWS CLI (installed, or else the official image
# through Docker), the AWS profile of the Terraform configuration and its applied state. It
# reaches the host through SSM, so it works from any network, with no SSH key or open port.
#
#   operate.sh env-init             create the host's settings, once: fresh secrets, the model
#                                   and tracing settings of ./.env, demo timing, the sign-in
#   operate.sh sign-in              the site's address, its shared sign-in and the demo console
#   operate.sh staff-key            the key the staff desk asks for
#   operate.sh oss-key              the key the demo console asks for (the simulator's admin key)
#   operate.sh scene <args...>      a mock-oss command on the host: scene outages --active
#   operate.sh reset                start the current release again, from a clean demo state
#   operate.sh rollback             start the release before the current one again
#   operate.sh llm openai|offline   switch what reads the messages; only the worker restarts
#   operate.sh logs <service>       the last 200 lines of one service's log
#   operate.sh status               the containers on the host
#   operate.sh gitlab-setup         give the GitLab project's pipeline this deployment's settings
#                                   (needs GITLAB_TOKEN, a GitLab.com token with the api scope)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROFILE="${AWS_PROFILE:-skylink-demo}"
GITLAB_PROJECT="${GITLAB_PROJECT:-prathap.aneesh/capstone_telecom}"
HOST_DEPLOY=/opt/skylink/current/deploy/aws/host/deploy.sh

tf() { terraform -chdir="$HERE/terraform" output -raw "$1"; }

REGION="$(tf region)"

aws_() {
  if command -v aws > /dev/null; then
    aws --profile "$PROFILE" --region "$REGION" "$@"
  else
    docker run --rm -i -v "$HOME/.aws:/root/.aws:ro" -e AWS_PROFILE="$PROFILE" \
      -e AWS_REGION="$REGION" amazon/aws-cli:2.42.1 "$@"
  fi
}

quote() { # each argument single-quoted, for the host's shell
  local arg
  for arg in "$@"; do printf "'%s' " "${arg//\'/\'\\\'\'}"; done
}

on_host() { # <command>: run it on the host as root through SSM, and print what it printed
  local instance id status=Pending
  instance="$(tf instance_id)"
  id="$(aws_ ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --parameters "$(jq -cn --arg c "$1" '{commands: [$c], executionTimeout: ["900"]}')" \
    --comment "operate.sh" --query Command.CommandId --output text)"
  for _ in $(seq 1 300); do
    sleep 3
    status="$(aws_ ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
      --query Status --output text 2> /dev/null || echo Pending)"
    case "$status" in Pending | InProgress | Delayed) ;; *) break ;; esac
  done
  aws_ ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query '[StandardOutputContent, StandardErrorContent]' --output text
  [ "$status" = Success ] || {
    echo "SSM status: $status" >&2
    return 1
  }
}

secret() { aws_ secretsmanager get-secret-value --secret-id "$1" --query SecretString --output text; }

env_init() {
  local env_secret sign_in_secret work
  env_secret="$(tf env_secret)"
  sign_in_secret="$(tf sign_in_secret)"
  if [ "$(aws_ secretsmanager describe-secret --secret-id "$env_secret" \
    --query VersionIdsToStages --output text)" != None ]; then
    echo "$env_secret already has a value: left as it is."
    return 0
  fi
  work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN
  python3 "$HERE/host_env.py" --site "$(tf site_address)" --out "$work/out"
  aws_ secretsmanager put-secret-value --secret-id "$env_secret" \
    --secret-string file:///dev/stdin < "$work/out/env" > /dev/null
  aws_ secretsmanager put-secret-value --secret-id "$sign_in_secret" \
    --secret-string file:///dev/stdin < "$work/out/sign-in.json" > /dev/null
  echo "Wrote $env_secret and $sign_in_secret."
}

llm() { # openai|offline
  local env_secret
  env_secret="$(tf env_secret)"
  secret "$env_secret" | sed "s/^CARE_LLM_PROVIDER=.*/CARE_LLM_PROVIDER=$1/" |
    aws_ secretsmanager put-secret-value --secret-id "$env_secret" \
      --secret-string file:///dev/stdin > /dev/null
  on_host "bash $HOST_DEPLOY restart worker"
}

gitlab_setup() {
  : "${GITLAB_TOKEN:?set GITLAB_TOKEN to a GitLab.com token with the api scope}"
  local api pair name value
  api="https://gitlab.com/api/v4/projects/${GITLAB_PROJECT//\//%2F}/variables"
  for pair in "AWS_REGION=$REGION" "AWS_DEPLOY_ROLE_ARN=$(tf deploy_role_arn)" \
    "ECR_REPOSITORY=$(tf ecr_repository)" "RELEASE_BUCKET=$(tf release_bucket)" \
    "DEPLOY_DOCUMENT=$(tf deploy_document)" "DEMO_INSTANCE_ID=$(tf instance_id)" \
    "DEMO_SITE=$(tf site_address)"; do
    name="${pair%%=*}"
    value="${pair#*=}"
    # Protected: only pipelines of protected branches see them. None of them is a secret.
    if curl -fsS -o /dev/null -H "PRIVATE-TOKEN: $GITLAB_TOKEN" "$api/$name" 2> /dev/null; then
      curl -fsS -o /dev/null -X PUT -H "PRIVATE-TOKEN: $GITLAB_TOKEN" "$api/$name" \
        --data-urlencode "value=$value" --data protected=true
    else
      curl -fsS -o /dev/null -X POST -H "PRIVATE-TOKEN: $GITLAB_TOKEN" "$api" \
        --data-urlencode "key=$name" --data-urlencode "value=$value" --data protected=true
    fi
    echo "  $name=$value"
  done
}

case "${1:-}" in
  env-init) env_init ;;
  sign-in)
    secret "$(tf sign_in_secret)" |
      jq -r '"Site:     \(.site)\nUser:     \(.user)\nPassword: \(.password)\nConsole:  \(.site | rtrimstr("/"))/sim/console/"'
    ;;
  staff-key) secret "$(tf env_secret)" | sed -n 's/^CARE_DEV_STAFF_KEY=//p' ;;
  oss-key) secret "$(tf env_secret)" | sed -n 's/^OSS_KEY_ADMIN=//p' ;;
  scene)
    shift
    on_host "bash $HOST_DEPLOY compose exec -T mock-oss mock-oss $(quote "$@")"
    ;;
  reset) on_host "bash $HOST_DEPLOY redeploy" ;;
  rollback) on_host "bash $HOST_DEPLOY rollback" ;;
  llm)
    case "${2:-}" in
      openai | offline) llm "$2" ;;
      *)
        echo "usage: operate.sh llm openai|offline" >&2
        exit 2
        ;;
    esac
    ;;
  logs)
    [[ "${2:-}" =~ ^[a-z-]+$ ]] || {
      echo "usage: operate.sh logs <service>" >&2
      exit 2
    }
    on_host "bash $HOST_DEPLOY compose logs --no-color --tail 200 $2"
    ;;
  status) on_host "bash $HOST_DEPLOY compose ps" ;;
  gitlab-setup) gitlab_setup ;;
  *)
    sed -n '2,18p' "$0" >&2
    exit 2
    ;;
esac
