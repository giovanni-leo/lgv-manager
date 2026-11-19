"""Recording a financing round in one step."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (CoInvestor, Company, Deal, FundingRound, Investment, OptionPool, ShareClass, ShareEvent,
                         Valuation)
from core.roles import assign_role
from core.services.ownership import ownership_by_class, ownership_company
from core.services.portfolio import company_summary

from . import factories as f
from .test_auth import verified_client

D = date


class RecordRoundPageTests(TestCase):
    def setUp(self):
        self.fund = f.entity()
        self.co = Company.objects.create(name="NeuroPath SA")
        self.deal = Deal.objects.create(company=self.co, round_name="Series A", stage="term_sheet",
                                        pre_money=Decimal("8800000"), proposed_ticket=Decimal("400000"))
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.user)
        self.url = f"/companies/{self.co.pk}/rounds/new/"

    def post(self, **fields):
        data = {"round_name": "Series A", "round_date": "2026-11-10", "share_class": "", "new_class_name": "Series A",
                "price_per_share": "8", "new_shares": "250000", "currency": "CHF", "pre_money": "",
                "our_shares": "50000", "our_amount": "", "entity": self.fund.pk, "pool_size": "100000",
                "warrants": "", "lead_investors": "Alpine Health Ventures", "other_investors": "MedCorp AG",
                "notes": "", "ex_name_1": "Common", "ex_shares_1": "1000000", "ex_common_1": "on"}
        data.update(fields)
        return self.client.post(self.url + (f"?deal={self.deal.pk}"), data)

    def test_first_round_builds_the_whole_cap_table(self):
        page = self.client.get(self.url + f"?deal={self.deal.pk}")
        self.assertContains(page, "Cap table before this round")
        self.assertContains(page, 'value="Series A"')                  # prefilled from the deal

        response = self.post()
        self.assertRedirects(response, f"/companies/{self.co.pk}/", fetch_redirect_response=False)
        classes = {c.name: c for c in ShareClass.objects.filter(company=self.co)}
        self.assertTrue(classes["Common"].is_common)
        self.assertEqual(classes["Series A"].original_issue_price, Decimal("8"))
        self.assertEqual(ShareEvent.objects.get(share_class=classes["Common"]).event_date, D(2026, 11, 9))
        inv = Investment.objects.get()
        self.assertEqual((inv.shares, inv.amount, inv.entity, inv.deal), (50000, Decimal("400000.00"),
                                                                          self.fund, self.deal))
        self.assertEqual(OptionPool.objects.get().authorized, 100000)

        own = ownership_company(self.co.pk, D(2026, 11, 10))
        self.assertEqual(own.pct_basic, Decimal("4.0000"))                # 50k / 1.25M
        self.assertEqual(own.pct_fully_diluted, Decimal("3.7037"))        # 50k / 1.35M
        v = Valuation.objects.get()
        self.assertEqual((v.price_per_share, v.post_money), (Decimal("8"), Decimal("10800000.00")))  # 8 × 1.35M

        r = FundingRound.objects.get()
        self.assertEqual((r.amount, r.pre_money, r.post_money), (Decimal("2000000.00"), Decimal("8800000.00"),
                                                                 Decimal("10800000.00")))
        self.assertEqual([i.name for i in r.leads], ["Alpine Health Ventures"])
        self.deal.refresh_from_db()
        self.co.refresh_from_db()
        self.assertEqual((self.deal.stage, self.co.status), ("invested", "portfolio"))
        summary = company_summary(self.co, D(2026, 11, 11))
        self.assertEqual((summary.invested_chf, summary.stake_value), (Decimal("400000.00"), Decimal("400000")))

        company_page = self.client.get(f"/companies/{self.co.pk}/")
        self.assertContains(company_page, "Series A recorded: 250&#x27;000 new Series A shares")
        self.assertContains(company_page, "3.70%")

    def test_amount_instead_of_shares(self):
        self.post(our_shares="", our_amount="400000")
        self.assertEqual(Investment.objects.get().shares, 50000)

    def test_next_round_uses_existing_classes_and_given_pre_money(self):
        self.post()
        self.assertNotContains(self.client.get(self.url), "Cap table before this round")
        series_a = ShareClass.objects.get(name="Series A")
        self.client.post(self.url, {
            "round_name": "Series B", "round_date": "2027-06-30", "share_class": "", "new_class_name": "Series B",
            "price_per_share": "12", "new_shares": "500000", "currency": "CHF", "pre_money": "20000000",
            "our_shares": "", "our_amount": "", "entity": "", "pool_size": "", "warrants": "",
            "lead_investors": "", "other_investors": "", "notes": ""})
        r = FundingRound.objects.get(round_name="Series B")
        self.assertEqual((r.amount, r.pre_money, r.post_money), (Decimal("6000000.00"), Decimal("20000000.00"),
                                                                 Decimal("26000000.00")))
        rows = {c.share_class: c for c in ownership_by_class(self.co.pk, D(2027, 7, 1))}
        self.assertEqual(rows["Series B"].shares_outstanding, 500000)
        self.assertEqual(rows["Series A"].my_shares, 50000)               # unchanged, now diluted
        self.assertEqual(Investment.objects.count(), 1)
        self.assertEqual(ShareClass.objects.get(name="Series B").seniority, series_a.seniority + 1)

    def test_validation(self):
        response = self.post(new_class_name="", our_shares="300000")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose the share class of the round")
        self.assertFalse(ShareEvent.objects.exists())
        self.post()
        response = self.post(new_class_name="series a")
        self.assertContains(response, "already exists")

    def test_completes_a_round_found_on_the_web(self):
        FundingRound.objects.create(company=self.co, round_name="Series A", announced_on=D(2026, 11, 1),
                                    source_url="https://news.example")
        self.post()
        r = FundingRound.objects.get()
        self.assertEqual((r.source_url, r.announced_on), ("https://news.example", D(2026, 11, 10)))
        self.assertEqual(CoInvestor.objects.get(name="MedCorp AG").kind, "vc")

    def test_viewer_cannot_record(self):
        viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(viewer, "viewer")
        verified_client(self.client, viewer)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertNotContains(self.client.get(f"/companies/{self.co.pk}/"), "Record a financing round")

    def test_buttons(self):
        self.assertContains(self.client.get(f"/companies/{self.co.pk}/"), f"/companies/{self.co.pk}/rounds/new/")
        self.assertContains(self.client.get(f"/deals/{self.deal.pk}/"), f"rounds/new/?deal={self.deal.pk}")

    def test_open_deal_with_the_same_round_name_is_closed(self):
        self.client.post(self.url, {
            "round_name": "series a", "round_date": "2026-11-10", "share_class": "", "new_class_name": "Series A",
            "price_per_share": "8", "new_shares": "250000", "currency": "CHF", "pre_money": "", "our_shares": "50000",
            "our_amount": "", "entity": self.fund.pk, "pool_size": "", "warrants": "", "lead_investors": "",
            "other_investors": "", "notes": "", "ex_name_1": "Common", "ex_shares_1": "1000000", "ex_common_1": "on"})
        self.deal.refresh_from_db()
        self.assertEqual(self.deal.stage, "invested")
        self.assertEqual(Investment.objects.get().deal, self.deal)


class FormatTests(TestCase):
    def test_no_negative_zero(self):
        from core.templatetags.vc import ratio_pct

        self.assertEqual(ratio_pct(Decimal("-0.0000001")), "0.0%")
        self.assertEqual(ratio_pct(Decimal("-0.05")), "-5.0%")
