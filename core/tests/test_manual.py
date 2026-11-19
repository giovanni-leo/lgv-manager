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
