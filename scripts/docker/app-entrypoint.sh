#!/bin/sh
set -eu

mkdir -p /app/data

if [ "${LENS_SKIP_DB_UPGRADE:-0}" != "1" ]; then
  lens db upgrade
fi

admin_username=${LENS_ADMIN_USERNAME:-admin}
: "${LENS_ADMIN_PASSWORD:?Set LENS_ADMIN_PASSWORD in .env}"
lens seed-admin --username "$admin_username" --password "$LENS_ADMIN_PASSWORD"

exec lens serve
