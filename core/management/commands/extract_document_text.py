"""Read the text of documents uploaded before text extraction existed, so they become searchable."""

from django.core.management.base import BaseCommand

from core.models import Document
from core.services.doc_text import extract_text


class Command(BaseCommand):
    help = "Fill the searchable text of documents that have none (PDF and Word files)."

    def handle(self, *args, **options):
        done = skipped = 0
        for doc in Document.objects.filter(extracted_text__isnull=True).iterator():
            try:
                with doc.file.open("rb") as fh:
                    text = extract_text(fh.read(), doc.original_filename, doc.mime_type)
            except OSError:
                self.stderr.write(f"File missing: {doc.title}")
                skipped += 1
                continue
            if text:
                # update(): no need to touch anything else, and no new audit entry for a technical field
                Document.objects.filter(pk=doc.pk).update(extracted_text=text)
                done += 1
            else:
                skipped += 1
        self.stdout.write(self.style.SUCCESS(f"{done} document(s) now searchable, {skipped} without readable text."))
