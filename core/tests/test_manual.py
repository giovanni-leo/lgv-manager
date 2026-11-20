"""The user manual page and the shared top menu (also on the Data section)."""

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from core.roles import assign_role

from .test_auth import verified_client


class ManualAndMenuTests(TestCase):
    def setUp(self):
        self.viewer = User.objects.create_user("v", "v@example.com", "a-long-test-password")
        assign_role(self.viewer, "viewer")

    def test_manual_requires_login(self):
        response = self.client.get("/manual/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/account/login/", response["Location"])

    def test_manual_page_for_every_role(self):
        verified_client(self.client, self.viewer)
        page = self.client.get("/manual/")
        self.assertContains(page, '<h2 id="1-first-login">1. First login</h2>', html=True)
        self.assertContains(page, "<table>")                                    # markdown tables rendered
        self.assertContains(page, 'href="#4-working-on-a-deal"')               # table of contents
        self.assertContains(page, '<a href="/manual/" aria-current="page">Manual</a>', html=True)

    def test_menu_links_to_manual(self):
        verified_client(self.client, self.viewer)
        self.assertContains(self.client.get("/"), '<a href="/manual/">Manual</a>', html=True)

    @override_settings(USER_MANUAL_URL="https://docs.example/guide")
    def test_external_manual(self):
        verified_client(self.client, self.viewer)
        self.assertContains(self.client.get("/"), 'href="https://docs.example/guide" target="_blank"')

    def test_data_section_has_the_app_menu(self):
        admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, admin)
        for url in ["/admin/", "/admin/core/company/"]:
            page = self.client.get(url)
            self.assertContains(page, 'class="topbar"')
            self.assertContains(page, '<a href="/admin/" aria-current="page">Data</a>', html=True)
            self.assertContains(page, '<a href="/manual/">Manual</a>', html=True)
            self.assertNotContains(page, "View site")
            self.assertNotContains(page, "Welcome,")
            self.assertNotContains(page, "theme-toggle")


class IsoDateTests(TestCase):
    """Dates are YYYY-MM-DD everywhere, whatever the browser's language."""

    def setUp(self):
        from core.models import Company

        self.admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, self.admin)
        self.company = Company.objects.create(name="NeuroPath SA")

    def test_date_fields_are_text_inputs_in_iso_format(self):
        for url, field in [(f"/companies/{self.company.pk}/rounds/new/", "round_date"), ("/deals/new/", "deadline")]:
            html = self.client.get(url).content.decode()
            tag = html[html.index(f'name="{field}"') - 200: html.index(f'name="{field}"') + 300]
            self.assertIn('placeholder="YYYY-MM-DD"', tag)
            self.assertNotIn('type="date"', tag)
        page = self.client.get(f"/companies/{self.company.pk}/").content.decode()
        self.assertNotIn('type="date"', page)
        self.assertIn('name="occurred_on"', page)
        self.assertIn('placeholder="YYYY-MM-DD"', page)

    def test_dates_are_shown_in_iso_format(self):
        from datetime import date

        from core.models import Interaction

        Interaction.objects.create(company=self.company, kind="note", summary="Board call", occurred_on=date(2026, 3, 7))
        page = self.client.get(f"/companies/{self.company.pk}/").content.decode()
        self.assertIn("2026-03-07", page)
        self.assertNotIn("7 Mar 2026", page)

    def test_typed_dates(self):
        from core.views_app import InteractionForm

        for text in ["2026-03-07", "07.03.2026"]:
            form = InteractionForm({"occurred_on": text, "kind": "note", "summary": "x"})
            self.assertTrue(form.is_valid(), text)
            self.assertEqual(str(form.cleaned_data["occurred_on"]), "2026-03-07")


class PeopleOrderTests(TestCase):
    def test_role_rank_order(self):
        from core.services.people import role_rank

        roles = ["CEO", "Chief Executive Officer & Co-founder", "CFO", "Chief Financial Officer", "COO", "CTO",
                 "Chief Medical Officer", "VP Engineering", "Chairman of the Board", "Chairperson", "Board member",
                 "Non-executive director", "Scientific advisor", "KOL advisor", None, "Receptionist"]
        ranks = [role_rank(r) for r in roles]
        self.assertEqual(ranks, [0, 0, 1, 1, 2, 2, 2, 3, 4, 4, 5, 5, 6, 6, 7, 7])
        self.assertEqual(role_rank("Chairman & CEO"), 0)

    def test_company_page_lists_ceo_cfo_c_board_then_advisors(self):
        from core.models import Company, Contact

        co = Company.objects.create(name="NeuroPath SA")
        for name, role in [("Zed Advisor", "Clinical advisor"), ("Ann Board", "Board member"), ("Bob CTO", "CTO"),
                           ("Cy Chair", "Chairman"), ("Di CFO", "CFO"), ("Eve CEO", "CEO"), ("Fay None", None)]:
            Contact.objects.create(company=co, full_name=name, role=role)
        admin = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, admin)
        page = self.client.get(f"/companies/{co.pk}/")
        order = [page.content.decode().index(n) for n in ("Eve CEO", "Di CFO", "Bob CTO", "Cy Chair", "Ann Board",
                                                          "Zed Advisor", "Fay None")]
        self.assertEqual(order, sorted(order))
