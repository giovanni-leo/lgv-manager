from django.contrib.auth.decorators import permission_required
from django.db import connection
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404

from .models import Document


def healthz(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})


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
