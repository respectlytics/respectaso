"""Tests for the in-app What's New page and the release-notes contract.

The single most important test here is the RELEASE GATE:
`test_newest_entry_matches_app_version`. Bumping VERSION without adding
that release's entry to aso/release_notes.py fails the suite, and the
release checklist requires a green suite - so a release can never ship
without updated notes.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from aso import release_notes


class ReleaseGateTest(TestCase):
    def test_newest_entry_matches_app_version(self):
        """RELEASE GATE: the newest release-notes entry MUST be the running
        version. If this fails you bumped VERSION without writing the
        release's notes - add the entry to aso/release_notes.py."""
        self.assertEqual(
            release_notes.RELEASES[0]["version"], settings.VERSION,
            "\n\nAdd a release-notes entry for v"
            f"{settings.VERSION} to aso/release_notes.py before releasing. "
            "A release must never ship without updated What's New notes.",
        )

    def test_entries_are_well_formed_and_descending(self):
        seen = set()
        versions = []
        for entry in release_notes.RELEASES:
            for key in ("version", "date", "title", "kind", "sections"):
                self.assertIn(key, entry, entry.get("version"))
            self.assertIn(entry["kind"], ("feature", "patch"))
            self.assertNotIn(entry["version"], seen)
            seen.add(entry["version"])
            versions.append(tuple(int(x) for x in entry["version"].split(".")))
            self.assertRegex(entry["date"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(entry["title"].strip())
        self.assertEqual(versions, sorted(versions, reverse=True))

    def test_check_command_passes_and_markdown_roundtrips(self):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("release_notes", "--check", stdout=out)
        self.assertIn("OK", out.getvalue())

        out = StringIO()
        call_command("release_notes", "--markdown", stdout=out)
        markdown = out.getvalue()
        self.assertTrue(markdown.strip())
        # No leftover inline HTML in the GitHub body.
        self.assertNotIn("<strong>", markdown)
        self.assertNotIn("&amp;", markdown)


class WhatsNewPageTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def test_page_lists_releases_and_marks_seen(self):
        response = self.client.get(reverse("aso:whats_new"))
        self.assertContains(response, "What&#x27;s New")
        self.assertContains(response, f"v{settings.VERSION}")
        from django.utils.html import escape

        self.assertContains(
            response, escape(release_notes.RELEASES[0]["title"][:30])
        )
        # Opening the page clears the update notice.
        self.assertEqual(release_notes.get_last_seen_version(), settings.VERSION)

    def test_dismiss_endpoint_marks_seen(self):
        response = self.client.post(reverse("aso:whats_new_seen"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(release_notes.get_last_seen_version(), settings.VERSION)

    def test_footer_links_to_whats_new(self):
        response = self.client.get(reverse("aso:methodology"))
        self.assertContains(response, reverse("aso:whats_new"))


class UpdateNoticeTest(TestCase):
    """The one-time notice is tiered: minor/major updates show it once,
    patch updates and fresh installs never do."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def _existing_install(self):
        # settings.json marks an install that has been used before.
        (Path(self._tmp.name) / "settings.json").write_text("{}")

    def test_fresh_install_never_notified(self):
        self.assertFalse(release_notes.should_show_notice())
        # State recorded so a later patch update stays silent too.
        self.assertEqual(release_notes.get_last_seen_version(), settings.VERSION)

    def test_minor_update_shows_notice_until_seen(self):
        self._existing_install()
        release_notes.mark_seen("1.0.0")
        self.assertTrue(release_notes.should_show_notice())
        self.assertTrue(release_notes.should_show_notice())  # persists
        release_notes.mark_seen()
        self.assertFalse(release_notes.should_show_notice())

    def test_patch_update_absorbed_silently(self):
        self._existing_install()
        maj, minor, _ = (int(x) for x in settings.VERSION.split("."))
        release_notes.mark_seen(f"{maj}.{minor}.0")
        # Same minor -> silent (unless the entry explicitly overrides).
        if release_notes.latest().get("notice") is None:
            self.assertFalse(release_notes.should_show_notice())
            self.assertEqual(
                release_notes.get_last_seen_version(), settings.VERSION
            )

    def test_pre_feature_upgrade_shows_notice(self):
        """Existing install with no state file (upgraded from before this
        feature existed) gets the notice."""
        self._existing_install()
        self.assertTrue(release_notes.should_show_notice())

    def test_notice_renders_in_base_template(self):
        self._existing_install()
        release_notes.mark_seen("1.0.0")
        response = self.client.get(reverse("aso:methodology"))
        self.assertContains(response, "whats-new-notice")
        self.assertContains(response, "see what")
