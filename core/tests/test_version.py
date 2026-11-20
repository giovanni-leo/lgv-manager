"""The running version is shown in every page and in /healthz."""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from core import version

from .test_auth import verified_client


class VersionTests(TestCase):
    def setUp(self):
        version.build_info.cache_clear()
        self.addCleanup(version.build_info.cache_clear)

    def with_dir(self, files):
        folder = tempfile.mkdtemp()
        for name, text in files.items():
            (Path(folder) / name).write_text(text)
        return mock.patch.object(version, "BASE_DIR", Path(folder))

    def test_build_info_file(self):
        with self.with_dir({"VERSION": "1.4\n", "build_info.json": json.dumps(
                {"count": "151", "commit": "b9a1d70", "date": "2026-11-21"})}):
            self.assertEqual(version.version_string(), "v1.4.151 (b9a1d70, 2026-11-21)")

    def test_without_build_information(self):
        with self.with_dir({"VERSION": "1.0"}), mock.patch.object(version, "_git", return_value=None):
            self.assertEqual(version.version_string(), "v1.0")

    def test_falls_back_to_git(self):
        answers = {("rev-list", "--count", "HEAD"): "7", ("rev-parse", "--short", "HEAD"): "abc1234",
                   ("log", "-1", "--format=%cs"): "2026-01-02", ("status", "--porcelain"): "M x"}
        with self.with_dir({"VERSION": "2.0"}), mock.patch.object(version, "_git", side_effect=lambda *a: answers[a]):
            self.assertEqual(version.version_string(), "v2.0.7 (abc1234+, 2026-01-02)")   # + = uncommitted changes

    def test_shown_in_pages_and_healthz(self):
        version.build_info.cache_clear()
        with self.with_dir({"VERSION": "1.0", "build_info.json": json.dumps(
                {"count": "9", "commit": "deadbee", "date": "2026-11-21"})}):
            self.assertEqual(self.client.get("/healthz").json()["version"], "v1.0.9 (deadbee, 2026-11-21)")
            verified_client(self.client, User.objects.create_superuser("g", "g@example.com", "a-long-test-password"))
            for url in ("/", "/admin/", "/manual/"):
                self.assertContains(self.client.get(url), "v1.0.9 (deadbee, 2026-11-21)", msg_prefix=url)
