"""Login with a second factor is required for every page, admin included."""

from io import StringIO

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from core.models import Company
from core.roles import assign_role, role_of


class TwoFactorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("giovanni", "g@example.com", "a-long-test-password")

    def verified_login(self):
        device = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.client.force_login(self.user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return device

    def test_health_check_is_public(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_anonymous_user_is_sent_to_login(self):
        for url in ["/", "/admin/", "/admin/core/company/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertIn("/account/login/", response["Location"])

    def test_password_only_session_without_device_must_set_up_2fa(self):
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertRedirects(response, "/account/two_factor/setup/", fetch_redirect_response=False)
        self.assertEqual(self.client.get("/account/two_factor/setup/").status_code, 200)

    def test_password_only_session_with_device_must_enter_code(self):
        TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        self.client.force_login(self.user)
        for url in ["/", "/admin/"]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/account/login/", response["Location"])

    def test_verified_session_reaches_home_and_admin(self):
        self.verified_login()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/admin/core/company/add/").status_code, 200)

    def test_full_login_flow_with_totp_code(self):
        device = TOTPDevice.objects.create(user=self.user, name="default", confirmed=True)
        response = self.client.post("/account/login/", {
            "auth-username": "giovanni", "auth-password": "a-long-test-password",
            "login_view-current_step": "auth",
        })
        self.assertEqual(response.status_code, 200)  # asks for the token
        code = str(totp(device.bin_key, device.step, device.t0, device.digits, device.drift)).zfill(6)
        response = self.client.post("/account/login/", {
            "token-otp_token": code, "login_view-current_step": "token",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_interface_is_in_english_even_for_italian_browsers(self):
        self.verified_login()
        response = self.client.get("/admin/core/", HTTP_ACCEPT_LANGUAGE="it-CH,it;q=0.9")
        self.assertContains(response, "Companies")
        self.assertNotContains(response, "Società")
        self.assertEqual(response["Content-Language"], "en")


class RoleTests(TestCase):
    def test_viewer_group_is_read_only(self):
        viewer = Group.objects.get(name="viewer")
        codenames = set(viewer.permissions.values_list("codename", flat=True))
        self.assertIn("view_company", codenames)
        self.assertIn("view_investment", codenames)
        self.assertFalse({c for c in codenames if not c.startswith("view_")})

    def test_editor_group_can_change_data(self):
        editor = Group.objects.get(name="editor")
        self.assertTrue(editor.permissions.filter(codename="change_investment").exists())


def verified_client(client, user):
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


class MultiUserTests(TestCase):
    def make_user(self, name, role):
        user = User.objects.create_user(name, f"{name}@example.com", "a-long-test-password")
        assign_role(user, role)
        return user

    def test_viewer_can_read_but_not_change(self):
        company = Company.objects.create(name="Alpha")
        client = verified_client(self.client, self.make_user("anna", "viewer"))
        self.assertEqual(client.get("/admin/core/company/").status_code, 200)
        self.assertEqual(client.get(f"/admin/core/company/{company.pk}/change/").status_code, 200)
        self.assertEqual(client.get("/admin/core/company/add/").status_code, 403)
        response = client.post(f"/admin/core/company/{company.pk}/change/", {"name": "Hacked", "status": "prospect"})
        self.assertEqual(response.status_code, 403)
        company.refresh_from_db()
        self.assertEqual(company.name, "Alpha")
        self.assertEqual(client.get("/admin/auth/user/").status_code, 403)

    def test_editor_can_change_data_but_not_users(self):
        client = verified_client(self.client, self.make_user("marc", "editor"))
        response = client.post("/admin/core/company/add/", {
            "name": "Beta", "status": "prospect", "board_role": "none",
            **{f"{prefix}-{k}": v for prefix in ["share_classes", "share_events", "option_pools",
                                                  "valuations", "metrics", "milestones"]
               for k, v in {"TOTAL_FORMS": "0", "INITIAL_FORMS": "0"}.items()},
        })
        self.assertEqual(response.status_code, 302, getattr(response, "context", None) and
                         response.context["adminform"].form.errors)
        self.assertTrue(Company.objects.filter(name="Beta").exists())
        self.assertEqual(client.get("/admin/auth/user/").status_code, 403)

    def test_roles_switch_cleanly(self):
        user = self.make_user("lea", "viewer")
        assign_role(user, "editor")
        self.assertEqual(role_of(user), "editor")
        self.assertEqual(list(user.groups.values_list("name", flat=True)), ["editor"])
        assign_role(user, "admin")
        self.assertEqual(role_of(user), "admin")
        self.assertTrue(user.is_superuser and user.is_staff)

    def test_adduser_command(self):
        out = StringIO()
        call_command("adduser", "paul", "--email", "paul@example.com", "--role", "editor", stdout=out)
        user = User.objects.get(username="paul")
        self.assertEqual(role_of(user), "editor")
        password = out.getvalue().split("One-time password: ")[1].split()[0]
        self.assertTrue(user.check_password(password))

    def test_new_user_must_set_up_two_factor(self):
        self.client.force_login(self.make_user("nina", "viewer"))
        self.assertRedirects(self.client.get("/"), "/account/two_factor/setup/", fetch_redirect_response=False)

    def test_admin_can_reset_lost_two_factor_device(self):
        admin = self.make_user("boss", "admin")
        lost = self.make_user("lost", "viewer")
        TOTPDevice.objects.create(user=lost, name="default", confirmed=True)
        client = verified_client(self.client, admin)
        response = client.post("/admin/auth/user/", {"action": "reset_two_factor", "_selected_action": [lost.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TOTPDevice.objects.filter(user=lost).exists())
        self.assertTrue(TOTPDevice.objects.filter(user=admin).exists())


class MinimalInterfaceTests(TestCase):
    def setUp(self):
        user = User.objects.create_superuser("g", "g@example.com", "a-long-test-password")
        verified_client(self.client, user)

    def test_secondary_fields_are_collapsed(self):
        company = Company.objects.create(name="Alpha", city="Geneva")
        html = self.client.get(f"/admin/core/company/{company.pk}/change/").content.decode()
        self.assertIn("More details", html)
        essential, more = html.split("More details", 1)
        self.assertIn('name="name"', essential)
        self.assertIn('name="city"', more)
        self.assertNotIn('name="city"', essential)
        self.assertIn("<details>", html)

    def test_menu_starts_with_daily_records(self):
        html = self.client.get("/admin/").content.decode()
        positions = [html.index(f"/admin/{path}/") for path in
                     ["core/company", "core/deal", "core/investment", "core/fxrate", "auth/user"]]
        self.assertEqual(positions, sorted(positions))


class SingleFundTests(TestCase):
    def setUp(self):
        from core.models import InvestingEntity

        self.fund = InvestingEntity.objects.create(name="Lake Growth Ventures", kind="fund")
        verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))

    def test_single_fund_is_preselected_and_out_of_the_way(self):
        html = self.client.get("/admin/core/investment/add/").content.decode()
        essential, more = html.split("More details", 1)
        self.assertNotIn('name="entity"', essential)
        self.assertIn(f'<option value="{self.fund.pk}" selected>', more)
        self.assertNotIn("column-entity", self.client.get("/admin/core/investment/").content.decode())

    def test_entity_field_returns_with_a_second_vehicle(self):
        from core.models import InvestingEntity

        InvestingEntity.objects.create(name="LGV Co-Invest", kind="fund")
        essential = self.client.get("/admin/core/investment/add/").content.decode().split("More details", 1)[0]
        self.assertIn('name="entity"', essential)

    def test_site_name_is_configurable(self):
        from django.test import override_settings

        with override_settings(SITE_NAME="Lake Growth Ventures"):
            self.assertContains(self.client.get("/"), "<title>Lake Growth Ventures</title>")


def _totp_now(b32_secret):
    import base64
    import hashlib
    import hmac
    import struct
    import time

    key = base64.b32decode(b32_secret)
    digest = hmac.new(key, struct.pack(">Q", int(time.time()) // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    return str((struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


class TwoFactorSetupFlowTests(TestCase):
    """The first-time 2FA setup must survive the browser's own background requests."""

    def setUp(self):
        import re

        self.re = re
        User.objects.create_superuser("giovanni", "g@example.com", "a-long-test-password")
        self.client.post("/account/login/", {"auth-username": "giovanni", "auth-password": "a-long-test-password",
                                             "login_view-current_step": "auth"})

    def start_setup(self):
        self.assertEqual(self.client.get("/account/two_factor/setup/").status_code, 200)
        page = self.client.post("/account/two_factor/setup/", {"setup_view-current_step": "welcome"})
        return self.re.search(r"TOTP Secret:.*?>([A-Z2-7]+)<", page.content.decode(), self.re.S).group(1), page

    def finish_setup(self, secret):
        return self.client.post("/account/two_factor/setup/", {
            "setup_view-current_step": "generator", "generator-token": _totp_now(secret)})

    def test_setup_completes(self):
        secret, _page = self.start_setup()
        response = self.finish_setup(secret)
        self.assertRedirects(response, "/account/two_factor/setup/complete/", fetch_redirect_response=False)
        self.assertTrue(TOTPDevice.objects.filter(user__username="giovanni", confirmed=True).exists())
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_background_requests_do_not_restart_the_wizard(self):
        secret, _page = self.start_setup()
        # What browsers do on their own while the QR code is on screen.
        favicon = self.client.get("/favicon.ico")
        self.assertEqual(favicon.status_code, 301)
        self.assertTrue(favicon["Location"].startswith("/static/"))
        self.assertEqual(self.client.get("/apple-touch-icon.png", HTTP_SEC_FETCH_DEST="image").status_code, 403)
        self.assertEqual(self.client.get("/admin/", HTTP_SEC_FETCH_DEST="script").status_code, 403)
        response = self.finish_setup(secret)
        self.assertRedirects(response, "/account/two_factor/setup/complete/", fetch_redirect_response=False)

    def test_authenticator_shows_the_fund_name(self):
        from django.test import override_settings

        with override_settings(SITE_NAME="Lake Growth Ventures"):
            _secret, page = self.start_setup()
            self.assertContains(page, "issuer=Lake+Growth+Ventures")
            self.assertNotContains(page, "issuer=testserver")
            qr = self.client.get("/account/two_factor/qrcode/")
            self.assertEqual(qr.status_code, 200)
            self.assertEqual(qr["Content-Type"].split(";")[0], "image/svg+xml")


class BackgroundRequestTests(TestCase):
    def test_page_navigation_is_redirected_to_login(self):
        response = self.client.get("/admin/", HTTP_SEC_FETCH_DEST="document")
        self.assertEqual(response.status_code, 302)

    def test_htmx_request_gets_client_side_redirect(self):
        response = self.client.get("/", HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertTrue(response["HX-Redirect"].startswith("/account/login/"))
