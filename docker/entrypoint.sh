#!/bin/sh
set -e
# Apply migrations (idempotent) before starting the server.
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
fi
exec "$@"
