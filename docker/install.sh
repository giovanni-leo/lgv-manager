#!/bin/sh
# One-command installation on a fresh Ubuntu server.
#
#   sh docker/install.sh vc.example.ch "Lake Growth Ventures"
#
# Installs Docker if missing, writes .env with random secrets (only if .env
# does not exist yet) and starts the stack. Safe to run again: it keeps the
# existing .env and data, and rebuilds the application.
set -eu

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
DJANGO_SECRET_KEY=$(openssl rand -hex 32)
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=$DOMAIN
DJANGO_CSRF_TRUSTED_ORIGINS=https://$DOMAIN
REQUIRE_2FA=1
DOCUMENT_MAX_UPLOAD_MB=100
POSTGRES_DB=vc
POSTGRES_USER=vc
POSTGRES_PASSWORD=$(openssl rand -hex 16)
EOF
fi

echo "Building and starting the application (a few minutes the first time)..."
$SUDO docker compose up -d --build

echo
echo "Done. Next step, create your account:"
echo "  $SUDO docker compose exec app python manage.py createsuperuser"
echo "Then open https://$DOMAIN"
