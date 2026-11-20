"""At-a-glance pages and the figures they show."""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from core.models import (CoInvestor, Company, CompanyMetric, Contact, Deal, DealStageHistory, FxRate, Interaction,
                         Investment, RoundParticipant, Valuation)
from core.roles import assign_role
from core.services.portfolio import company_summary, deal_summary, portfolio_totals, runway, xirr

from . import factories as f
from .test_auth import verified_client

D = date


class XirrTests(TestCase):
    def test_ten_percent_over_two_years(self):
        self.assertAlmostEqual(float(xirr([(D(2022, 1, 1), -100), (D(2024, 1, 1), 121)])), 0.10, places=3)

    def test_loss(self):
        self.assertAlmostEqual(float(xirr([(D(2022, 1, 1), -100), (D(2023, 1, 1), 50)])), -0.50, places=3)

    def test_needs_money_in_and_out(self):
        self.assertIsNone(xirr([(D(2022, 1, 1), -100)]))
        self.assertIsNone(xirr([]))


class CompanySummaryTests(TestCase):
    """Fund holds 50,000 Series A (ratio 1.5) of 800,000 common + 200,000 Series A."""

    @classmethod
    def setUpTestData(cls):
        cls.fund = f.entity()
        cls.co = f.company()
        cls.common = f.share_class(cls.co, "Common", "1", common=True)
        cls.series_a = f.share_class(cls.co, "Series A", "1.5", seniority=1)
        f.issue(cls.common, 800_000, D(2022, 1, 10))
        f.issue(cls.series_a, 200_000, D(2023, 3, 1))
        f.buy(cls.fund, cls.series_a, 50_000, D(2023, 3, 1), "150000")  # CHF 3.00 per Series A share

    def test_value_from_price_per_share_of_a_preferred_class(self):
        # Series B priced at 6.00 per Series A share = 4.00 per common equivalent.
        Valuation.objects.create(company=self.co, valuation_date=D(2025, 1, 1), method="round",
                                 price_per_share=Decimal("6"), share_class=self.series_a,
                                 post_money=Decimal("4400000"))
        s = company_summary(self.co, D(2025, 1, 2))
        self.assertEqual(s.stake_value, Decimal("300000"))           # 75,000 common equiv × 4.00
        self.assertEqual(s.company_value, Decimal("4400000"))
        self.assertEqual(s.invested_chf, Decimal("150000"))
        self.assertEqual(s.moic, Decimal("2.00"))
        self.assertAlmostEqual(float(s.irr), 2 ** (365 / (D(2025, 1, 2) - D(2023, 3, 1)).days) - 1, places=3)

    def test_value_from_post_money_and_fully_diluted_stake(self):
        Valuation.objects.create(company=self.co, valuation_date=D(2025, 1, 1), method="internal_fair_value",
                                 post_money=Decimal("11000000"))
        s = company_summary(self.co, D(2025, 1, 2))
        self.assertEqual(s.stake_value, Decimal("750002"))            # 11M × 6.8182%
        self.assertEqual(s.ownership.pct_fully_diluted, Decimal("6.8182"))

    def test_write_off_values_stake_at_zero(self):
        Valuation.objects.create(company=self.co, valuation_date=D(2025, 1, 1), method="write_off")
        s = company_summary(self.co, D(2025, 1, 2))
        self.assertEqual((s.stake_value_chf, s.moic), (Decimal(0), Decimal("0.00")))

    def test_amounts_in_other_currencies_are_converted_to_chf(self):
        euro = f.buy(self.fund, self.common, 10_000, D(2024, 1, 1), "100000")
        Investment.objects.filter(pk=euro.pk).update(currency="EUR")
        s = company_summary(self.co, D(2024, 6, 1))
        self.assertTrue(s.missing_fx)                                  # no EUR rate yet
        FxRate.objects.create(rate_date=D(2023, 12, 29), currency="EUR", chf_per_unit=Decimal("0.93"))
        s = company_summary(self.co, D(2024, 6, 1))
        self.assertFalse(s.missing_fx)
        self.assertEqual(s.invested_chf, Decimal("243000.00"))         # 150,000 + 100,000 × 0.93

    def test_realizations_count_in_the_multiple(self):
        f.sell(self.fund, self.series_a, 10_000, D(2024, 6, 1), "60000")
        Valuation.objects.create(company=self.co, valuation_date=D(2024, 6, 1), method="round",
                                 price_per_share=Decimal("4.5"), share_class=self.series_a)
        s = company_summary(self.co, D(2024, 6, 2))
        self.assertEqual(s.realized_chf, Decimal("60000"))
        self.assertEqual(s.stake_value, Decimal("180000"))             # 40,000 × 1.5 × 3.00
        self.assertEqual(s.moic, Decimal("1.60"))                      # (180k + 60k) / 150k
        totals = portfolio_totals([s], D(2024, 6, 2))
        self.assertEqual((totals.invested_chf, totals.value_chf, totals.moic),
                         (Decimal("150000"), Decimal("180000"), Decimal("1.60")))

    def test_runway_is_reduced_by_months_since_the_figures(self):
        today = timezone.localdate()
        CompanyMetric.objects.create(company=self.co, as_of=today - timedelta(days=91),
                                     cash=Decimal("1200000"), monthly_burn=Decimal("100000"))
        r = runway(self.co)
        self.assertEqual(r.months, Decimal("9.0"))
        self.assertEqual(r.level, "warning")

    def test_reporting_and_assemblies(self):
        today = timezone.localdate()
        Interaction.objects.create(company=self.co, occurred_on=today - timedelta(days=40), kind="report",
                                   summary="Q3 report")
        Interaction.objects.create(company=self.co, occurred_on=today - timedelta(days=200),
                                   kind="general_assembly", summary="AGM")
        Interaction.objects.create(company=self.co, occurred_on=today + timedelta(days=20),
                                   kind="general_assembly", summary="AGM")
        s = company_summary(self.co)
        self.assertEqual(s.last_report, today - timedelta(days=40))
        self.assertEqual(s.last_assembly, today - timedelta(days=200))
        self.assertEqual(s.next_assembly, today + timedelta(days=20))


class DealSummaryTests(TestCase):
    def test_round_figures(self):
        deal = Deal.objects.create(company=f.company(), round_name="Series A", round_size=Decimal("5000000"),
                                   pre_money=Decimal("15000000"), proposed_ticket=Decimal("1000000"))
        alpha = CoInvestor.objects.create(name="Alpha Ventures", kind="vc")
        corp = CoInvestor.objects.create(name="MedCorp", kind="strategic")
        RoundParticipant.objects.create(deal=deal, co_investor=alpha, role="lead", status="committed",
                                        amount=Decimal("2500000"))
        RoundParticipant.objects.create(deal=deal, co_investor=corp, status="interested", amount=Decimal("1000000"))
        ds = deal_summary(deal)
        self.assertEqual(ds.post_money, Decimal("20000000"))
        self.assertEqual(ds.implied_stake_pct, Decimal("5.00"))
        self.assertEqual(ds.committed, Decimal("2500000"))             # interested does not count
        self.assertEqual(ds.committed_pct, Decimal("50"))
        self.assertEqual(ds.lead.co_investor, alpha)
        self.assertEqual([p.co_investor for p in ds.strategics], [corp])


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fund = f.entity()
        cls.co = f.company("CardioSense SA")
        cls.deal = Deal.objects.create(company=cls.co, round_name="Series A", round_size=Decimal("5000000"),
                                       pre_money=Decimal("15000000"), proposed_ticket=Decimal("750000"),
                                       stage="analysis", deadline=timezone.localdate() + timedelta(days=5),
                                       next_step="Call with the lead")
        cls.admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        cls.viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(cls.viewer, "viewer")

    def setUp(self):
        verified_client(self.client, self.admin)

    def test_pipeline_shows_the_deal_in_its_stage(self):
        html = self.client.get("/pipeline/").content.decode()
        analysis = html.split(">Review")[1].split('class="column"')[0]
        self.assertIn("CardioSense SA", analysis)
        self.assertIn("in 5 days", analysis)

    def test_deal_page_shows_key_figures(self):
        response = self.client.get(f"/deals/{self.deal.pk}/")
        for text in ["CHF 5M", "CHF 15M", "CHF 20M", "CHF 750k", "3.75%", "Call with the lead"]:
            self.assertContains(response, text)

    def test_edit_a_section_in_place(self):
        url = f"/s/deal/{self.deal.pk}/round/"
        form = self.client.get(url + "?edit=1")
        self.assertContains(form, 'name="pre_money"')
        response = self.client.post(url, {"round_name": "Series A", "round_size": "6000000", "pre_money": "14000000",
                                          "proposed_ticket": "1000000", "currency": "CHF", "deadline": ""})
        self.assertContains(response, "CHF 20M")                       # 14M + 6M
        self.assertContains(response, "5.00%")
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.round_size, Decimal("6000000"))
        self.assertIsNone(self.deal.deadline)

    def test_invalid_input_keeps_the_form_open(self):
        response = self.client.post(f"/s/deal/{self.deal.pk}/round/", {"round_size": "lots", "currency": "CHF"})
        self.assertContains(response, "errorlist")
        self.assertContains(response, 'name="round_size"')

    def test_viewer_sees_but_cannot_edit(self):
        verified_client(self.client, self.viewer)
        response = self.client.get(f"/deals/{self.deal.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "?edit=1")
        self.assertNotContains(response, "Add investor")
        self.assertEqual(self.client.get(f"/s/deal/{self.deal.pk}/round/?edit=1").status_code, 403)
        self.assertEqual(self.client.post(f"/s/deal/{self.deal.pk}/round/", {"round_size": "1"}).status_code, 403)
        self.assertEqual(self.client.post(f"/deals/{self.deal.pk}/stage/", {"stage": "passed"}).status_code, 403)

    def test_add_investors_to_the_round(self):
        url = f"/deals/{self.deal.pk}/participants/"
        self.client.post(url, {"investor": "Alpha Ventures", "kind": "vc", "role": "lead", "status": "committed",
                               "amount": "2500000"})
        response = self.client.post(url, {"investor": "alpha ventures", "kind": "vc", "role": "lead",
                                          "status": "signed", "amount": "3000000"})
        self.assertEqual(CoInvestor.objects.count(), 1)                # same investor, case-insensitive
        self.assertEqual(RoundParticipant.objects.get().status, "signed")
        self.assertContains(response, "60%")                           # 3M of 5M
        self.client.post(url, {"investor": "MedCorp", "kind": "strategic", "role": "participant",
                               "status": "interested"})
        self.assertEqual(CoInvestor.objects.get(name="MedCorp").kind, "strategic")
        page = self.client.get(f"/deals/{self.deal.pk}/")
        self.assertContains(page, "Alpha Ventures")
        self.assertContains(page, "Strategic")

    def test_editing_a_participant_refreshes_the_totals(self):
        investor = CoInvestor.objects.create(name="Alpha Ventures")
        p = RoundParticipant.objects.create(deal=self.deal, co_investor=investor, status="interested",
                                            amount=Decimal("1000000"))
        response = self.client.post(f"/s/participant/{p.pk}/row/", {"role": "lead", "status": "committed",
                                                                     "amount": "1000000", "currency": "CHF"})
        self.assertEqual(response["HX-Retarget"], f"#deal-{self.deal.pk}-participants")
        self.assertContains(response, "20%")
        self.client.post(f"/participants/{p.pk}/delete/")
        self.assertFalse(RoundParticipant.objects.exists())

    def test_stage_change_is_logged_and_invested_moves_company_to_portfolio(self):
        response = self.client.post(f"/deals/{self.deal.pk}/stage/", {"stage": "invested"})
        self.assertEqual(response["HX-Refresh"], "true")
        self.deal.refresh_from_db()
        self.co.refresh_from_db()
        self.assertEqual(self.deal.closed_on, timezone.localdate())
        self.assertEqual(self.co.status, "portfolio")
        self.assertEqual(DealStageHistory.objects.filter(deal=self.deal).last().to_stage, "invested")
        self.assertContains(self.client.get(f"/deals/{self.deal.pk}/"), "Record the financing round")

    def test_quick_activity_entry(self):
        response = self.client.post("/interactions/add/", {"company": self.co.pk, "deal": self.deal.pk,
                                                           "occurred_on": "2026-11-10", "kind": "call",
                                                           "summary": "Spoke with the CEO"})
        self.assertContains(response, "Spoke with the CEO")
        self.assertEqual(Interaction.objects.get().deal, self.deal)
        self.assertContains(self.client.get(f"/companies/{self.co.pk}/"), "Spoke with the CEO")

    def test_new_deal_with_a_new_company(self):
        response = self.client.post("/deals/new/", {"new_company": "NeuroPath SA", "sector": "Diagnostics",
                                                    "round_name": "Seed", "round_size": "2000000", "currency": "CHF"})
        deal = Deal.objects.get(company__name="NeuroPath SA")
        self.assertRedirects(response, f"/deals/{deal.pk}/", fetch_redirect_response=False)
        self.assertEqual(deal.stage, "sourced")
        self.assertEqual(deal.company.sector, "Diagnostics")

    def test_portfolio_and_company_pages(self):
        common = f.share_class(self.co, "Common", common=True)
        f.issue(common, 1_000_000, D(2024, 1, 1))
        f.buy(self.fund, common, 100_000, D(2024, 1, 1), "200000")
        Valuation.objects.create(company=self.co, valuation_date=D(2025, 1, 1), method="round",
                                 price_per_share=Decimal("5"))
        Company.objects.filter(pk=self.co.pk).update(status="portfolio")
        portfolio = self.client.get("/portfolio/")
        for text in ["CardioSense SA", "10.00%", "CHF 200k", "CHF 500k", "2.50×"]:
            self.assertContains(portfolio, text)
        page = self.client.get(f"/companies/{self.co.pk}/")
        for text in ["Our position", "Governance and reporting", "Cap table today", "Open round"]:
            self.assertContains(page, text)

    def test_governance_section_edit(self):
        response = self.client.post(f"/s/company/{self.co.pk}/governance/", {
            "board_role": "observer", "reporting_frequency": "quarterly", "data_room_url": "https://dataroom.example"})
        self.assertContains(response, "Observer")
        self.assertContains(response, "Quarterly")
        self.assertContains(response, "https://dataroom.example")

    def test_home_lists_upcoming_deadlines(self):
        self.assertContains(self.client.get("/"), "CardioSense SA")

    def test_admin_returns_to_the_page_after_saving(self):
        response = self.client.post(f"/admin/core/contact/add/?company={self.co.pk}&next=/companies/{self.co.pk}/",
                                    {"full_name": "Jane CEO", "company": self.co.pk})
        self.assertRedirects(response, f"/companies/{self.co.pk}/", fetch_redirect_response=False)
        evil = self.client.post("/admin/core/contact/add/?next=https://evil.example/",
                                {"full_name": "Joe", "company": self.co.pk})
        self.assertNotIn("evil.example", evil["Location"])


class RoundParticipantIntegrityTests(TestCase):
    def test_sql_delete_of_deal_removes_participants(self):
        deal = Deal.objects.create(company=f.company())
        RoundParticipant.objects.create(deal=deal, co_investor=CoInvestor.objects.create(name="X"))
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM deals WHERE id = %s", [deal.pk])
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            cursor.execute("SELECT count(*) FROM round_participants")
            self.assertEqual(cursor.fetchone()[0], 0)


class DocumentFileTests(TestCase):
    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from core.models import Document

        self.doc = Document.objects.create(company=f.company(), title="Deck",
                                           file=SimpleUploadedFile("deck.pdf", b"%PDF-1.4 x", "application/pdf"))
        self.url = self.doc.file.url

    def test_requires_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response["Location"])

    def test_logged_in_user_gets_the_file_inline(self):
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 x")
        self.assertIn("inline", response["Content-Disposition"])
        self.assertEqual(self.client.get("/files/../config/settings.py").status_code, 404)
        self.assertEqual(self.client.get("/files/documents/other.pdf").status_code, 404)


class CompanyOverviewTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="NeuroPath SA", sector="Diagnostics", city="Lausanne",
                                         description="Blood test for early detection of neurodegeneration.")
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))

    def test_overview_comes_first_with_key_information(self):
        from core.models import FundingRound, Milestone

        FundingRound.objects.create(company=self.co, round_name="Seed", announced_on=D(2024, 3, 1),
                                    amount=Decimal("4500000"), post_money=Decimal("18000000"))
        Milestone.objects.create(company=self.co, title="Pivotal study start", category="clinical",
                                 target_date=timezone.localdate() + timedelta(days=30))
        Contact.objects.create(full_name="Marc Dubois", role="CEO", company=self.co)
        html = self.client.get(f"/companies/{self.co.pk}/").content.decode()
        overview = html.split("<h2>Overview")[1].split("</section>")[0]
        for text in ["Blood test for early detection", "Diagnostics", "Lausanne", "Seed", "CHF 4.5M",
                     "Marc Dubois", "Pivotal study start", "in 30 days"]:
            self.assertIn(text, overview)
        self.assertLess(html.index("<h2>Overview"), html.index("Funding history"))

    def test_prospect_page_hides_empty_portfolio_cards(self):
        html = self.client.get(f"/companies/{self.co.pk}/").content.decode()
        self.assertNotIn("Our position", html)
        self.assertNotIn("Cap table today", html)
        self.assertIn('href="/pipeline/"', html.split("<h1>")[0])
        Company.objects.filter(pk=self.co.pk).update(status="portfolio")
        html = self.client.get(f"/companies/{self.co.pk}/").content.decode()
        self.assertIn("Our position", html)
        self.assertIn("Cap table today", html)

    def test_overview_is_editable_in_place(self):
        form = self.client.get(f"/s/company/{self.co.pk}/profile/?edit=1")
        self.assertContains(form, 'name="description"')
        self.assertContains(form, "card editing wide")
        response = self.client.post(f"/s/company/{self.co.pk}/profile/", {
            "name": "NeuroPath SA", "status": "prospect", "sector": "Diagnostics", "city": "Geneva",
            "description": "New description."})
        self.assertContains(response, "New description.")
        self.assertContains(response, "Geneva")

    def test_deal_page_shows_the_company(self):
        deal = Deal.objects.create(company=self.co, round_name="Series A")
        page = self.client.get(f"/deals/{deal.pk}/")
        self.assertContains(page, "About NeuroPath SA")
        self.assertContains(page, "Blood test for early detection")

    def test_menu_highlights_pipeline_for_prospects(self):
        html = self.client.get(f"/companies/{self.co.pk}/").content.decode()
        nav = html.split("<nav>")[1].split("</nav>")[0]
        self.assertIn('href="/pipeline/" aria-current="page"', nav)
        self.assertNotIn('href="/portfolio/" aria-current="page"', nav)
