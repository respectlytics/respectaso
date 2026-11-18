"""Release notes never mention telemetry or our own analytics (the owner's global instructions,
2026-11-18): not how RespectASO counts users, installs or licences, not the update check, not what is
sent to us. They speak only of what the person gets. The store analytics the app shows a person about
their own apps (Sales & growth) is a feature and stays allowed."""

import re

from django.test import SimpleTestCase

from aso.release_notes import RELEASES

# Phrasings that would tell a reader what RespectASO itself measures or sends.
TELEMETRY = re.compile(
    r"telemetry|usage (data|statistics|stats)|anonymous (data|statistics|stats|usage)|crash report"
    r"|active (users?|copies|installs?|licen[cs]es?)|daily active|install(s|ations)? (are |is )?counted"
    r"|count(s|ing)? (users?|installs?|copies|licen[cs]es?|sessions?)|update check|checks? for (an )?updates?"
    r"|phones? home|(automatically|in the background|silently) sends?|our (own )?(analytics|dashboard|metrics)",
    re.IGNORECASE,
)


def _texts(entry):
    yield entry.get("title", "")
    for section in entry.get("sections", []):
        yield section.get("heading", "")
        yield from section.get("intro", [])
        yield from section.get("items", [])


class ReleaseNotesSayNothingAboutTelemetryTest(SimpleTestCase):
    def test_no_entry_mentions_telemetry_or_our_own_analytics(self):
        found = [(entry["version"], text) for entry in RELEASES[:5] for text in _texts(entry)
                 if TELEMETRY.search(re.sub(r"<[^>]+>", "", text))]
        self.assertEqual(found, [], "Release notes speak only of what the person gets")

    def test_the_guard_catches_the_shapes_and_keeps_the_feature(self):
        for bad in ("RespectASO now counts daily active users.", "The update check runs once a day.",
                    "Anonymous usage statistics help us improve.", "Fewer telemetry calls.",
                    "It automatically sends a report each day."):
            with self.subTest(bad=bad):
                self.assertTrue(TELEMETRY.search(bad))
        for good in ("Sales & growth shows what you earn.", "Store analytics for every app.",
                     "Numbers such as 1.5 read as you expect.",
                     "If it cannot start, it shows what to send us and gives you the buttons to do it."):
            with self.subTest(good=good):
                self.assertFalse(TELEMETRY.search(good))
