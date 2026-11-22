#!/usr/bin/env bash
# The S3 bucket that holds this configuration's Terraform state (the backend in versions.tf).
# It is made here, with the AWS CLI, and not by Terraform: destroying the deployment must never
# delete the record of what it destroys.
#
#   state-bucket.sh create   once, before the first `terraform init`
#   state-bucket.sh delete   after `terraform destroy`: every version of the state, then the bucket
#
# Uses the AWS CLI if installed, else the official image through Docker, with the profile of
# the backend.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
setting() { sed -n "s/^ *$1 *= *\"\(.*\)\"/\1/p" "$HERE/versions.tf" | head -1; }
BUCKET="$(setting bucket)"
REGION="$(setting region)"
PROFILE="$(setting profile)"

aws_() {
  if command -v aws > /dev/null; then
    aws --profile "$PROFILE" --region "$REGION" "$@"
  else
    docker run --rm -i -v "$HOME/.aws:/root/.aws:ro" -e AWS_PROFILE="$PROFILE" \
      -e AWS_REGION="$REGION" amazon/aws-cli:2.42.1 "$@"
  fi
}

create() {
  aws_ s3api create-bucket --bucket "$BUCKET" \
    --create-bucket-configuration "LocationConstraint=$REGION" > /dev/null
  aws_ s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  # Every write of the state is kept, so a bad apply can be undone from the version before it.
  aws_ s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
  aws_ s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}, "BucketKeyEnabled": true}]}'
  aws_ s3api put-bucket-policy --bucket "$BUCKET" --policy "$(jq -cn --arg b "$BUCKET" '{
    Version: "2012-10-17",
    Statement: [{
      Sid: "OnlyOverTls", Effect: "Deny", Principal: "*", Action: "s3:*",
      Resource: ["arn:aws:s3:::\($b)", "arn:aws:s3:::\($b)/*"],
      Condition: {Bool: {"aws:SecureTransport": "false"}}
    }]}')"
  aws_ s3api put-bucket-tagging --bucket "$BUCKET" --tagging \
    'TagSet=[{Key=Project,Value=skylink-care},{Key=Purpose,Value=terraform-state},{Key=ManagedBy,Value=state-bucket.sh}]'
  echo "created s3://$BUCKET in $REGION: private, versioned, encrypted, TLS only"
}

delete() {
  local batch
  while true; do
    batch="$(aws_ s3api list-object-versions --bucket "$BUCKET" --max-items 500 --output json |
      jq -c '{Objects: [(.Versions // [])[], (.DeleteMarkers // [])[] | {Key, VersionId}], Quiet: true}')"
    [ "$(jq '.Objects | length' <<< "$batch")" -gt 0 ] || break
    aws_ s3api delete-objects --bucket "$BUCKET" --delete file:///dev/stdin <<< "$batch" > /dev/null
  done
  aws_ s3api delete-bucket --bucket "$BUCKET"
  echo "deleted s3://$BUCKET"
}

case "${1:-}" in
  create) create ;;
  delete) delete ;;
  *)
    sed -n '2,8p' "$0" >&2
    exit 2
    ;;
esac
