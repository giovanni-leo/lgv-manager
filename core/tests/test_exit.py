"""Exit waterfall with liquidation preference and seniority (Field Medical after the Series B)."""

from decimal import Decimal
from datetime import date

from django.test import TestCase

from core.models import Company, InvestingEntity, Investment, OptionPool, ShareClass, ShareEvent
from core.services.exit import ClassTerms, ExitModel, breakeven, build_model, exit_payout

D = Decimal


def field_medical():
    co = Company.objects.create(name="Field Medical", status="portfolio")
    ef = InvestingEntity.objects.create(name="Efficient Frontier Holding SA", kind="company")
    classes = {}
    for name, shares, price, seniority, common in [
            ("Common", 1724623, None, 0, True), ("Series A-1 Preferred", 3234860, "6.43", 1, False),
            ("Series A Preferred", 1856814, "10.77", 1, False), ("Series B Preferred", 2185833, "13.724719", 2, False)]:
        classes[name] = ShareClass.objects.create(company=co, name=name, is_common=common, seniority=seniority,
                                                  original_issue_price=price, currency="USD")
        ShareEvent.objects.create(company=co, share_class=classes[name], event_type="issuance",
                                  event_date=date(2025, 6, 20), shares_delta=shares)
    OptionPool.objects.create(company=co, as_of=date(2025, 6, 20), authorized=1131431, warrants=67008)
    for cls, shares, price in [("Series A Preferred", 165273, "10.77"), ("Series B Preferred", 56278, "13.724719")]:
        Investment.objects.create(company=co, entity=ef, share_class=classes[cls], invest_date=date(2025, 6, 20),
                                  instrument="equity", shares=shares, price_per_share=price,
                                  amount=D(price) * shares, currency="USD")
    return co


class WaterfallTests(TestCase):
    def setUp(self):
        self.model = build_model(field_medical())

    def ours(self, value):
        return exit_payout(self.model, value).my_payout

    def test_payouts_match_hand_calculation(self):
        for exit_value, expected in [(20e6, 514_934), (50e6, 1_644_000), (70.8e6, 2_552_390), (100e6, 2_552_390),
                                     (140e6, 3_040_725), (300e6, 6_520_000)]:
            self.assertAlmostEqual(float(self.ours(D(str(exit_value)))), expected, delta=6000, msg=f"exit {exit_value}")

    def test_everything_is_distributed(self):
        for value in (5e6, 30e6, 31e6, 80e6, 100e6, 250e6, 1e9):
            result = exit_payout(self.model, D(str(value)))
            self.assertAlmostEqual(float(sum(r.payout for r in result.rows)), value, delta=1, msg=str(value))

    def test_series_b_is_paid_first(self):
        result = exit_payout(self.model, D("20000000"))
        by = {r.name: r for r in result.rows}
        self.assertEqual(by["Series B Preferred"].payout, D("20000000"))      # preference 30M: takes all
        self.assertEqual(by["Series A Preferred"].payout, 0)
        self.assertEqual(by["Common"].payout, 0)

    def test_a_and_a1_are_paid_pari_passu(self):
        by = {r.name: r for r in exit_payout(self.model, D("40000000")).rows}   # after 30M to B, 10M for A and A-1
        pref_a, pref_a1 = by["Series A Preferred"].preference, by["Series A-1 Preferred"].preference
        paid = by["Series A Preferred"].payout + by["Series A-1 Preferred"].payout
        self.assertAlmostEqual(float(paid), 10e6, delta=100)
        self.assertAlmostEqual(float(by["Series A Preferred"].payout / paid), float(pref_a / (pref_a + pref_a1)), places=9)

    def test_classes_convert_when_better_off(self):
        mid = {r.name: r.converted for r in exit_payout(self.model, D("100000000")).rows}
        self.assertEqual((mid["Series B Preferred"], mid["Series A Preferred"], mid["Series A-1 Preferred"]),
                         (False, False, True))                                    # A-1 (6.43) converts first
        high = {r.name: r.converted for r in exit_payout(self.model, D("300000000")).rows}
        self.assertTrue(all(high[n] for n in ("Series A Preferred", "Series A-1 Preferred", "Series B Preferred")))

    def test_breakeven(self):
        be = breakeven(self.model, D("2552389.95"))           # our preferences are fully covered at 30M + 40.8M
        self.assertAlmostEqual(float(be), 70.8e6, delta=0.1e6)
        self.assertGreaterEqual(self.ours(be), D("2552389.95") - 2)
        self.assertLess(self.ours(be - 1000000), D("2552389.95") - 1)
        self.assertIsNone(breakeven(self.model, D("0")))

    def test_participating_preferred_gets_both(self):
        model = ExitModel([ClassTerms("P", D(100), D(1), D(1000), 1, True, False, D(100)),
                           ClassTerms("Common", D(100), D(1), D(0), 0, False, True, D(0))], D(0))
        result = exit_payout(model, D(3000))
        self.assertEqual(result.rows[0].payout, D(1000) + D(1000))            # 1000 preference + half of the rest
        self.assertEqual(result.rows[1].payout, D(1000))

    def test_missing_price_is_reported(self):
        co = Company.objects.create(name="NoPrice")
        cls = ShareClass.objects.create(company=co, name="Series A")
        ShareEvent.objects.create(company=co, share_class=cls, event_type="issuance", event_date=date(2025, 1, 1),
                                  shares_delta=10)
        self.assertIn("Series A has no original issue price", build_model(co).warnings[0])


class ExitCardTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User

        from core.models import Valuation

        from .test_auth import verified_client

        self.co = field_medical()
        Valuation.objects.create(company=self.co, valuation_date=date(2025, 6, 20), method="round",
                                 post_money=D("140000000"), currency="USD")
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))

    def test_card_shows_scenarios_from_the_valuation(self):
        page = self.client.get(f"/companies/{self.co.pk}/")
        self.assertContains(page, "If the company is sold")
        self.assertContains(page, "USD 35M")                        # 0.25 × 140M
        self.assertContains(page, "USD 560M")
        self.assertContains(page, "We get our money back (1×) from a sale price of about USD 70.8M")

    def test_chosen_value_shows_the_waterfall(self):
        page = self.client.get(f"/companies/{self.co.pk}/", {"exit": "50M"})
        self.assertContains(page, "Sale for USD 50M: who receives what")
        self.assertContains(page, "Series B Preferred")
        self.assertContains(page, "Options and warrants")
        self.assertContains(page, "USD 1.64M")                       # our proceeds at 50M
        self.assertContains(page, "0.64×")

    def test_bad_input_is_explained(self):
        self.assertContains(self.client.get(f"/companies/{self.co.pk}/", {"exit": "lots"}), "Enter an amount such as")

    def test_no_card_without_our_shares(self):
        from core.models import Company

        other = Company.objects.create(name="Empty", status="portfolio")
        self.assertNotContains(self.client.get(f"/companies/{other.pk}/"), "If the company is sold")

    def test_parse(self):
        from core.services.exit import parse_exit_value

        for text, value in [("100M", 100_000_000), ("1.5bn", 1_500_000_000), ("120'000'000", 120_000_000),
                            ("250k", 250_000), (" 90 m ", 90_000_000)]:
            self.assertEqual(parse_exit_value(text), D(value), text)
        for bad in ["", "abc", "-5", "0", "1e99", None]:
            self.assertIsNone(parse_exit_value(bad), bad)
