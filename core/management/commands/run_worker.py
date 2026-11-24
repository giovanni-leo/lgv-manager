"""Background worker: runs Claude jobs and reads the intake mailbox.

Runs as its own container (see docker-compose.yml). `--once` processes what is
waiting and exits (useful for tests or a cron job).
"""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.db.migrations.executor import MigrationExecutor

from core.ai import jobs, mail
from core.services import ecb

log = logging.getLogger(__name__)
FX_EVERY_SECONDS = 6 * 3600


class Command(BaseCommand):
    help = "Run queued Claude jobs and poll the intake mailbox."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process what is waiting, then exit.")

    def handle(self, *args, once=False, **options):
        self.running = True
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        self._wait_for_migrations()
        stale = jobs.fail_stale_jobs()
        if stale:
            log.warning("Marked %s interrupted job(s) as failed", stale)
        last_poll = 0.0
        last_fx = -FX_EVERY_SECONDS            # exchange rates: once at start, then every few hours
        self.stdout.write("Worker started" + (" (mailbox: %s)" % settings.MAIL_IMAP_USER
                                              if mail.mailbox_configured() else " (no mailbox configured)"))
        while self.running:
            if not connection.in_atomic_block:  # drop broken or expired connections between jobs
                close_old_connections()
            if mail.mailbox_configured() and (once or time.monotonic() - last_poll >= settings.MAIL_POLL_SECONDS):
                last_poll = time.monotonic()
                try:
                    count = mail.poll_mailbox()
                    if count:
                        log.info("Stored %s new email(s)", count)
                except Exception:  # mailbox down: try again at the next poll
                    log.exception("Could not read the mailbox")
            if not once and time.monotonic() - last_fx >= FX_EVERY_SECONDS:
                last_fx = time.monotonic()
                try:
                    added = ecb.update()
                    if added:
                        log.info("Added %s exchange rate(s) from the ECB", added)
                except Exception:  # offline or ECB down: try again later
                    log.warning("Could not load the ECB exchange rates", exc_info=True)
            job = jobs.claim_next_job()
            if job:
                log.info("Running %s", job)
                jobs.run_job(job)
                continue
            if once:
                break
            time.sleep(2)

    def _wait_for_migrations(self):
        """The app container applies migrations at start; wait for it instead of failing."""
        while self.running:
            executor = MigrationExecutor(connection)
            if not executor.migration_plan(executor.loader.graph.leaf_nodes()):
                return
            self.stdout.write("Waiting for database migrations…")
            time.sleep(3)

    def _stop(self, *_args):
        self.running = False
