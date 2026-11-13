"""Reading emails: parsing .eml files and fetching new mail over IMAP."""

import hashlib
import html
import imaplib
import logging
import re
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.models import AiJob, InboundEmail

log = logging.getLogger(__name__)
SMALL_INLINE_IMAGE = 30 * 1024  # logos and signature images


def _html_to_text(value):
    value = re.sub(r"(?is)<(script|style).*?</\1>", "", value)
    value = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", value)
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def _body_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    text = part.get_content()
    return _html_to_text(text) if part.get_content_type() == "text/html" else text


def _collect(msg, body_parts, attachments, depth=0):
    body_parts.append(_body_text(msg))
    for part in msg.iter_attachments():
        mime = part.get_content_type()
        if mime == "message/rfc822" and depth < 3:
            inner = part.get_content()  # forwarded as attachment
            body_parts.append(f"\n--- Forwarded message: {inner.get('subject', '')} from {inner.get('from', '')} ---")
            _collect(inner, body_parts, attachments, depth + 1)
            continue
        data = part.get_payload(decode=True) or b""
        if mime.startswith("image/") and len(data) < SMALL_INLINE_IMAGE:
            continue
        attachments.append({"filename": part.get_filename() or f"attachment-{len(attachments) + 1}",
                            "mime_type": mime, "data": data})


def parse_eml(raw):
    """Subject, sender, date, body text and attachments of a raw email."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    addresses = getaddresses([str(msg.get("from", ""))])
    sender = (addresses[0][1] if addresses else "").lower()
    try:
        sent_at = parsedate_to_datetime(msg["date"]) if msg["date"] else None
    except (TypeError, ValueError):
        sent_at = None
    body_parts, attachments = [], []
    _collect(msg, body_parts, attachments)
    message_id = (msg.get("message-id") or "").strip() or f"<sha256-{hashlib.sha256(raw).hexdigest()}>"
    return {"message_id": message_id, "sender": sender, "subject": str(msg.get("subject", "")).strip(),
            "sent_at": sent_at, "body": "\n".join(p for p in body_parts if p).strip(), "attachments": attachments}


def sender_allowed(address):
    """Exact address or @domain listed in MAIL_ALLOWED_SENDERS. An empty list allows nobody."""
    address = (address or "").lower()
    domain = "@" + address.rsplit("@", 1)[-1] if "@" in address else None
    return any(entry == address or (entry.startswith("@") and entry == domain)
               for entry in settings.MAIL_ALLOWED_SENDERS)


def ingest_raw(raw, uploaded_by=None):
    """Store an email and queue its analysis. Returns the InboundEmail (existing one if already received).

    Emails from the mailbox are only analysed when the sender is allowed; an email uploaded
    by a logged-in user is trusted.
    """
    info = parse_eml(raw)
    existing = InboundEmail.objects.filter(message_id=info["message_id"]).first()
    if existing:
        return existing
    with transaction.atomic():
        inbound = InboundEmail(message_id=info["message_id"], sender=info["sender"] or "unknown",
                               subject=info["subject"][:500], received_at=timezone.now())
        inbound.raw.save(f"{hashlib.sha256(raw).hexdigest()[:16]}.eml", ContentFile(raw), save=False)
        if uploaded_by is None and not sender_allowed(info["sender"]):
            inbound.status = InboundEmail.Status.REJECTED
            inbound.note = "The sender is not in MAIL_ALLOWED_SENDERS; the email was not analysed."
        elif not settings.AI_ENABLED:
            inbound.status = InboundEmail.Status.FAILED
            inbound.note = "Claude is not configured (ANTHROPIC_API_KEY missing)."
        try:
            with transaction.atomic():
                inbound.save()
        except IntegrityError:  # received twice at the same time
            return InboundEmail.objects.get(message_id=info["message_id"])
        if inbound.status == InboundEmail.Status.RECEIVED:
            AiJob.objects.create(kind=AiJob.Kind.EMAIL, inbound_email=inbound, requested_by=uploaded_by)
    return inbound


def mailbox_configured():
    return bool(settings.MAIL_IMAP_HOST and settings.MAIL_IMAP_USER and settings.MAIL_IMAP_PASSWORD)


def poll_mailbox():
    """Fetch unread emails from the intake mailbox. Returns how many were stored."""
    if not mailbox_configured():
        return 0
    count = 0
    imap = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
    try:
        imap.login(settings.MAIL_IMAP_USER, settings.MAIL_IMAP_PASSWORD)
        imap.select(settings.MAIL_IMAP_FOLDER)
        _status, data = imap.search(None, "UNSEEN")
        for number in (data[0] or b"").split():
            _status, parts = imap.fetch(number, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
            if raw:
                try:
                    ingest_raw(raw)
                    count += 1
                except Exception:  # keep going with the other emails; this one stays unread
                    log.exception("Could not store email %s", number)
                    continue
            imap.store(number, "+FLAGS", "\\Seen")
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - connection already gone
            pass
    return count
