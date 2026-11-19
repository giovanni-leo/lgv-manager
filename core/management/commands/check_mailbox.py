"""Check the intake mailbox settings without processing any email."""

import imaplib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.ai import mail


class Command(BaseCommand):
    help = "Log in to the intake mailbox and report how many unread emails are waiting."

    def handle(self, *args, **options):
        if not mail.mailbox_configured():
            raise CommandError("MAIL_IMAP_HOST, MAIL_IMAP_USER and MAIL_IMAP_PASSWORD must be set in .env.")
        try:
            imap = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT, timeout=30)
        except OSError as exc:
            raise CommandError(f"Cannot reach {settings.MAIL_IMAP_HOST}:{settings.MAIL_IMAP_PORT}: {exc}") from exc
        try:
            try:
                imap.login(settings.MAIL_IMAP_USER, settings.MAIL_IMAP_PASSWORD)
            except imaplib.IMAP4.error as exc:
                raise CommandError(f"Login refused for {settings.MAIL_IMAP_USER}: {exc}. For Gmail use an app "
                                   "password (16 letters, no spaces), not the account password.") from exc
            status, _data = imap.select(settings.MAIL_IMAP_FOLDER, readonly=True)
            if status != "OK":
                raise CommandError(f"Folder {settings.MAIL_IMAP_FOLDER!r} not found.")
            _status, data = imap.search(None, "UNSEEN")
            unread = len((data[0] or b"").split())
        finally:
            try:
                imap.logout()
            except Exception:  # noqa: BLE001
                pass
        self.stdout.write(self.style.SUCCESS(f"Mailbox OK: {settings.MAIL_IMAP_USER}, {unread} unread email(s)."))
        senders = ", ".join(settings.MAIL_ALLOWED_SENDERS) or "NOBODY - set MAIL_ALLOWED_SENDERS"
        self.stdout.write(f"Allowed senders: {senders}")
        self.stdout.write(f"Sender check by: {settings.MAIL_AUTH_SERVER or 'switched off'}")
        if not settings.AI_ENABLED:
            self.stdout.write(self.style.WARNING("ANTHROPIC_API_KEY is not set: emails will be stored but not read."))
