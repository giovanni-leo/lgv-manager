#!/bin/sh
# One-command installation on a fresh Ubuntu server.
#
#   sh docker/install.sh vc.example.ch "Lake Growth Ventures"
#
# Installs Docker if missing, writes .env with random secrets (only if .env
# does not exist yet) and starts the stack. Safe to run again: it keeps the
# existing .env and data, and rebuilds the application.
#
# Optional, for a machine that already uses ports 80/443 (e.g. Umbrel):
#   HTTP_PORT=8080 HTTPS_PORT=8443 LOCAL_DATA=1 sh docker/install.sh DOMAIN
# LOCAL_DATA=1 keeps the database and documents in ./data instead of Docker volumes.
set -eu

rand_hex() {  # $1 bytes of randomness, as hex
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    od -An -vtx1 -N"$1" /dev/urandom | tr -d ' \n'
  fi
}

DOMAIN="${1:?usage: sh docker/install.sh DOMAIN [SITE_NAME]}"
SITE_NAME="${2:-Lake Growth Ventures}"

cd "$(dirname "$0")/.."

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  SUDO="sudo"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | $SUDO sh
fi

if [ -f .env ]; then
  echo ".env already exists: keeping it."
else
  echo "Writing .env with random secrets..."
  umask 077
  cat > .env <<EOF
DOMAIN=$DOMAIN
SITE_NAME=$SITE_NAME
DJANGO_SECRET_KEY=$(rand_hex 32)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
REQUIRE_2FA=1
DOCUMENT_MAX_UPLOAD_MB=100
POSTGRES_DB=vc
POSTGRES_USER=vc
POSTGRES_PASSWORD=$(rand_hex 16)
EOF
  if [ -n "${HTTP_PORT:-}" ]; then echo "HTTP_PORT=$HTTP_PORT" >> .env; fi
  if [ -n "${HTTPS_PORT:-}" ]; then echo "HTTPS_PORT=$HTTPS_PORT" >> .env; fi
  if [ "${LOCAL_DATA:-0}" = "1" ]; then
    echo "COMPOSE_FILE=docker-compose.yml:docker/compose.local-data.yml" >> .env
  fi
fi

if grep -q "compose.local-data.yml" .env; then
  # The app runs as uid 1000 in its container and must be able to write documents.
  mkdir -p data/postgres data/media data/caddy data/caddy-config
  $SUDO chown 1000:1000 data/media
fi

echo "Building and starting the application (a few minutes the first time)..."
$SUDO docker compose up -d --build

echo
echo "Done. Next step, create your account:"
echo "  $SUDO docker compose exec app python manage.py createsuperuser"
PORT=$(grep -E '^HTTPS_PORT=' .env | cut -d= -f2 || true)
echo "Then open https://$DOMAIN"
if [ -n "$PORT" ]; then
  echo "(this machine listens on port $PORT: forward the router's port 443 to it, and port 80 to HTTP_PORT)"
fi
