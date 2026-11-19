#!/bin/sh
# Sets up the nightly encrypted backup to Google Drive. Safe to run again.
#
# Open the SSH session from your PC with a tunnel, so the Google sign-in page can
# reach this server (needed once, for step 3):
#   ssh -L 53682:127.0.0.1:53682 umbrel@192.168.1.174
# then:
#   cd ~/lgv-manager && sh docker/backup-setup.sh
set -eu
cd "$(dirname "$0")/.."

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi
DC="$SUDO docker compose --profile backup"

env_get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- || true; }
env_set() {
  if grep -qE "^$1=" .env; then
    $SUDO sed -i "s|^$1=.*|$1=$2|" .env
  else
    printf '%s=%s\n' "$1" "$2" | $SUDO tee -a .env >/dev/null
  fi
}

[ -f .env ] || { echo "No .env here: run docker/install.sh first."; exit 1; }

echo "== 1/5 Building the backup container"
$DC build backup

echo
echo "== 2/5 Encryption key"
if [ -n "$(env_get BACKUP_AGE_RECIPIENT)" ]; then
  echo "A key is already set up (public key $(env_get BACKUP_AGE_RECIPIENT)). Keeping it."
else
  KEYS=$($SUDO docker run --rm lgv-backup:latest age-keygen 2>/dev/null)
  PUBLIC=$(printf '%s\n' "$KEYS" | grep -o 'age1[0-9a-z]*' | head -1)
  SECRET=$(printf '%s\n' "$KEYS" | grep '^AGE-SECRET-KEY-')
  echo
  echo "################################################################################"
  echo "  BACKUP SECRET KEY - the only way to read the backups."
  echo "  Save it NOW in your password manager. It is not stored on this server and"
  echo "  will not be shown again. Without it, the backups cannot be restored."
  echo
  echo "  $SECRET"
  echo
  echo "################################################################################"
  while :; do
    printf 'Type SAVED once the key is in your password manager: '
    read -r answer
    [ "$answer" = "SAVED" ] && break
  done
  env_set BACKUP_AGE_RECIPIENT "$PUBLIC"
  unset KEYS SECRET
  clear 2>/dev/null || true
  echo "Public key saved in .env: $PUBLIC"
fi

echo
echo "== 3/5 Google Drive"
$SUDO mkdir -p data/backup
$SUDO chmod 700 data/backup
if $SUDO grep -qs '^\[gdrive\]' data/backup/rclone.conf; then
  echo "Google Drive is already connected. Keeping it."
else
  echo "A link starting with http://127.0.0.1:53682/auth appears below."
  echo "Open it in the browser of the PC where you opened this SSH session,"
  echo "sign in with the Google account for the backups and click Allow."
  echo "(The app only gets access to the files it creates, in the LGV-Backups folder.)"
  echo
  $SUDO docker run --rm -it --network host -v "$PWD/data/backup:/config" lgv-backup:latest \
    rclone config create gdrive drive scope=drive.file
  $SUDO chmod 600 data/backup/rclone.conf
fi

echo
echo "== 4/5 Alert email and schedule"
EMAIL=$(env_get BACKUP_ALERT_EMAIL)
if [ -z "$EMAIL" ]; then
  printf 'Email address that receives an alert when a backup fails: '
  read -r EMAIL
  env_set BACKUP_ALERT_EMAIL "$EMAIL"
fi
[ -n "$(env_get BACKUP_TIME)" ] || env_set BACKUP_TIME "02:30"
PROFILES=$(env_get COMPOSE_PROFILES)
case ",$PROFILES," in
  *,backup,*) ;;
  ,,) env_set COMPOSE_PROFILES backup ;;
  *) env_set COMPOSE_PROFILES "$PROFILES,backup" ;;
esac
$DC up -d backup

echo
echo "== 5/5 Check and first backup"
i=0
until [ "$($DC ps backup --format '{{.State}}' 2>/dev/null)" = "running" ] && sleep 3 \
      && [ "$($DC ps backup --format '{{.State}}' 2>/dev/null)" = "running" ]; do
  i=$((i + 1))
  if [ "$i" -ge 10 ]; then
    echo "The backup container does not start. Its log:"
    $DC logs --tail 30 backup
    exit 1
  fi
  sleep 3
done
$DC exec backup lgv-backup check || echo "(fix what is marked above, then run: sudo docker compose exec backup lgv-backup check)"
$DC exec backup lgv-backup run
echo
echo "Done. A backup runs every night at $(env_get BACKUP_TIME) (Swiss time)."
echo "A test email was sent to $EMAIL. Check also the LGV-Backups folder in Google Drive."
