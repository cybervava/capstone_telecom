#!/bin/sh
# One-shot container set-up, run by the `setup` service in docker-compose.yml:
# generate the synthetic data pack if it is not there yet, migrate the care database,
# seed the mock gateway, and load the reference data into the care platform.
#
# Seeding recreates the gateway's store: its tickets are gone and its event sequence restarts.
# The care platform therefore forgets what it knew of the previous gateway state in the same
# step, otherwise it would refuse the restarted feed and write to tickets that no longer exist.
set -eu

# The pack is kept under the version of the generator that wrote it. A release whose generator
# writes other columns generates its own pack, and a release that is rolled back to still
# finds the one it wrote (releases before 1.1.0 kept theirs at the top of the volume).
PACK_VERSION="$(python -c 'from skylink_datagen.config import GENERATOR_VERSION; print(GENERATOR_VERSION)')"
DATA_DIR="/data/generated/$PACK_VERSION"

if [ ! -f "$DATA_DIR/manifest.json" ]; then
  skylink-datagen generate --out "$DATA_DIR" --customers "${CUSTOMERS:-40000}" --seed 42
fi
care-admin migrate
mock-oss seed --data "$DATA_DIR" --db /state/mock_oss.sqlite
care-admin reset-runtime
care-admin load-reference --data "$DATA_DIR"
care-admin index-guides
