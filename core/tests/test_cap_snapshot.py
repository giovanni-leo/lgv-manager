"""Cap table from financing documents (figures of Field Medical's Series A and Series B)."""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.ai import claude, jobs
from core.ai.apply import SuggestionError, decide
from core.ai.schemas import CapTableReading, CapTableSnapshot, ClassSnapshot, HoldingSnapshot, InvestorRef
from core.models import (AiJob, Company, FundingRound, InvestingEntity, Investment, OptionPool, ShareClass,
                         ShareEvent, Suggestion, Valuation)
from core.services.cap_snapshot import apply_snapshot, match_entity
from core.services.ownership import ownership_by_class, ownership_company

from .test_auth import verified_client
from .test_documents import make_document, tiny_pdf

D = date


def klass(name, shares, price=None, rank=None, common=False):
    return dict(name=name, is_common=common, shares_outstanding=shares, original_issue_price=price,
                liquidation_multiple=None if common else 1, participating=None if common else False,
                seniority_rank=rank, conversion_ratio=1)


SERIES_A = dict(
    round_name="Series A", closing_date="2025-01-08", currency="USD", option_pool_total=651920, option_pool_granted=281920,
    warrants=67239, fully_diluted_total=6307994, round_class_name="Series A Preferred", round_price_per_share=10.77,
    round_amount_raised=None, pre_money=None, post_money=None, lead_investors=[], other_investors=[],
    classes=[klass("Common", 1681464, common=True), klass("Series A-1 Preferred", 3234860, 6.43, 1),
             klass("Series A Preferred", 672511, 10.77, 1)],
    our_holdings=[], evidence="Disclosure schedule 2.2(c)", caveats=None)

SERIES_B = dict(
    round_name="Series B", closing_date="2025-06-20", currency="USD", option_pool_total=1131431, option_pool_granted=605687,
    warrants=67008, fully_diluted_total=10200569, round_class_name="Series B Preferred",
    round_price_per_share=13.724719, round_amount_raised=29999943.71, pre_money=None, post_money=None,
    lead_investors=[dict(name="BioStar Ventures", kind="vc"), dict(name="CUE Growth", kind="vc")], other_investors=[],
    classes=[klass("Common", 1724623, common=True), klass("Series A-1 Preferred", 3234860, 6.43, 2),
             klass("Series A Preferred", 1856814, 10.77, 2), klass("Series B Preferred", 2185833, 13.724719, 1)],
    our_holdings=[dict(investor_name="Efficient Frontier Holding SA", class_name="Series B Preferred",
                       shares=56278, price_per_share=13.724719, amount=772399.74)],
    evidence="Disclosure schedule 2.2(c), schedule of purchasers", caveats=None)


class ApplySnapshotTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Field Medical")
        self.ef = InvestingEntity.objects.create(name="Efficient Frontier Holding SA", kind="company")
        InvestingEntity.objects.create(name="Lake Growth Ventures", kind="fund")

    def test_series_b_from_scratch(self):
        apply_snapshot(self.co, SERIES_B)
        rows = {r.share_class: r for r in ownership_by_class(self.co.pk, D(2025, 6, 21))}
        self.assertEqual({k: int(v.shares_outstanding) for k, v in rows.items()},
                         {"Common": 1724623, "Series A Preferred": 1856814, "Series A-1 Preferred": 3234860,
                          "Series B Preferred": 2185833})
        total = ownership_company(self.co.pk, D(2025, 6, 21))
        self.assertEqual(int(total.fully_diluted_total), 10200569)
        self.assertEqual(total.pct_fully_diluted.quantize(Decimal("0.0001")), Decimal("0.5517"))   # 56,278 / FD
        b = ShareClass.objects.get(name="Series B Preferred")
        a, a1, common = (ShareClass.objects.get(name=n) for n in ("Series A Preferred", "Series A-1 Preferred", "Common"))
        self.assertGreater(b.seniority, a.seniority)                 # B is paid first
        self.assertEqual(a.seniority, a1.seniority)                  # A and A-1 pari passu
        self.assertEqual((common.seniority, common.is_common), (0, True))
        self.assertEqual((b.liquidation_pref_mult, b.participating), (Decimal(1), False))
        inv = Investment.objects.get()
        self.assertEqual((inv.entity, inv.share_class, inv.shares, inv.amount, inv.invest_date),
                         (self.ef, b, 56278, Decimal("772399.74"), D(2025, 6, 20)))
        self.co.refresh_from_db()
        self.assertEqual(self.co.status, "portfolio")
        v = Valuation.objects.get()
        self.assertEqual((v.valuation_date, v.share_class, v.price_per_share), (D(2025, 6, 20), b, Decimal("13.724719")))
        self.assertAlmostEqual(float(v.post_money), 13.724719 * 10200569, delta=1)
        r = FundingRound.objects.get()
        self.assertEqual((r.round_name, r.amount), ("Series B", Decimal("29999943.71")))
        self.assertAlmostEqual(float(r.pre_money), float(r.post_money) - 29999943.71, delta=1)
        self.assertEqual(sorted(i.name for i in r.leads), ["BioStar Ventures", "CUE Growth"])
        pool = OptionPool.objects.get()
        self.assertEqual((pool.authorized, pool.granted, pool.warrants), (1131431, 605687, 67008))

    def test_second_closing_records_only_the_difference(self):
        apply_snapshot(self.co, SERIES_A)
        self.assertEqual(ShareEvent.objects.filter(share_class__name="Series A Preferred").count(), 1)
        apply_snapshot(self.co, SERIES_B)
        events = {(e.share_class.name, e.event_date): e.shares_delta for e in ShareEvent.objects.all()}
        self.assertEqual(events[("Common", D(2025, 6, 20))], 43159)
        self.assertEqual(events[("Series A Preferred", D(2025, 6, 20))], 1184303)
        self.assertNotIn(("Series A-1 Preferred", D(2025, 6, 20)), events)            # unchanged
        self.assertEqual(OptionPool.objects.count(), 2)
        self.assertEqual(int(ownership_company(self.co.pk, D(2025, 6, 21)).fully_diluted_total), 10200569)

    def test_applying_twice_changes_nothing(self):
        apply_snapshot(self.co, SERIES_B)
        counts = (ShareEvent.objects.count(), Investment.objects.count(), Valuation.objects.count(),
                  FundingRound.objects.count(), OptionPool.objects.count())
        apply_snapshot(self.co, SERIES_B)
        self.assertEqual((ShareEvent.objects.count(), Investment.objects.count(), Valuation.objects.count(),
                          FundingRound.objects.count(), OptionPool.objects.count()), counts)

    def test_existing_class_terms_are_not_overwritten(self):
        ShareClass.objects.create(company=self.co, name="series b preferred", seniority=9, liquidation_pref_mult=2,
                                  participating=True)
        apply_snapshot(self.co, SERIES_B)
        b = ShareClass.objects.get(name__iexact="Series B Preferred")
        self.assertEqual((b.seniority, b.liquidation_pref_mult, b.participating), (9, 2, True))
        self.assertEqual(b.original_issue_price, Decimal("13.724719"))                # only the empty price is filled
        self.assertEqual(ShareClass.objects.filter(name__iexact="Series B Preferred").count(), 1)

    def test_fewer_shares_in_documents_than_in_app_is_refused_and_rolled_back(self):
        apply_snapshot(self.co, SERIES_B)
        wrong = {**SERIES_B, "closing_date": "2025-07-01",
                 "classes": [klass("Common", 1000000, common=True)] + SERIES_B["classes"][1:]}
        before = ShareEvent.objects.count()
        with self.assertRaisesMessage(SuggestionError, "Common: the documents show 1,000,000"):
            apply_snapshot(self.co, wrong)
        self.assertEqual(ShareEvent.objects.count(), before)

    def test_fully_diluted_mismatch_is_refused(self):
        with self.assertRaisesMessage(SuggestionError, "fully diluted total is 10,200,569 but the documents state"):
            apply_snapshot(self.co, {**SERIES_B, "fully_diluted_total": 10000000})
        self.assertFalse(ShareClass.objects.exists())                                 # nothing half-done

    def test_unknown_entity_is_refused_when_several_exist(self):
        holdings = [dict(investor_name="Some Other Fund LP", class_name="Series B Preferred", shares=10,
                         price_per_share=None, amount=None)]
        with self.assertRaisesMessage(SuggestionError, "no investing entity with that name"):
            apply_snapshot(self.co, {**SERIES_B, "our_holdings": holdings})

    def test_entity_matching_ignores_legal_forms(self):
        entities = list(InvestingEntity.objects.all())
        self.assertEqual(match_entity("Efficient Frontier Holding", entities), self.ef)
        self.assertEqual(match_entity("EFFICIENT FRONTIER HOLDING S.A.", entities), self.ef)
        self.assertIsNone(match_entity("Frontier Bank", entities))
        self.assertIsNone(match_entity("", entities))


@override_settings(ANTHROPIC_API_KEY="x", AI_ENABLED=True)
class CapTableJobTests(TestCase):
    def setUp(self):
        self.co = Company.objects.create(name="Field Medical")
        InvestingEntity.objects.create(name="Efficient Frontier Holding SA", kind="company")
        self.user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.user)
        self.doc = make_document(self.co, "schedule.pdf", tiny_pdf("Capitalization"), title="Disclosure schedule",
                                 category="cap_table")

    def reading(self):
        return CapTableReading(snapshot=CapTableSnapshot(**{
            **SERIES_B,
            "classes": [ClassSnapshot(**c) for c in SERIES_B["classes"]],
            "lead_investors": [InvestorRef(**i) for i in SERIES_B["lead_investors"]],
            "our_holdings": [HoldingSnapshot(**h) for h in SERIES_B["our_holdings"]]}), note=None)

    def run_job(self):
        job = jobs.claim_next_job()
        with mock.patch.object(claude, "read_cap_table", return_value=(self.reading(), claude.Usage(50000, 3000), [])) as m:
            jobs.run_job(job)
        job.refresh_from_db()
        return job, m

    def test_page_lists_documents_and_queues_the_job(self):
        url = f"/companies/{self.co.pk}/cap-table/read/"
        self.assertContains(self.client.get(url), "Disclosure schedule")
        self.assertEqual(self.client.post(url, {}).status_code, 200)                 # nothing chosen: form again
        response = self.client.post(url, {"documents": [self.doc.pk]})
        self.assertRedirects(response, f"/companies/{self.co.pk}/", fetch_redirect_response=False)
        job = AiJob.objects.get()
        self.assertEqual((job.kind, job.params), ("cap_table", {"documents": [self.doc.pk]}))
        self.client.post(url, {"documents": [self.doc.pk]})
        self.assertEqual(AiJob.objects.count(), 1)                                    # not queued twice

    def test_documents_of_other_companies_cannot_be_chosen(self):
        other = make_document(Company.objects.create(name="Other"), "x.pdf", tiny_pdf("x"), title="Other doc")
        self.client.post(f"/companies/{self.co.pk}/cap-table/read/", {"documents": [other.pk]})
        self.assertFalse(AiJob.objects.exists())

    def test_job_makes_a_proposal_and_accepting_applies_it(self):
        self.client.post(f"/companies/{self.co.pk}/cap-table/read/", {"documents": [self.doc.pk]})
        job, call = self.run_job()
        self.assertEqual(job.status, "done")
        self.assertEqual(call.call_args.args[0:2], ("Field Medical", ["Efficient Frontier Holding SA"]))
        self.assertEqual(call.call_args.args[2][0]["title"], "Disclosure schedule")
        s = Suggestion.objects.get()
        self.assertEqual(s.target, "cap_table")
        self.assertIn("4 classes, 9,002,130 shares issued, 10,200,569 fully diluted, ours 56,278", s.label)
        self.assertFalse(ShareClass.objects.exists())                                 # nothing before Accept
        page = self.client.get(f"/companies/{self.co.pk}/")
        self.assertContains(page, "Show the proposed cap table")
        self.assertContains(page, "Series B Preferred")
        self.assertEqual(self.client.post(f"/suggestions/{s.pk}/accept/")["HX-Refresh"], "true")
        self.assertEqual(Investment.objects.get().shares, 56278)

    def test_problems_are_shown_on_the_card_not_half_applied(self):
        s = Suggestion.objects.create(
            job=AiJob.objects.create(kind="cap_table", company=self.co), company=self.co, target="cap_table",
            payload={**SERIES_B, "fully_diluted_total": 5}, label="Cap table")
        response = self.client.post(f"/suggestions/{s.pk}/accept/")
        self.assertContains(response, "fully diluted total is")
        self.assertFalse(ShareClass.objects.exists())
        s.refresh_from_db()
        self.assertEqual(s.status, "pending")

    def test_viewer_cannot_read_or_accept(self):
        from core.roles import assign_role

        viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(viewer, "viewer")
        verified_client(self.client, viewer)
        self.assertEqual(self.client.get(f"/companies/{self.co.pk}/cap-table/read/").status_code, 403)

    def test_buttons(self):
        Company.objects.filter(pk=self.co.pk).update(status="portfolio")
        ShareClass.objects.create(company=self.co, name="Common", is_common=True)
        self.assertContains(self.client.get(f"/companies/{self.co.pk}/"), "Read from documents")
