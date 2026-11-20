"""Document folders, text extraction and the Documents page."""

import io
from datetime import date

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase

from core.models import Company, Document, DocumentFolder
from core.roles import assign_role
from core.services.doc_text import extract_text

from .test_auth import verified_client


def tiny_pdf(text):
    """A one-page PDF whose text can be read back."""
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objects) + 1, xref)


def tiny_docx(text):
    from docx import Document as Word

    doc = Word()
    doc.add_paragraph(text)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text, table.rows[0].cells[1].text = "Cash", "1'200'000"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def make_document(company, name, data, **kwargs):
    doc = Document(company=company, **kwargs)
    doc.file = ContentFile(data, name=name)
    doc.save()
    return doc


class TextExtractionTests(TestCase):
    def test_pdf_docx_and_text(self):
        self.assertIn("Annual general meeting", extract_text(tiny_pdf("Annual general meeting 2026"), "a.pdf"))
        text = extract_text(tiny_docx("Board minutes of March"), "b.docx")
        self.assertIn("Board minutes of March", text)
        self.assertIn("Cash | 1'200'000", text)
        self.assertEqual(extract_text(b"plain  words\nhere", "c.txt"), "plain words here")

    def test_unreadable_files_give_nothing(self):
        self.assertIsNone(extract_text(b"%PDF-broken", "a.pdf"))
        self.assertIsNone(extract_text(b"\x89PNG....", "logo.png"))
        self.assertIsNone(extract_text(b"", "a.pdf"))

    def test_upload_fills_the_searchable_text(self):
        co = Company.objects.create(name="NeuroPath SA")
        doc = make_document(co, "report.pdf", tiny_pdf("Quarterly report cash runway"))
        self.assertIn("cash runway", doc.extracted_text)
        broken = make_document(co, "scan.pdf", b"%PDF-1.4 not really")
        self.assertIsNone(broken.extracted_text)        # still saved


class DocumentsPageTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.admin)
        self.field = Company.objects.create(name="Field Medical")
        self.neuro = Company.objects.create(name="NeuroPath SA")
        self.folder = DocumentFolder.objects.create(company=self.field, name="Series B")
        self.spa = make_document(self.field, "spa.pdf", tiny_pdf("Series B Preferred Stock Purchase Agreement"),
                                 title="Stock purchase agreement", category="subscription", folder=self.folder,
                                 document_date=date(2025, 6, 20))
        self.charter = make_document(self.field, "coi.docx", tiny_docx("Liquidation preference non participating"),
                                     title="Certificate of incorporation", category="charter", folder=self.folder,
                                     document_date=date(2025, 6, 20))
        self.report = make_document(self.neuro, "q1.pdf", tiny_pdf("Quarterly report cash runway 14 months"),
                                    title="Q1 report", category="investor_report", document_date=date(2026, 4, 30))

    def titles(self, **params):
        page = self.client.get("/documents/", params)
        self.assertEqual(page.status_code, 200)
        return [d.title for d in page.context["page"]]

    def test_search_inside_the_files(self):
        self.assertEqual(self.titles(q="runway"), ["Q1 report"])
        self.assertEqual(self.titles(q="liquidation preference"), ["Certificate of incorporation"])
        self.assertEqual(self.titles(q="purchase agreement"), ["Stock purchase agreement"])
        self.assertEqual(self.titles(q="nothing like this"), [])
        page = self.client.get("/documents/", {"q": "runway"})
        self.assertContains(page, "<mark>runway</mark>", html=False)

    def test_search_finds_title_and_file_name_even_without_text(self):
        make_document(self.neuro, "scanned-board-pack.pdf", b"%PDF-1.4 image only", title="Scan 7")
        self.assertEqual(self.titles(q="scanned-board"), ["Scan 7"])

    def test_filters(self):
        self.assertEqual(sorted(self.titles(company=self.field.pk)),
                         ["Certificate of incorporation", "Stock purchase agreement"])
        self.assertEqual(self.titles(category="investor_report"), ["Q1 report"])
        self.assertEqual(sorted(self.titles(folder=self.folder.pk)),
                         ["Certificate of incorporation", "Stock purchase agreement"])
        self.assertEqual(self.titles(year=2026), ["Q1 report"])
        self.assertEqual(self.titles(company=self.neuro.pk, q="runway", year=2026), ["Q1 report"])
        self.assertEqual(self.titles(company=self.field.pk, q="runway"), [])
        self.assertEqual(len(self.titles()), 3)
        self.assertEqual(len(self.titles(company="abc", year="x")), 3)       # bad input is ignored

    def test_snippet_is_escaped(self):
        make_document(self.neuro, "x.txt", b"Look <script>alert(1)</script> runway here", title="Hostile")
        page = self.client.get("/documents/", {"q": "runway"})
        self.assertNotContains(page, "<script>alert")          # stripped by the database or escaped by us
        self.assertContains(page, "<mark>runway</mark>")

    def test_company_card_groups_documents_by_folder(self):
        page = self.client.get(f"/companies/{self.field.pk}/")
        self.assertContains(page, "📁 Series B")
        self.assertContains(page, "Stock purchase agreement")
        self.assertContains(page, f"/documents/?company={self.field.pk}")
        loose = make_document(self.field, "loose.txt", b"x", title="Loose note")
        self.assertContains(self.client.get(f"/companies/{self.field.pk}/"), "Loose note")
        self.assertEqual(loose.folder, None)

    def test_folder_deleted_keeps_documents(self):
        self.folder.delete()
        self.spa.refresh_from_db()
        self.assertIsNone(self.spa.folder)

    def test_menu_and_permissions(self):
        self.assertContains(self.client.get("/"), 'href="/documents/"')
        outsider = User.objects.create_user("o", "o@example.com", "a-long-test-password")
        verified_client(self.client, outsider)
        self.assertEqual(self.client.get("/documents/").status_code, 403)
        assign_role(outsider, "viewer")
        verified_client(self.client, outsider)
        self.assertEqual(self.client.get("/documents/").status_code, 200)


def tiny_xlsx():
    import io

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Summary Cap Table"
    sheet.append(["Class", "Shares", "Price"])
    sheet.append(["Series B-1 Preferred", 17855941, 1.7788])
    sheet.append([])
    sheet.append(["Date", None, None])
    book.create_sheet("Investors").append(["Efficient Frontier Holding SA", 337307, 600000])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


class SpreadsheetTests(TestCase):
    def test_excel_text_is_extracted(self):
        text = extract_text(tiny_xlsx(), "cap.xlsx")
        self.assertIn("== Sheet: Summary Cap Table ==", text)
        self.assertIn("Series B-1 Preferred 17855941 1.7788", text.replace(" | ", " "))
        self.assertIn("Efficient Frontier Holding SA | 337307 | 600000", extract_text(tiny_xlsx(), "cap.xlsx", compact=False))
        self.assertIsNone(extract_text(b"not a workbook", "broken.xlsx"))

    def test_excel_is_searchable_and_readable_for_the_cap_table(self):
        from core.ai import claude

        co = Company.objects.create(name="AdvNanoT")
        doc = make_document(co, "cap.xlsx", tiny_xlsx(), title="Pro forma cap table")
        self.assertIn("17855941", doc.extracted_text)
        blocks, notes = claude._document_blocks([{"title": doc.title, "filename": "cap.xlsx",
                                                  "mime_type": doc.mime_type, "data": tiny_xlsx()}])
        self.assertEqual(notes, [])
        self.assertIn("Series B-1 Preferred | 17855941 | 1.7788", blocks[0]["text"])
        mail_blocks, mail_notes = claude._attachment_blocks([{"filename": "cap.xlsx", "mime_type": doc.mime_type,
                                                              "data": tiny_xlsx()}])
        self.assertEqual(mail_notes, [])
        self.assertIn("17855941", mail_blocks[0]["source"]["data"])

    def test_unreadable_type_is_named_in_the_message(self):
        from core.ai import claude

        with self.assertRaisesMessage(claude.AiError, "(.zip:"):
            from unittest import mock

            with mock.patch.object(claude, "get_client"):
                claude.read_cap_table("X", [], [{"title": "Archive", "filename": "a.zip",
                                                 "mime_type": "application/zip", "data": b"PK"}])
