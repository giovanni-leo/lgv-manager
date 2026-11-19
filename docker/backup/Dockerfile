# Backup container: same PostgreSQL version as the database (pg_dump/pg_restore 16), plus
# age (encryption), rclone (Google Drive and other remotes) and Python for the script.
FROM postgres:16-alpine
RUN apk add --no-cache age rclone python3 tzdata
ENV RCLONE_CONFIG=/config/rclone.conf TZ=Europe/Zurich PYTHONUNBUFFERED=1
COPY docker/backup/lgv_backup.py /usr/local/bin/lgv-backup
RUN chmod 755 /usr/local/bin/lgv-backup
ENTRYPOINT []
CMD ["lgv-backup", "schedule"]
