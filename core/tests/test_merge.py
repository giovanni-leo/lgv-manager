"""Merging a company that appears twice."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core.ai import claude, jobs, mail
from core.models import (AiJob, CoInvestor, Company, CompanyMetric, Contact, Deal, Document, DocumentFolder,
                         FundingRound, FundingRoundInvestor, Interaction, Investment, OptionPool, ShareClass,
                         ShareEvent, Suggestion, Valuation)
from core.roles import assign_role
from core.services.merge import MergeError, check, counts, merge_companies

from .factories import entity
from .test_ai import AI_ON, email_result, make_eml
from .test_auth import verified_client
from .test_documents import make_document, tiny_pdf


class MergeServiceTests(TestCase):
    def setUp(self):
        self.keep = Company.objects.create(name="Advanced NanoTherapies, Inc", sector="medical devices")
        self.dup = Company.objects.create(name="AdvNanoT", website="https://advnano.example", city="Cleveland",
                                          description="Nano drug delivery.")

    def test_everything_moves_and_the_duplicate_disappears(self):
        deal = Deal.objects.create(company=self.dup, round_name="Series B")
        Contact.objects.create(company=self.dup, full_name="Ana Smith", role="CEO")
        Interaction.objects.create(company=self.dup, kind="note", occurred_on=date(2026, 5, 1), summary="Call")
        doc = make_document(self.dup, "deck.pdf", tiny_pdf("deck"), title="Deck")
        Suggestion.objects.create(job=AiJob.objects.create(kind="email", company=self.dup), company=self.dup,
                                  target="company_field", payload={"field": "city", "value": "x"}, label="x")
        report = merge_companies(self.keep, self.dup)
        self.assertFalse(Company.objects.filter(pk=self.dup.pk).exists())
        for model in (Deal, Contact, Interaction, Document, Suggestion, AiJob):
            self.assertEqual(model.objects.get().company, self.keep, model.__name__)
        self.assertEqual(Deal.objects.get().pk, deal.pk)
        self.assertEqual(Document.objects.get().pk, doc.pk)
        self.assertEqual(report.moved["Deals"], 1)

    def test_empty_details_are_filled_and_the_name_is_remembered(self):
        merge_companies(self.keep, self.dup)
        self.keep.refresh_from_db()
        self.assertEqual((self.keep.website, self.keep.city, self.keep.description, self.keep.sector),
                         ("https://advnano.example", "Cleveland", "Nano drug delivery.", "medical devices"))
        self.assertEqual(self.keep.aliases, "AdvNanoT")
        self.assertEqual(self.keep.name, "Advanced NanoTherapies, Inc")        # the kept company's own values win

    def test_status_follows_the_more_advanced_one(self):
        Company.objects.filter(pk=self.dup.pk).update(status="portfolio")
        merge_companies(self.keep, Company.objects.get(pk=self.dup.pk))
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.status, "portfolio")

    def test_identical_documents_are_kept_once_and_folders_combine(self):
        a = make_document(self.keep, "a.pdf", tiny_pdf("same"), title="Term sheet")
        folder_keep = DocumentFolder.objects.create(company=self.keep, name="Series B")
        folder_dup = DocumentFolder.objects.create(company=self.dup, name="series b")
        make_document(self.dup, "b.pdf", tiny_pdf("same"), title="Term sheet copy", folder=folder_dup)
        extra = make_document(self.dup, "c.pdf", tiny_pdf("other"), title="Other", folder=folder_dup)
        report = merge_companies(self.keep, self.dup)
        self.assertEqual(Document.objects.count(), 2)
        self.assertIn("already there", report.dropped[0])
        extra.refresh_from_db()
        self.assertEqual((extra.company, extra.folder), (self.keep, folder_keep))
        self.assertEqual(DocumentFolder.objects.get().pk, folder_keep.pk)
        self.assertTrue(Document.objects.filter(pk=a.pk).exists())

    def test_same_round_found_twice_becomes_one(self):
        vc, other = CoInvestor.objects.create(name="Alpha VC", kind="vc"), CoInvestor.objects.create(name="Beta", kind="vc")
        r1 = FundingRound.objects.create(company=self.keep, round_name="Series B", announced_on=date(2026, 5, 1))
        FundingRoundInvestor.objects.create(funding_round=r1, co_investor=vc, is_lead=True)
        r2 = FundingRound.objects.create(company=self.dup, round_name="series b", announced_on=date(2026, 6, 9),
                                         amount=Decimal("30000000"))
        FundingRoundInvestor.objects.create(funding_round=r2, co_investor=other)
        FundingRound.objects.create(company=self.dup, round_name="Seed", announced_on=date(2024, 1, 1))
        merge_companies(self.keep, self.dup)
        self.assertEqual(self.keep.funding_rounds.count(), 2)
        b = self.keep.funding_rounds.get(round_name="Series B")
        self.assertEqual(b.amount, Decimal("30000000.00"))                      # completed from the duplicate
        self.assertEqual(sorted(x.co_investor.name for x in b.investors.all()), ["Alpha VC", "Beta"])

    def test_dates_that_clash_keep_the_kept_companys_row(self):
        CompanyMetric.objects.create(company=self.keep, as_of=date(2026, 3, 31), cash=Decimal(100), currency="USD")
        CompanyMetric.objects.create(company=self.dup, as_of=date(2026, 3, 31), cash=Decimal(999), currency="USD")
        CompanyMetric.objects.create(company=self.dup, as_of=date(2026, 6, 30), cash=Decimal(50), currency="USD")
        OptionPool.objects.create(company=self.dup, as_of=date(2026, 1, 1), authorized=10)
        merge_companies(self.keep, self.dup)
        self.assertEqual(sorted((m.as_of, m.cash) for m in self.keep.metrics.all()),
                         [(date(2026, 3, 31), Decimal("100.00")), (date(2026, 6, 30), Decimal("50.00"))])
        self.assertEqual(self.keep.option_pools.get().authorized, 10)

    def test_cap_table_of_the_duplicate_moves_when_the_kept_one_has_none(self):
        ef = entity("Efficient Frontier Holding SA", "company")
        cls = ShareClass.objects.create(company=self.dup, name="Series B-1 Preferred")
        ShareEvent.objects.create(company=self.dup, share_class=cls, event_type="issuance",
                                  event_date=date(2026, 6, 1), shares_delta=1000)
        Investment.objects.create(company=self.dup, entity=ef, share_class=cls, invest_date=date(2026, 6, 1),
                                  instrument="equity", shares=10, amount=Decimal(10), currency="USD")
        Valuation.objects.create(company=self.dup, valuation_date=date(2026, 6, 1), method="round",
                                 price_per_share=Decimal("1.5"), share_class=cls, currency="USD")
        merge_companies(self.keep, self.dup)
        self.assertEqual(ShareClass.objects.get().company, self.keep)
        self.assertEqual(ShareEvent.objects.get().company, self.keep)
        self.assertEqual(Investment.objects.get().company, self.keep)
        self.assertEqual(Valuation.objects.get().company, self.keep)

    def test_two_cap_tables_are_refused_and_nothing_changes(self):
        for co in (self.keep, self.dup):
            cls = ShareClass.objects.create(company=co, name="Common", is_common=True)
            ShareEvent.objects.create(company=co, share_class=cls, event_type="issuance",
                                      event_date=date(2026, 1, 1), shares_delta=5)
        Deal.objects.create(company=self.dup)
        with self.assertRaisesMessage(MergeError, "Both companies have a cap table"):
            merge_companies(self.keep, self.dup)
        self.assertTrue(Company.objects.filter(pk=self.dup.pk).exists())
        self.assertEqual(Deal.objects.get().company, self.dup)

    def test_same_company_is_refused(self):
        with self.assertRaises(MergeError):
            check(self.keep, self.keep)

    def test_counts(self):
        Deal.objects.create(company=self.dup)
        self.assertEqual(counts(self.dup), [("Deals", 1)])


@AI_ON
class AliasMatchingTests(TestCase):
    def test_merged_name_is_recognised_in_the_next_email(self):
        keep = Company.objects.create(name="CardioSense SA")
        dup = Company.objects.create(name="Cardio Sensing Medical Devices")
        merge_companies(keep, dup)
        self.assertEqual(jobs.match_company("Cardio Sensing Medical Devices", None), keep)
        # an email that names the company the old way no longer creates a second company
        mail.ingest_raw(make_eml())
        with mock.patch.object(claude, "extract_email",
                               return_value=(email_result("Cardio Sensing Medical Devices"), claude.Usage(), [])):
            jobs.run_job(jobs.claim_next_job())
        self.assertEqual(Company.objects.count(), 1)
        self.assertEqual(Deal.objects.get().company, keep)


class MergePageTests(TestCase):
    def setUp(self):
        self.keep = Company.objects.create(name="Advanced NanoTherapies, Inc")
        self.dup = Company.objects.create(name="AdvNanoT")
        Deal.objects.create(company=self.dup, round_name="Series B")
        self.admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.admin)
        self.url = f"/companies/{self.keep.pk}/merge/"

    def test_choose_preview_and_confirm(self):
        page = self.client.get(self.url)
        self.assertContains(page, "AdvNanoT")
        preview = self.client.get(self.url, {"source": self.dup.pk})
        self.assertContains(preview, "1 <span class=\"muted\">deals</span>", html=False)
        self.assertContains(preview, "This cannot be undone")
        self.assertTrue(Company.objects.filter(pk=self.dup.pk).exists())             # nothing yet
        response = self.client.post(self.url, {"source": self.dup.pk, "confirm": "1"}, follow=True)
        self.assertContains(response, "was merged into")
        self.assertFalse(Company.objects.filter(pk=self.dup.pk).exists())
        self.assertEqual(Deal.objects.get().company, self.keep)

    def test_get_never_merges(self):
        self.client.get(self.url, {"source": self.dup.pk, "confirm": "1"})
        self.assertTrue(Company.objects.filter(pk=self.dup.pk).exists())

    def test_cap_table_conflict_is_explained(self):
        for co in (self.keep, self.dup):
            cls = ShareClass.objects.create(company=co, name="Common", is_common=True)
            ShareEvent.objects.create(company=co, share_class=cls, event_type="issuance",
                                      event_date=date(2026, 1, 1), shares_delta=5)
        page = self.client.post(self.url, {"source": self.dup.pk, "confirm": "1"})
        self.assertContains(page, "Both companies have a cap table")
        self.assertTrue(Company.objects.filter(pk=self.dup.pk).exists())

    def test_button_and_permissions(self):
        self.assertContains(self.client.get(f"/companies/{self.keep.pk}/"), "Merge a duplicate")
        viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(viewer, "viewer")
        verified_client(self.client, viewer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(f"/companies/{self.keep.pk}/"), "Merge a duplicate")
