"""Percentages of ownership: per class, basic as-converted and fully diluted."""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase

from core.models import Investment
from core.services.ownership import consistency_warnings, ownership_by_class, ownership_company

from . import factories as f

D = date


def by_name(rows):
    return {r.share_class: r for r in rows}


class OwnershipTests(TestCase):
    """Reference cap table.

    Common:    800,000 issued 2022-01-10 (ratio 1)
    Series A:  200,000 issued 2023-03-01 (ratio 1.5)
    The fund buys 50,000 Series A on 2023-03-01.
    """

    @classmethod
    def setUpTestData(cls):
        cls.fund = f.entity()
        cls.coinvest = f.entity("LGV Co-Invest", "fund")
        cls.co = f.company()
        cls.common = f.share_class(cls.co, "Common", "1", common=True)
        cls.series_a = f.share_class(cls.co, "Series A", "1.5", seniority=1)
        f.issue(cls.common, 800_000, D(2022, 1, 10))
        f.issue(cls.series_a, 200_000, D(2023, 3, 1))
        f.buy(cls.fund, cls.series_a, 50_000, D(2023, 3, 1), "150000")

    def classes(self, as_of=D(2024, 6, 1), entity=None):
        return by_name(ownership_by_class(self.co.pk, as_of, entity.pk if entity else None))

    def test_pct_of_single_class(self):
        rows = self.classes()
        self.assertEqual(rows["Series A"].shares_outstanding, 200_000)
        self.assertEqual(rows["Series A"].my_shares, 50_000)
        self.assertEqual(rows["Series A"].pct_of_class, Decimal("25.0000"))
        self.assertEqual(rows["Common"].my_shares, 0)
        self.assertEqual(rows["Common"].pct_of_class, Decimal("0.0000"))

    def test_basic_as_converted_uses_conversion_ratio(self):
        total = ownership_company(self.co.pk, D(2024, 6, 1))
        self.assertEqual(total.total_as_converted, Decimal("1100000"))  # 800k + 200k × 1.5
        self.assertEqual(total.my_as_converted, Decimal("75000"))      # 50k × 1.5
        self.assertEqual(total.pct_basic, Decimal("6.8182"))
        # Without a pool fully diluted equals basic.
        self.assertEqual(total.pct_fully_diluted, Decimal("6.8182"))

    def test_fully_diluted_uses_latest_pool_at_date(self):
        f.pool(self.co, 50_000, D(2022, 6, 1))
        f.pool(self.co, 100_000, D(2024, 1, 1), warrants=20_000, granted=60_000)

        later = ownership_company(self.co.pk, D(2024, 6, 1))
        self.assertEqual(later.option_pool, 100_000)
        self.assertEqual(later.warrants, 20_000)
        self.assertEqual(later.fully_diluted_total, Decimal("1220000"))
        self.assertEqual(later.pct_fully_diluted, Decimal("6.1475"))  # 75k / 1.22M
        self.assertEqual(later.pct_basic, Decimal("6.8182"))

        earlier = ownership_company(self.co.pk, D(2023, 6, 1))
        self.assertEqual(earlier.option_pool, 50_000)
        self.assertEqual(earlier.pct_fully_diluted, Decimal("6.5217"))  # 75k / 1.15M

    def test_my_warrants_count_in_fully_diluted_numerator(self):
        f.pool(self.co, 100_000, D(2024, 1, 1), warrants=20_000)
        Investment.objects.create(company=self.co, entity=self.fund, invest_date=D(2024, 1, 1),
                                  instrument="warrant", shares=10_000, amount=Decimal("0"))
        total = ownership_company(self.co.pk, D(2024, 6, 1))
        self.assertEqual(total.my_warrants, 10_000)
        self.assertEqual(total.pct_fully_diluted, Decimal("6.9672"))  # 85k / 1.22M
        self.assertEqual(total.pct_basic, Decimal("6.8182"))           # warrants not in basic

    def test_sale_reduces_my_shares_from_its_date(self):
        f.sell(self.fund, self.series_a, 10_000, D(2024, 3, 1), "45000")
        self.assertEqual(self.classes(D(2024, 2, 29))["Series A"].pct_of_class, Decimal("25.0000"))
        after = self.classes(D(2024, 3, 1))["Series A"]
        self.assertEqual(after.my_shares, 40_000)
        self.assertEqual(after.pct_of_class, Decimal("20.0000"))
        self.assertEqual(ownership_company(self.co.pk, D(2024, 3, 1)).pct_basic, Decimal("5.4545"))

    def test_as_of_date_excludes_later_events(self):
        rows = self.classes(D(2022, 12, 31))
        self.assertEqual(rows["Common"].shares_outstanding, 800_000)
        self.assertEqual(rows["Series A"].shares_outstanding, 0)
        self.assertEqual(rows["Series A"].my_shares, 0)
        self.assertIsNone(rows["Series A"].pct_of_class)  # division by zero -> NULL
        self.assertEqual(ownership_company(self.co.pk, D(2022, 12, 31)).pct_basic, Decimal("0.0000"))

    def test_split_applies_to_class_total_and_my_shares(self):
        f.buy(self.coinvest, self.common, 20_000, D(2023, 1, 1))
        event = f.split(self.common, "2", D(2024, 9, 1))
        event.refresh_from_db()
        self.assertEqual(event.shares_delta, 800_000)  # informative delta filled in automatically

        # Shares issued and bought on the split date are already post-split.
        f.issue(self.common, 1_000, D(2024, 9, 1))
        f.buy(self.coinvest, self.common, 1_000, D(2024, 9, 1))

        before = self.classes(D(2024, 8, 31))["Common"]
        self.assertEqual((before.shares_outstanding, before.my_shares), (800_000, 20_000))
        after = self.classes(D(2024, 9, 1))["Common"]
        self.assertEqual(after.shares_outstanding, 1_601_000)
        self.assertEqual(after.my_shares, 41_000)
        self.assertEqual(after.pct_of_class, Decimal("2.5609"))
        # Series A is not affected by a split of the common shares.
        self.assertEqual(self.classes(D(2024, 9, 1))["Series A"].shares_outstanding, 200_000)

    def test_reverse_split_and_sale_after_split(self):
        f.split(self.series_a, "0.1", D(2025, 1, 1))  # 1-for-10
        rows = self.classes(D(2025, 1, 1))
        self.assertEqual(rows["Series A"].shares_outstanding, 20_000)
        self.assertEqual(rows["Series A"].my_shares, 5_000)
        self.assertEqual(rows["Series A"].pct_of_class, Decimal("25.0000"))

        f.sell(self.fund, self.series_a, 1_000, D(2025, 2, 1))  # post-split shares
        self.assertEqual(self.classes(D(2025, 2, 1))["Series A"].my_shares, 4_000)
        # Ownership of the company is unchanged by the split itself.
        self.assertEqual(ownership_company(self.co.pk, D(2025, 1, 1)).pct_basic, Decimal("0.9036"))
        # 5,000 × 1.5 / (800,000 + 20,000 × 1.5) = 7,500 / 830,000

    def test_two_splits_compound(self):
        f.split(self.common, "2", D(2024, 1, 1))
        f.split(self.common, "3", D(2025, 1, 1))
        self.assertEqual(self.classes(D(2025, 1, 1))["Common"].shares_outstanding, 4_800_000)
        self.assertEqual(self.classes(D(2024, 6, 1))["Common"].shares_outstanding, 1_600_000)

    def test_conversion_events_move_shares_between_classes(self):
        f.issue(self.series_a, -50_000, D(2024, 5, 1), "conversion_out")
        f.issue(self.common, 75_000, D(2024, 5, 1), "conversion_in")
        rows = self.classes(D(2024, 5, 1))
        self.assertEqual(rows["Series A"].shares_outstanding, 150_000)
        self.assertEqual(rows["Common"].shares_outstanding, 875_000)
        # As-converted total is unchanged by a conversion at the conversion ratio.
        self.assertEqual(ownership_company(self.co.pk, D(2024, 5, 1)).total_as_converted, Decimal("1100000"))

    def test_totals_per_entity(self):
        f.buy(self.coinvest, self.common, 22_000, D(2023, 1, 1))
        fund = self.classes(entity=self.fund)
        mine = self.classes(entity=self.coinvest)
        self.assertEqual((fund["Series A"].my_shares, fund["Common"].my_shares), (50_000, 0))
        self.assertEqual((mine["Series A"].my_shares, mine["Common"].my_shares), (0, 22_000))
        self.assertEqual(ownership_company(self.co.pk, D(2024, 6, 1), self.coinvest.pk).pct_basic, Decimal("2.0000"))
        self.assertEqual(ownership_company(self.co.pk, D(2024, 6, 1)).pct_basic, Decimal("8.8182"))

    def test_flags_my_shares_above_class_total(self):
        f.buy(self.coinvest, self.series_a, 160_000, D(2023, 4, 1))
        row = self.classes()["Series A"]
        self.assertTrue(row.inconsistent)
        self.assertFalse(row.missing_issuance)
        self.assertIn(row, consistency_warnings(self.co.pk, D(2024, 6, 1)))
        # The check uses all entities even when the view is filtered by one.
        self.assertTrue(self.classes(entity=self.fund)["Series A"].inconsistent)

    def test_flags_missing_issuance_events(self):
        series_b = f.share_class(self.co, "Series B", "1", seniority=2)
        f.buy(self.fund, series_b, 10_000, D(2024, 1, 1))
        row = self.classes()["Series B"]
        self.assertTrue(row.missing_issuance)
        self.assertTrue(row.inconsistent)  # 10,000 held of 0 outstanding

    def test_flags_more_sold_than_held(self):
        f.sell(self.fund, self.series_a, 60_000, D(2024, 1, 1))
        self.assertTrue(self.classes()["Series A"].negative_position)

    def test_consistent_cap_table_has_no_warnings(self):
        self.assertEqual(consistency_warnings(self.co.pk, D(2024, 6, 1)), [])

    def test_reference_views_match_functions_today(self):
        f.pool(self.co, 100_000, D(2024, 1, 1), warrants=20_000)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pct_basic, pct_fully_diluted FROM v_ownership_company WHERE company_id = %s",
                           [self.co.pk])
            self.assertEqual(cursor.fetchone(), (Decimal("6.8182"), Decimal("6.1475")))
            cursor.execute("SELECT share_class, shares_outstanding, my_shares, pct_of_class "
                           "FROM v_ownership_by_class WHERE company_id = %s ORDER BY share_class", [self.co.pk])
            self.assertEqual(cursor.fetchall(), [
                ("Common", Decimal("800000"), Decimal("0"), Decimal("0.0000")),
                ("Series A", Decimal("200000"), Decimal("50000"), Decimal("25.0000")),
            ])
            cursor.execute("SELECT my_shares FROM v_my_shares WHERE share_class_id = %s", [self.series_a.pk])
            self.assertEqual(cursor.fetchone()[0], 50_000)


class MultiCompanyTests(TestCase):
    def test_companies_are_independent(self):
        fund = f.entity()
        a, b = f.company("Alpha"), f.company("Beta")
        ca, cb = f.share_class(a, "Common", common=True), f.share_class(b, "Common", common=True)
        f.issue(ca, 1_000, D(2024, 1, 1))
        f.issue(cb, 4_000, D(2024, 1, 1))
        f.buy(fund, ca, 100, D(2024, 1, 1))
        f.buy(fund, cb, 100, D(2024, 1, 1))
        result = {r.name: r.pct_basic for r in ownership_company(as_of=D(2024, 2, 1))}
        self.assertEqual(result, {"Alpha": Decimal("10.0000"), "Beta": Decimal("2.5000")})
