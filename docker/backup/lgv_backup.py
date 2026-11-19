"""Encrypted off-site backups of LGV Manager: database, documents and .env.

Runs in the `backup` container (see docker-compose.yml).

    lgv_backup.py schedule          run every night at BACKUP_TIME (default 02:30), alert by email on failure
    lgv_backup.py run               one backup now
    lgv_backup.py check             test the settings, Google Drive and the alert email
    lgv_backup.py list              backups available
    lgv_backup.py restore [DATE]    restore the database and documents (latest backup by default)

Everything is encrypted on this machine with age, using only the PUBLIC key in BACKUP_AGE_RECIPIENT.
The secret key that can read the backups is kept by a person (password manager), never on the server:
it is asked for, and forgotten, only during a restore.

Layout on the remote (BACKUP_REMOTE, default gdrive:LGV-Backups):
    daily/lgv-YYYY-MM-DD.tar.age    database dump + document manifest + .env, one per day
    documents/<sha256>.age          each document once, named by the hash of its content

Retention: the 7 most recent daily archives, the newest of each of the last 4 weeks and of the last
6 months. Documents are uploaded once and kept (they are small and rarely deleted).
"""

import datetime as dt
import getpass
import hashlib
import io
import json
import os
import smtplib
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from email.message import EmailMessage
from pathlib import Path

MEDIA = Path(os.environ.get("BACKUP_MEDIA_DIR", "/data/media"))
CONFIG = Path(os.environ.get("BACKUP_CONFIG_DIR", "/config"))
ENV_FILE = Path(os.environ.get("BACKUP_ENV_FILE", "/backup/.env"))
REMOTE = os.environ.get("BACKUP_REMOTE", "gdrive:LGV-Backups").rstrip("/")
RECIPIENT = os.environ.get("BACKUP_AGE_RECIPIENT", "").strip()
KEEP_DAILY, KEEP_WEEKLY, KEEP_MONTHLY = 7, 4, 6
STATUS = CONFIG / "status.json"
HASH_CACHE = CONFIG / "hashes.json"


class BackupError(Exception):
    pass


def log(message):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {message}", flush=True)


def sh(*args, input=None, env=None):
    result = subprocess.run(args, input=input, capture_output=True, env={**os.environ, **(env or {})})
    if result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace").strip()[-800:]
        raise BackupError(f"{args[0]} {args[1] if len(args) > 1 else ''} failed: {detail}")
    return result.stdout


def pg_env():
    return {"PGHOST": os.environ.get("POSTGRES_HOST", "db"), "PGPORT": os.environ.get("POSTGRES_PORT", "5432"),
            "PGUSER": os.environ.get("POSTGRES_USER", "vc"), "PGDATABASE": os.environ.get("POSTGRES_DB", "vc"),
            "PGPASSWORD": os.environ.get("POSTGRES_PASSWORD", "")}


# --- Remote (rclone) ----------------------------------------------------------------------------

def remote_files(folder):
    try:
        out = sh("rclone", "lsf", "--files-only", f"{REMOTE}/{folder}")
    except BackupError as exc:
        if "directory not found" in str(exc).lower():
            return []
        raise
    return [line for line in out.decode().splitlines() if line]


def daily_archives():
    """{date: filename} of the archives on the remote."""
    found = {}
    for name in remote_files("daily"):
        try:
            found[dt.date.fromisoformat(name.removeprefix("lgv-").removesuffix(".tar.age"))] = name
        except ValueError:
            continue
    return found


def to_keep(dates):
    """The dates whose archive is kept: 7 most recent, newest of the last 4 weeks and 6 months."""
    newest_first = sorted(dates, reverse=True)
    keep = set(newest_first[:KEEP_DAILY])
    for key, count in ((lambda d: d.isocalendar()[:2], KEEP_WEEKLY), (lambda d: (d.year, d.month), KEEP_MONTHLY)):
        seen = []
        for d in newest_first:
            if key(d) not in seen:
                seen.append(key(d))
                if len(seen) > count:
                    break
                keep.add(d)
    return keep


# --- Backup -------------------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def document_manifest():
    """{relative path: sha256} of every file in the media folder (hashes cached by size and mtime)."""
    try:
        cache = json.loads(HASH_CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    manifest, new_cache = {}, {}
    for path in sorted(p for p in MEDIA.rglob("*") if p.is_file()):
        rel, stat = str(path.relative_to(MEDIA)), path.stat()
        cached = cache.get(rel)
        digest = cached[2] if cached and cached[:2] == [stat.st_size, stat.st_mtime_ns] else _sha256(path)
        manifest[rel] = digest
        new_cache[rel] = [stat.st_size, stat.st_mtime_ns, digest]
    HASH_CACHE.write_text(json.dumps(new_cache))
    return manifest


def run_backup(today=None):
    if not RECIPIENT.startswith("age1"):
        raise BackupError("BACKUP_AGE_RECIPIENT is not set: run docker/backup-setup.sh first.")
    today = today or dt.date.today()
    started = time.monotonic()
    companies = sh("psql", "-tAc", "SELECT count(*) FROM companies", env=pg_env()).decode().strip()
    if companies == "0" and daily_archives():
        # A new or wiped server: do not let an empty backup replace or outlive the good ones.
        raise BackupError("The database has no companies while backups exist: not backing up. "
                          "Restore first (lgv-backup restore), then backups resume.")
    with tempfile.TemporaryDirectory(dir=CONFIG) as tmp:
        tmp = Path(tmp)
        log("Dumping the database")
        dump = tmp / "database.dump"
        sh("pg_dump", "--format=custom", "--no-owner", "--file", str(dump), env=pg_env())

        log("Checking documents")
        manifest = document_manifest()
        uploaded = set(name.removesuffix(".age") for name in remote_files("documents"))
        missing = {digest: rel for rel, digest in manifest.items() if digest not in uploaded}
        if missing:
            staging = tmp / "documents"
            staging.mkdir()
            for digest, rel in missing.items():
                sh("age", "-r", RECIPIENT, "-o", str(staging / f"{digest}.age"), str(MEDIA / rel))
            log(f"Uploading {len(missing)} new document(s)")
            sh("rclone", "copy", "--transfers", "4", str(staging), f"{REMOTE}/documents")

        archive = tmp / f"lgv-{today.isoformat()}.tar"
        with tarfile.open(archive, "w") as tar:
            tar.add(dump, "database.dump")
            data = json.dumps({"created": dt.datetime.now().isoformat(timespec="seconds"),
                               "documents": manifest}, indent=1).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
            if ENV_FILE.is_file():
                tar.add(ENV_FILE, "env")
        sh("age", "-r", RECIPIENT, "-o", f"{archive}.age", str(archive))
        size = Path(f"{archive}.age").stat().st_size
        log(f"Uploading the archive ({size / 1e6:.1f} MB)")
        sh("rclone", "copyto", f"{archive}.age", f"{REMOTE}/daily/{archive.name}.age")

    archives = daily_archives()
    if today not in archives:
        raise BackupError("The archive is not on the remote after the upload.")
    for d in sorted(set(archives) - to_keep(archives)):
        log(f"Removing old archive {archives[d]}")
        sh("rclone", "deletefile", f"{REMOTE}/daily/{archives[d]}")

    status = {"finished": dt.datetime.now().isoformat(timespec="seconds"), "date": today.isoformat(),
              "archive_bytes": size, "documents": len(manifest), "documents_uploaded": len(missing),
              "seconds": round(time.monotonic() - started)}
    STATUS.write_text(json.dumps(status, indent=1))
    log(f"Backup done: {len(manifest)} documents ({len(missing)} new), archive {size / 1e6:.1f} MB")
    return status


# --- Alerts -------------------------------------------------------------------------------------

def send_email(subject, body):
    to = os.environ.get("BACKUP_ALERT_EMAIL", "").strip()
    user, password = os.environ.get("MAIL_IMAP_USER", ""), os.environ.get("MAIL_IMAP_PASSWORD", "").replace(" ", "")
    host = os.environ.get("BACKUP_SMTP_HOST") or ("smtp.gmail.com" if "gmail" in os.environ.get(
        "MAIL_IMAP_HOST", "") else "")
    if not (to and user and password and host):
        log("Alert email not configured (BACKUP_ALERT_EMAIL, MAIL_IMAP_USER/PASSWORD)")
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("BACKUP_SMTP_PORT", "587")), timeout=60) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)
    return True


def last_success():
    try:
        return dt.datetime.fromisoformat(json.loads(STATUS.read_text())["finished"])
    except (OSError, ValueError, KeyError):
        return None


def run_and_alert():
    try:
        run_backup()
    except Exception as exc:  # noqa: BLE001 - every failure must reach a person
        log(f"BACKUP FAILED: {exc}")
        traceback.print_exc()
        last = last_success()
        body = (f"Last night's backup of {os.environ.get('SITE_NAME', 'LGV Manager')} failed.\n\n"
                f"Error: {exc}\n\nLast successful backup: {last:%d %b %Y %H:%M}" if last else
                f"Last night's backup failed.\n\nError: {exc}\n\nNo successful backup yet.")
        body += ("\n\nOn the server: cd ~/lgv-manager && sudo docker compose logs --tail 50 backup"
                 "\nIt is tried again tomorrow night; to retry now: sudo docker compose exec backup lgv-backup run")
        try:
            send_email("Backup FAILED - LGV Manager", body)
        except Exception as mail_exc:  # noqa: BLE001
            log(f"Could not send the alert email either: {mail_exc}")


def schedule():
    at = dt.time.fromisoformat(os.environ.get("BACKUP_TIME", "02:30"))
    log(f"Backups scheduled every day at {at:%H:%M} to {REMOTE}")
    while True:
        now = dt.datetime.now()
        target = dt.datetime.combine(now.date(), at)
        if target <= now:
            target += dt.timedelta(days=1)
        time.sleep(max(1, (target - dt.datetime.now()).total_seconds()))
        run_and_alert()
        time.sleep(61)


# --- Check, list, restore -----------------------------------------------------------------------

def check():
    ok = True
    print("Public key:", RECIPIENT if RECIPIENT.startswith("age1") else "MISSING (BACKUP_AGE_RECIPIENT)")
    ok &= RECIPIENT.startswith("age1")
    try:
        sh("pg_isready", "-q", env=pg_env())
        print("Database: OK")
    except BackupError as exc:
        print("Database:", exc)
        ok = False
    try:
        archives = daily_archives()
        print(f"Remote {REMOTE}: OK, {len(archives)} archive(s)")
    except BackupError as exc:
        print(f"Remote {REMOTE}:", exc)
        ok = False
    try:
        sent = send_email("Backup test - LGV Manager", "This is a test: backup alerts reach this address.")
        print("Alert email:", "test sent to " + os.environ.get("BACKUP_ALERT_EMAIL", "") if sent else "not configured")
        ok &= sent
    except Exception as exc:  # noqa: BLE001
        print("Alert email:", exc)
        ok = False
    last = last_success()
    print("Last successful backup:", f"{last:%d %b %Y %H:%M}" if last else "none yet")
    return ok


def list_backups():
    for d, name in sorted(daily_archives().items(), reverse=True):
        print(d.isoformat(), name)


def _read_key():
    key = os.environ.get("BACKUP_SECRET_KEY") or getpass.getpass("Paste the backup secret key (AGE-SECRET-KEY-...): ")
    key = key.strip()
    if not key.startswith("AGE-SECRET-KEY-"):
        raise BackupError("That is not an age secret key (it starts with AGE-SECRET-KEY-).")
    return key


def restore(which="latest", assume_yes=False):
    archives = daily_archives()
    if not archives:
        raise BackupError(f"No backup found on {REMOTE}.")
    day = max(archives) if which == "latest" else dt.date.fromisoformat(which)
    if day not in archives:
        raise BackupError(f"No backup for {day}. Available: {', '.join(str(d) for d in sorted(archives))}")
    print(f"Restoring the backup of {day}: the database is REPLACED and missing documents are added.")
    print("Stop the app first: sudo docker compose stop app worker")
    if not assume_yes and input("Type RESTORE to continue: ").strip() != "RESTORE":
        raise BackupError("Cancelled.")
    key = _read_key()
    with tempfile.TemporaryDirectory(dir=CONFIG) as tmp:
        tmp = Path(tmp)
        key_file = tmp / "key"
        key_file.touch(mode=0o600)
        key_file.write_text(key + "\n")
        try:
            sh("rclone", "copyto", f"{REMOTE}/daily/{archives[day]}", str(tmp / "archive.tar.age"))
            sh("age", "-d", "-i", str(key_file), "-o", str(tmp / "archive.tar"), str(tmp / "archive.tar.age"))
            with tarfile.open(tmp / "archive.tar") as tar:
                tar.extractall(tmp / "x", filter="data")
            manifest = json.loads((tmp / "x" / "manifest.json").read_text())["documents"]

            log("Restoring the database")
            sh("pg_restore", "--clean", "--if-exists", "--no-owner", "--single-transaction", "--exit-on-error",
               "--dbname", pg_env()["PGDATABASE"], str(tmp / "x" / "database.dump"), env=pg_env())

            restored = 0
            for rel, digest in manifest.items():
                target = MEDIA / rel
                if target.is_file() and _sha256(target) == digest:
                    continue
                enc = tmp / "doc.age"
                sh("rclone", "copyto", f"{REMOTE}/documents/{digest}.age", str(enc))
                target.parent.mkdir(parents=True, exist_ok=True)
                sh("age", "-d", "-i", str(key_file), "-o", str(target), str(enc))
                restored += 1
            log(f"Database restored; {restored} document(s) written, {len(manifest) - restored} already present")
            if (tmp / "x" / "env").is_file():
                saved = CONFIG / f"env-from-backup-{day}"
                saved.touch(mode=0o600)
                saved.write_bytes((tmp / "x" / "env").read_bytes())
                log(f"The .env of that day is in data/backup/{saved.name} (compare it with yours, then delete it)")
        finally:
            key_file.unlink(missing_ok=True)
    print("Done. Start the app again: sudo docker compose start app worker")


def main(argv):
    CONFIG.mkdir(parents=True, exist_ok=True)
    command = argv[1] if len(argv) > 1 else "schedule"
    try:
        if command == "schedule":
            schedule()
        elif command == "run":
            run_backup()
        elif command == "check":
            return 0 if check() else 1
        elif command == "list":
            list_backups()
        elif command == "restore":
            args = [a for a in argv[2:] if a != "--yes"]
            restore(args[0] if args else "latest", assume_yes="--yes" in argv)
        else:
            print(__doc__)
            return 2
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
