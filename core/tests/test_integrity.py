"""Database constraints, referential actions, stage history and documents."""

import hashlib
from datetime import date
from decimal import Decimal

from django.contrib.postgres.search import SearchQuery
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from core.models import (Contact, Deal, DealStageHistory, Document, Investment, ShareClass, ShareEvent)

from . import factories as f

D = date


def check_deferred():
    """Deferred constraints are only checked at commit: force the check inside a test."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class ConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fund = f.entity()
        cls.co = f.company("Alpha")
        cls.other = f.company("Beta")
        cls.common = f.share_class(cls.co, "Common", common=True)
        cls.other_common = f.share_class(cls.other, "Common", common=True)

    def test_equity_requires_shares_and_class(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Investment.objects.create(company=self.co, entity=self.fund, invest_date=D(2024, 1, 1),
                                      instrument="equity", amount=Decimal("1000"))

    def test_convertible_without_shares_is_allowed(self):
        Investment.objects.create(company=self.co, entity=self.fund, invest_date=D(2024, 1, 1),
                                  instrument="convertible_loan", amount=Decimal("100000"))

    def test_share_class_must_belong_to_same_company(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Investment.objects.create(company=self.co, entity=self.fund, share_class=self.other_common,
                                      invest_date=D(2024, 1, 1), instrument="equity", shares=10,
                                      amount=Decimal("10"))
            check_deferred()

    def test_model_validation_reports_foreign_share_class(self):
        inv = Investment(company=self.co, entity=self.fund, share_class=self.other_common,
                         invest_date=D(2024, 1, 1), instrument="equity", shares=10, amount=Decimal("10"))
        with self.assertRaises(ValidationError) as ctx:
            inv.full_clean()
        self.assertIn("share_class", ctx.exception.message_dict)

    def test_event_sign_must_match_type(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.issue(self.common, -100, D(2024, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.issue(self.common, 100, D(2024, 1, 1), "buyback")

    def test_split_requires_ratio(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareEvent.objects.create(company=self.co, share_class=self.common, event_date=D(2024, 1, 1),
                                      event_type="split", shares_delta=0)

    def test_class_name_unique_per_company(self):
        f.share_class(self.other, "Series A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            f.share_class(self.other, "Series A")

    def test_currency_must_be_iso_code(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareClass.objects.create(company=self.co, name="X", currency="chf")

    def test_db_defaults_work_for_plain_sql_inserts(self):
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO companies (name) VALUES ('SQL Co') RETURNING status, created_at")
            status, created_at = cursor.fetchone()
            self.assertEqual(status, "prospect")
            self.assertIsNotNone(created_at)
            cursor.execute("INSERT INTO deals (company_id) SELECT id FROM companies WHERE name = 'SQL Co' "
                           "RETURNING stage, currency, opened_on")
            self.assertEqual(cursor.fetchone()[:2], ("sourced", "CHF"))


class ReferentialActionTests(TestCase):
    """ON DELETE rules of the reference schema hold for plain SQL deletes too."""

    def test_sql_delete_of_company_cascades_and_sets_null(self):
        fund = f.entity()
        co = f.company()
        common = f.share_class(co, "Common", common=True)
        f.issue(common, 1_000, D(2024, 1, 1))
        f.buy(fund, common, 100, D(2024, 1, 1))
        contact = Contact.objects.create(full_name="Jane Doe", company=co)
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM companies WHERE id = %s", [co.pk])
            check_deferred()
            for table in ("share_classes", "share_events", "investments"):
                cursor.execute(f"SELECT count(*) FROM {table}")
                self.assertEqual(cursor.fetchone()[0], 0, table)
        contact.refresh_from_db()
        self.assertIsNone(contact.company_id)

    def test_sql_delete_of_entity_with_investments_is_refused(self):
        fund = f.entity()
        common = f.share_class(f.company(), "Common", common=True)
        f.buy(fund, common, 100, D(2024, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM investing_entities WHERE id = %s", [fund.pk])
            check_deferred()

    def test_orm_delete_of_company_works(self):
        fund = f.entity()
        co = f.company()
        common = f.share_class(co, "Common", common=True)
        f.issue(common, 1_000, D(2024, 1, 1))
        f.buy(fund, common, 100, D(2024, 1, 1))
        co.delete()
        check_deferred()
        self.assertFalse(ShareClass.objects.exists())


class DealStageHistoryTests(TestCase):
    def test_every_stage_change_is_logged(self):
        deal = Deal.objects.create(company=f.company())
        deal.stage = "first_meeting"
        deal.save()
        deal.next_step = "Send NDA"
        deal.save()  # no stage change, no new row
        deal = Deal.objects.get(pk=deal.pk)
        deal.stage = "analysis"
        deal.save()
        history = list(DealStageHistory.objects.filter(deal=deal).values_list("from_stage", "to_stage"))
        self.assertEqual(history, [(None, "sourced"), ("sourced", "first_meeting"),
                                   ("first_meeting", "analysis")])

    def test_invalid_stage_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Deal.objects.create(company=f.company(), stage="won")


class DocumentTests(TestCase):
    def upload(self, content=b"%PDF-1.4 test", name="deck.pdf"):
        return SimpleUploadedFile(name, content, content_type="application/pdf")

    def test_metadata_and_hash_are_computed(self):
        co = f.company()
        doc = Document.objects.create(company=co, title="Deck", file=self.upload())
        self.assertEqual(doc.sha256, hashlib.sha256(b"%PDF-1.4 test").hexdigest())
        self.assertEqual(doc.size_bytes, 13)
        self.assertEqual(doc.mime_type, "application/pdf")
        self.assertEqual(doc.original_filename, "deck.pdf")
        self.assertTrue(doc.file.name.startswith("documents/"))
        self.assertNotIn("deck", doc.file.name)  # opaque storage key

    def test_duplicate_file_rejected_per_company(self):
        co = f.company()
        Document.objects.create(company=co, title="A", file=self.upload())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(company=co, title="B", file=self.upload())
        Document.objects.create(company=f.company("Other"), title="C", file=self.upload())

    def test_duplicate_rejected_for_deal_only_documents(self):
        deal = Deal.objects.create(company=f.company())
        Document.objects.create(deal=deal, title="A", file=self.upload())
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(deal=deal, title="B", file=self.upload())

    def test_document_needs_company_or_deal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Document.objects.create(title="Orphan", file=self.upload())

    def test_full_text_search_vector(self):
        co = f.company()
        doc = Document.objects.create(company=co, title="Term sheet Serie A", file=self.upload())
        Document.objects.filter(pk=doc.pk).update(extracted_text="Liquidation preference 1x non participating")
        self.assertEqual(Document.objects.filter(search=SearchQuery("preference", config="simple")).count(), 1)
        self.assertEqual(Document.objects.filter(search=SearchQuery("serie", config="simple")).count(), 1)
        self.assertEqual(Document.objects.filter(search=SearchQuery("dividend", config="simple")).count(), 0)


class AuditLogTests(TestCase):
    def test_changes_to_main_tables_are_logged(self):
        from auditlog.models import LogEntry

        co = f.company()
        co.status = "exited"
        co.save()
        entries = LogEntry.objects.get_for_object(co).order_by("timestamp")
        self.assertEqual([e.action for e in entries], [LogEntry.Action.CREATE, LogEntry.Action.UPDATE])
        self.assertEqual(entries.last().changes_dict["status"], ["portfolio", "exited"])


class SpvInvestmentTests(TestCase):
    """An interest in an SPV has no shares or share class of its own."""

    def test_spv_interest_needs_no_shares_and_equity_still_does(self):
        from datetime import date
        from decimal import Decimal

        from django.core.exceptions import ValidationError
        from django.db import IntegrityError, transaction

        from core.models import Company, InvestingEntity, Investment

        co = Company.objects.create(name="Laza Medical")
        entity = InvestingEntity.objects.create(name="Efficient Frontier Holding SA", kind="company")
        spv = Investment(company=co, entity=entity, invest_date=date(2026, 11, 4), instrument="spv",
                         amount=Decimal("250000"), currency="USD")
        spv.full_clean()
        spv.save()
        equity = Investment(company=co, entity=entity, invest_date=date(2026, 11, 4), instrument="equity",
                            amount=Decimal("1"), currency="USD")
        with self.assertRaises(ValidationError):
            equity.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            equity.save()
