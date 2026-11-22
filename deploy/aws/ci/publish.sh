#!/bin/sh
# The pipeline's publish step (.gitlab-ci.yml): copies the scanned image from this project's
# registry to ECR under the same commit tag, and uploads the commit's release bundle for the host.
set -eu

: "${ECR_REPOSITORY:?}" "${RELEASE_BUCKET:?}" "${RELEASE:?}" "${IMAGE:?}"
target="$ECR_REPOSITORY:$RELEASE"

if aws ecr describe-images --repository-name "${ECR_REPOSITORY#*/}" \
  --image-ids "imageTag=$RELEASE" > /dev/null 2>&1; then
  echo "$target is already in ECR, where a tag never moves"
else
  echo "$CI_REGISTRY_PASSWORD" |
    docker login --username "$CI_REGISTRY_USER" --password-stdin "$CI_REGISTRY"
  aws ecr get-login-password |
    docker login --username AWS --password-stdin "${ECR_REPOSITORY%%/*}"
  docker pull --quiet "$IMAGE"
  docker tag "$IMAGE" "$target"
  docker push --quiet "$target"
fi

# What the host needs besides the image: the compose files, the setup and smoke scripts, the
# proxy's configuration and its own deploy script.
tar -czf release.tgz docker-compose.yml scripts/container_setup.sh scripts/smoke.py \
  deploy/aws/compose.aws.yml deploy/aws/Caddyfile deploy/aws/host
aws s3 cp release.tgz "s3://$RELEASE_BUCKET/releases/$RELEASE.tgz" --only-show-errors
echo "release $RELEASE: $target, s3://$RELEASE_BUCKET/releases/$RELEASE.tgz"
