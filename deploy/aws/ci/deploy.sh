#!/bin/sh
# The pipeline's deploy step (.gitlab-ci.yml): has SSM run the deploy document on the demo host
# for this commit, waits for it, prints what the host printed, and checks the site from outside.
set -eu

: "${RELEASE:?}" "${DEPLOY_DOCUMENT:?}" "${DEMO_INSTANCE_ID:?}" "${DEMO_SITE:?}"

id=$(aws ssm send-command --document-name "$DEPLOY_DOCUMENT" \
  --instance-ids "$DEMO_INSTANCE_ID" --parameters "Release=$RELEASE" \
  --comment "GitLab pipeline $CI_PIPELINE_ID" --timeout-seconds 120 \
  --query Command.CommandId --output text)
echo "SSM command $id on $DEMO_INSTANCE_ID"

# Up to 15 minutes: the document allows the host that long.
status=Pending
i=0
while [ "$i" -lt 180 ]; do
  sleep 5
  i=$((i + 1))
  status=$(aws ssm get-command-invocation --command-id "$id" \
    --instance-id "$DEMO_INSTANCE_ID" --query Status --output text 2> /dev/null) ||
    status=Pending
  case "$status" in
    Pending | InProgress | Delayed) ;;
    *) break ;;
  esac
done

aws ssm get-command-invocation --command-id "$id" --instance-id "$DEMO_INSTANCE_ID" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
echo "SSM status: $status"
[ "$status" = Success ]

curl --fail --silent --show-error --retry 10 --retry-delay 3 --retry-all-errors \
  "https://$DEMO_SITE/health"
echo
echo "Live: https://$DEMO_SITE"
