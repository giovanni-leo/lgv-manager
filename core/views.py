from functools import lru_cache
from pathlib import Path

import markdown
from django.conf import settings
from django.contrib.auth.decorators import permission_required
from django.db import connection
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.safestring import mark_safe

from .models import Document
from .version import version_string


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok", "version": version_string()})


MANUAL_PATH = Path(settings.BASE_DIR) / "docs" / "USER_GUIDE.md"


@lru_cache(maxsize=4)
def _manual_html(mtime):
    # The guide is our own file in the repository, so its HTML is trusted.
    md = markdown.Markdown(extensions=["tables", "toc", "sane_lists"])
    return mark_safe(md.convert(MANUAL_PATH.read_text(encoding="utf-8"))), mark_safe(md.toc)


def manual(request):
    """The user guide (docs/USER_GUIDE.md) as an app page."""
    body, toc = _manual_html(MANUAL_PATH.stat().st_mtime)
    return render(request, "app/manual.html", {"body": body, "toc": toc})


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@permission_required("core.view_document", raise_exception=True)
def document_file(request, path):
    """Serve an uploaded file to a logged-in user (never directly by the web server).

    Only paths recorded on a document are served, so nothing else on disk can be reached.
    """
    document = get_object_or_404(Document, file=path)
    inline = (document.mime_type or "").startswith(("application/pdf", "image/"))
    response = FileResponse(document.file.open("rb"), as_attachment=not inline,
                            filename=document.original_filename, content_type=document.mime_type or None)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
