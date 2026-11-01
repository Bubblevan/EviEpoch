#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=app_password="$EPOCH_APP_DB_PASSWORD" \
  --file=/docker-entrypoint-initdb.d/10-roles.template
