"""Funds: selector, deals assigned to a fund, positions in the fund's currency, ECB exchange rates."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.models import (Company, Deal, FxRate, InvestingEntity, Investment, Realization, ShareClass, ShareEvent,
                         Valuation)
from core.services import ecb
from core.services.fx import convert
from core.services.portfolio import company_summary

from .test_auth import verified_client

D = Decimal

ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01" xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
<gesmes:subject>Reference rates</gesmes:subject><Cube>
<Cube time="2026-11-20"><Cube currency="USD" rate="1.0600"/><Cube currency="CHF" rate="0.9300"/><Cube currency="JPY" rate="160.1"/></Cube>
<Cube time="2026-11-19"><Cube currency="USD" rate="1.0500"/><Cube currency="CHF" rate="0.9400"/></Cube>
</Cube></gesmes:Envelope>"""


def fund(name, code, currency, kind="fund"):
    return InvestingEntity.objects.create(name=name, code=code, kind=kind, base_currency=currency)


class EcbRatesTests(TestCase):
    def test_rates_are_stored_as_chf_per_unit(self):
        added = ecb.update(fetch=lambda url: ECB_XML)
        self.assertEqual(added, 4)                                          # EUR and USD, two days
        eur = FxRate.objects.get(currency="EUR", rate_date=date(2026, 11, 20)).chf_per_unit
        usd = FxRate.objects.get(currency="USD", rate_date=date(2026, 11, 20)).chf_per_unit
        self.assertEqual(eur, D("0.93000000"))
        self.assertEqual(usd, D("0.87735849"))                              # 0.93 / 1.06
        self.assertFalse(FxRate.objects.filter(currency="JPY").exists())    # only the currencies in use

    def test_existing_and_manual_rates_are_kept(self):
        FxRate.objects.create(currency="USD", rate_date=date(2026, 11, 20), chf_per_unit=D("0.5"))
        self.assertEqual(ecb.update(fetch=lambda url: ECB_XML), 3)
        self.assertEqual(FxRate.objects.get(currency="USD", rate_date=date(2026, 11, 20)).chf_per_unit, D("0.5"))
        self.assertEqual(ecb.update(fetch=lambda url: ECB_XML), 0)           # running again adds nothing


class ConvertTests(TestCase):
    def setUp(self):
        ecb.update(fetch=lambda url: ECB_XML)

    def test_through_chf(self):
        self.assertEqual(convert(D(100), "USD", "USD", date(2026, 11, 20)), D(100))
        self.assertAlmostEqual(float(convert(D(106), "USD", "EUR", date(2026, 11, 20))), 100, places=4)
        self.assertAlmostEqual(float(convert(D(100), "EUR", "CHF", date(2026, 11, 20))), 93, places=4)
        self.assertAlmostEqual(float(convert(D(93), "CHF", "EUR", date(2026, 11, 20))), 100, places=4)

    def test_latest_rate_on_or_before_the_date(self):
        self.assertAlmostEqual(float(convert(D(100), "EUR", "CHF", date(2026, 11, 21))), 93, places=4)   # a Saturday
        self.assertAlmostEqual(float(convert(D(100), "EUR", "CHF", date(2026, 11, 19))), 94, places=4)
        self.assertIsNone(convert(D(100), "EUR", "CHF", date(2020, 1, 1)))                              # none yet

    def test_the_rate_of_the_transaction_wins(self):
        self.assertEqual(convert(D(100), "USD", "CHF", date(2026, 11, 20), override_chf=D("0.9")), D(90))


class FundPositionTests(TestCase):
    def setUp(self):
        ecb.update(fetch=lambda url: ECB_XML)
        self.efh = fund("Efficient Frontier Holding SA", "EFH", "USD")
        self.lgv1 = fund("Lake Geneva Ventures 1", "LGV1", "CHF")
        self.co = Company.objects.create(name="Field Medical", status="portfolio")
        cls = ShareClass.objects.create(company=self.co, name="Series B", original_issue_price=D("1.5"),
                                        currency="USD")
        ShareEvent.objects.create(company=self.co, share_class=cls, event_type="issuance",
                                  event_date=date(2026, 6, 1), shares_delta=1000)
        Investment.objects.create(company=self.co, entity=self.efh, share_class=cls, invest_date=date(2026, 6, 1),
                                  instrument="equity", shares=100, price_per_share=D("1.5"), amount=D("150"),
                                  currency="USD")
        Valuation.objects.create(company=self.co, valuation_date=date(2026, 6, 1), method="round",
                                 price_per_share=D("3"), share_class=cls, currency="USD")
        Investment.objects.create(company=self.co, entity=self.lgv1, invest_date=date(2025, 1, 1),
                                  instrument="reported", amount=D("1000"), currency="CHF", reported_value=D("1500"),
                                  reported_value_date=date(2026, 9, 30))

    def test_each_fund_is_shown_in_its_own_currency(self):
        e = company_summary(self.co, date(2026, 11, 20), fund=self.efh)
        self.assertEqual((e.ccy, e.invested_chf, e.stake_value_chf), ("USD", D("150.00"), D("300.0")))   # 100 x 3 USD
        self.assertEqual(e.moic, D("2.00"))
        c = company_summary(self.co, date(2026, 11, 20), fund=self.lgv1)
        self.assertEqual((c.ccy, c.invested_chf, c.stake_value_chf), ("CHF", D("1000.00"), D("1500.00")))
        self.assertEqual(c.moic, D("1.50"))

    def test_a_reported_value_after_the_date_is_not_used(self):
        c = company_summary(self.co, date(2026, 6, 30), fund=self.lgv1)
        self.assertIsNone(c.stake_value_chf)

    def test_money_in_another_currency_is_converted(self):
        Realization.objects.create(company=self.co, entity=self.efh, realized_on=date(2026, 11, 20),
                                   kind=Realization.Kind.values[0], proceeds=D("93"),
                                   currency="CHF")
        e = company_summary(self.co, date(2026, 11, 20), fund=self.efh)
        self.assertAlmostEqual(float(e.realized_chf), 93 / 0.87735849, places=2)                          # CHF -> USD


class FundPagesTests(TestCase):
    def setUp(self):
        ecb.update(fetch=lambda url: ECB_XML)
        self.efh = fund("Efficient Frontier Holding SA", "EFH", "USD")
        self.lgv2 = fund("Lake Growth Ventures 2", "LGV2", "EUR")
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.user)
        self.co = Company.objects.create(name="Field Medical", status="portfolio")
        Investment.objects.create(company=self.co, entity=self.efh, invest_date=date(2026, 6, 1),
                                  instrument="reported", amount=D("1000"), currency="USD", reported_value=D("1200"))

    def test_the_first_fund_is_shown_by_default_and_the_choice_is_remembered(self):
        page = self.client.get("/portfolio/")
        self.assertContains(page, '<button type="submit" name="fund" value="%d" class="pill on"' % self.efh.pk)
        self.assertContains(page, "<h1>Portfolio <span")                        # the page says which fund it shows
        self.assertContains(page, "USD 1&#x27;200")                             # EFH, in dollars
        self.assertEqual(self.client.post("/fund/select/", {"fund": self.lgv2.pk, "next": "/portfolio/"})["Location"],
                         "/portfolio/")
        page = self.client.get("/portfolio/")
        self.assertContains(page, '<button type="submit" name="fund" value="%d" class="pill on"' % self.lgv2.pk)
        self.assertNotContains(page, "Field Medical")                          # LGV2 has no investments yet
        self.client.post("/fund/select/", {"fund": "all"})
        page = self.client.get("/portfolio/")
        self.assertContains(page, "Field Medical")                              # all funds: one section per fund
        self.assertContains(page, 'name="fund" value="all" class="pill pill-all on"')

    def test_home_shows_only_the_selected_fund(self):
        from datetime import timedelta

        from django.utils import timezone

        soon = timezone.localdate() + timedelta(days=5)
        beta = Company.objects.create(name="BetaCo", status="portfolio")
        Investment.objects.create(company=beta, entity=self.lgv2, invest_date=date(2026, 6, 1), instrument="reported",
                                  amount=D("10"), currency="EUR", reported_value=D("10"))
        from core.models import Interaction

        Interaction.objects.create(company=self.co, kind="general_assembly", occurred_on=soon, summary="AGM Field")
        Interaction.objects.create(company=beta, kind="board", occurred_on=soon, summary="Board BetaCo")
        Deal.objects.create(company=Company.objects.create(name="AlphaDeal"), stage="due_diligence", fund=self.efh,
                            deadline=soon, round_name="A")
        Deal.objects.create(company=Company.objects.create(name="BetaDeal"), stage="due_diligence", fund=self.lgv2,
                            deadline=soon, round_name="A")
        Deal.objects.create(company=Company.objects.create(name="NewDeal"), stage="sourced", deadline=soon)
        home = self.client.get("/")                                              # EFH by default
        self.assertContains(home, "AGM Field")
        self.assertContains(home, "AlphaDeal")
        self.assertContains(home, "NewDeal")                                     # no fund yet: always visible
        self.assertNotContains(home, "BetaDeal")
        self.assertNotContains(home, "Board BetaCo")
        self.client.post("/fund/select/", {"fund": self.lgv2.pk})
        home = self.client.get("/")
        self.assertContains(home, "BetaDeal")
        self.assertContains(home, "Board BetaCo")
        self.assertNotContains(home, "AlphaDeal")
        self.assertNotContains(home, "AGM Field")
        self.client.post("/fund/select/", {"fund": "all"})
        home = self.client.get("/")
        for text in ("AlphaDeal", "BetaDeal", "AGM Field", "Board BetaCo"):
            self.assertContains(home, text)

    def test_with_a_single_fund_there_is_no_selector(self):
        self.lgv2.delete()
        self.assertNotContains(self.client.get("/portfolio/"), 'class="fund-bar"')

    def test_selector_ignores_unsafe_redirects_and_unknown_funds(self):
        response = self.client.post("/fund/select/", {"fund": "9999", "next": "https://evil.example/"})
        self.assertEqual(response["Location"], "/")

    def test_pipeline_shows_the_fund_and_filters_by_it(self):
        Deal.objects.create(company=Company.objects.create(name="Alpha"), stage="due_diligence", fund=self.efh)
        Deal.objects.create(company=Company.objects.create(name="Beta"), stage="due_diligence", fund=self.lgv2)
        Deal.objects.create(company=Company.objects.create(name="Gamma"), stage="sourced")           # no fund yet
        self.client.post("/fund/select/", {"fund": "all"})
        page = self.client.get("/pipeline/")
        self.assertContains(page, "Alpha")
        self.assertContains(page, "Beta")
        self.client.post("/fund/select/", {"fund": self.efh.pk})
        page = self.client.get("/pipeline/")
        self.assertContains(page, "Alpha")
        self.assertContains(page, "Gamma")
        self.assertNotContains(page, "Beta")

    def test_deal_needs_a_fund_from_qualified_on(self):
        deal = Deal.objects.create(company=Company.objects.create(name="Alpha"), stage="sourced")
        response = self.client.post(f"/deals/{deal.pk}/stage/", {"stage": "first_meeting"}, follow=False)
        deal.refresh_from_db()
        self.assertEqual(deal.stage, "sourced")                                  # two funds: a choice is needed
        self.assertEqual(response["HX-Refresh"], "true")
        deal.fund = self.lgv2
        deal.save()
        self.client.post(f"/deals/{deal.pk}/stage/", {"stage": "first_meeting"})
        deal.refresh_from_db()
        self.assertEqual(deal.stage, "first_meeting")

    def test_with_a_single_fund_it_is_assigned_automatically(self):
        self.lgv2.delete()
        deal = Deal.objects.create(company=Company.objects.create(name="Alpha"), stage="sourced")
        self.client.post(f"/deals/{deal.pk}/stage/", {"stage": "first_meeting"})
        deal.refresh_from_db()
        self.assertEqual((deal.stage, deal.fund), ("first_meeting", self.efh))

    def test_company_page_lists_the_position_of_each_fund(self):
        page = self.client.get(f"/companies/{self.co.pk}/")
        self.assertContains(page, "EFH")
        self.assertContains(page, "USD 1&#x27;200")

    def test_new_deal_form_has_a_fund_field(self):
        self.client.post("/deals/new/", {"new_company": "OrthoFlex SA", "currency": "EUR", "fund": self.lgv2.pk})
        self.assertEqual(Deal.objects.get().fund, self.lgv2)


class SetupFundsTests(TestCase):
    def test_creates_the_three_funds_and_reuses_the_existing_entity(self):
        from django.core.management import call_command

        old = InvestingEntity.objects.create(name="Efficient Frontier Holding SA", kind="company", base_currency="CHF")
        other = InvestingEntity.objects.create(name="Old vehicle", kind="company", base_currency="CHF")
        co = Company.objects.create(name="Field Medical", status="portfolio")
        inv = Investment.objects.create(company=co, entity=other, invest_date=date(2026, 6, 1), instrument="spv",
                                        amount=D(10), currency="USD")
        deal = Deal.objects.create(company=co, stage="invested")
        sourced = Deal.objects.create(company=Company.objects.create(name="New"), stage="sourced")
        call_command("setup_funds", assign_existing="EFH", stdout=__import__("io").StringIO())
        old.refresh_from_db()
        self.assertEqual((old.code, old.kind, old.base_currency), ("EFH", "fund", "USD"))
        self.assertEqual(sorted(InvestingEntity.objects.exclude(name="Old vehicle").values_list("code", "base_currency")),
                         [("EFH", "USD"), ("LGV1", "CHF"), ("LGV2", "EUR")])
        inv.refresh_from_db()
        deal.refresh_from_db()
        sourced.refresh_from_db()
        self.assertEqual((inv.entity, deal.fund, sourced.fund), (old, old, None))
        call_command("setup_funds", stdout=__import__("io").StringIO())                      # again: nothing changes
        self.assertEqual(InvestingEntity.objects.filter(code__isnull=False).count(), 3)


class FundDocumentsTests(TestCase):
    def setUp(self):
        from .test_documents import make_document, tiny_pdf

        self.efh = fund("Efficient Frontier Holding SA", "EFH", "USD")
        self.lgv2 = fund("Lake Growth Ventures 2", "LGV2", "EUR")
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))
        field = Company.objects.create(name="Field Medical", status="portfolio")
        Investment.objects.create(company=field, entity=self.efh, invest_date=date(2026, 6, 1), instrument="spv",
                                  amount=D(10), currency="USD")
        beta = Company.objects.create(name="BetaCo", status="portfolio")
        Investment.objects.create(company=beta, entity=self.lgv2, invest_date=date(2026, 6, 1), instrument="spv",
                                  amount=D(10), currency="EUR")
        new = Company.objects.create(name="NewCo")                                 # no fund yet
        make_document(field, "a.pdf", tiny_pdf("a"), title="Field SPA")
        make_document(beta, "b.pdf", tiny_pdf("b"), title="Beta SPA")
        make_document(new, "c.pdf", tiny_pdf("c"), title="New deck")

    def titles(self):
        return sorted(d.title for d in self.client.get("/documents/").context["page"])

    def test_documents_follow_the_selected_fund(self):
        self.assertEqual(self.titles(), ["Field SPA", "New deck"])                 # EFH by default + companies without fund
        page = self.client.get("/documents/")
        self.assertContains(page, "<h1>Documents <span")
        self.assertNotContains(page, ">BetaCo</option>")                          # not in the company filter either
        self.client.post("/fund/select/", {"fund": self.lgv2.pk})
        self.assertEqual(self.titles(), ["Beta SPA", "New deck"])
        self.client.post("/fund/select/", {"fund": "all"})
        self.assertEqual(self.titles(), ["Beta SPA", "Field SPA", "New deck"])


class MergeFundsTests(TestCase):
    def setUp(self):
        self.efh = fund("Efficient Frontier Holding SA", "EFH", "USD")
        self.spv = fund("EFH SPV", "EFH-SPV", "USD")
        co = Company.objects.create(name="Field Medical", status="portfolio")
        self.inv = Investment.objects.create(company=co, entity=self.efh, invest_date=date(2026, 6, 1),
                                             instrument="spv", amount=D(10), currency="USD")
        self.real = Realization.objects.create(company=co, entity=self.efh, realized_on=date(2026, 7, 1),
                                               kind=Realization.Kind.values[0], proceeds=D(5), currency="USD")
        self.deal = Deal.objects.create(company=co, stage="invested", fund=self.efh)

    def run_command(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("merge_funds", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        out = self.run_command("EFH", "EFH-SPV")
        self.assertIn("1 investment(s), 1 realization(s), 1 deal(s)", out)
        self.assertIn("Dry run", out)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.entity, self.efh)

    def test_apply_moves_everything_and_removes_the_fund(self):
        self.run_command("EFH", "EFH-SPV", "--apply")
        self.inv.refresh_from_db(), self.real.refresh_from_db(), self.deal.refresh_from_db()
        self.assertEqual((self.inv.entity, self.real.entity, self.deal.fund), (self.spv, self.spv, self.spv))
        self.assertFalse(InvestingEntity.objects.filter(code="EFH").exists())

    def test_different_currencies_are_flagged_and_unknown_or_same_funds_refused(self):
        from django.core.management import CommandError

        InvestingEntity.objects.filter(pk=self.spv.pk).update(base_currency="CHF")
        self.assertIn("The currencies differ", self.run_command("EFH", "EFH-SPV"))
        with self.assertRaises(CommandError):
            self.run_command("EFH", "NOPE")
        with self.assertRaises(CommandError):
            self.run_command("EFH", "EFH")

    def test_setup_funds_does_not_bring_efh_back_when_efh_spv_exists(self):
        from io import StringIO

        from django.core.management import call_command

        self.run_command("EFH", "EFH-SPV", "--apply")
        call_command("setup_funds", stdout=StringIO())
        self.assertEqual(sorted(InvestingEntity.objects.values_list("code", flat=True)), ["EFH-SPV", "LGV1", "LGV2"])
        self.assertEqual(InvestingEntity.objects.get(code="EFH-SPV").base_currency, "USD")


class AppNameTests(TestCase):
    def test_the_top_bar_names_the_app_not_a_fund(self):
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))
        page = self.client.get("/")
        self.assertContains(page, '<a class="brand" href="/">VC Manager</a>', html=True)
        self.assertContains(page, "<title>VC Manager</title>")
