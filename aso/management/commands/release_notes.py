"""Emit release notes from aso/release_notes.py (the single source of truth).

Usage:
    python manage.py release_notes --markdown   # newest entry as GitHub markdown
    python manage.py release_notes --check      # newest entry matches VERSION?

The release pipeline uses --markdown to build the GitHub release body, so
the in-app What's New page and the GitHub release can never drift apart.
--check exits non-zero when the newest entry's version differs from
core.settings.VERSION (the same rule test_whats_new enforces in the suite).
"""

import html
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from aso.release_notes import latest


def _html_to_markdown(text: str) -> str:
    """Invert the minimal inline HTML the entries carry back to markdown."""
    s = text
    s = re.sub(r"<strong>(.*?)</strong>", r"**\1**", s)
    s = re.sub(r"<em>(.*?)</em>", r"*\1*", s)
    s = re.sub(r"<code>(.*?)</code>", r"`\1`", s)
    s = re.sub(r'<a href="([^"]+)"[^>]*>(.*?)</a>', r"[\2](\1)", s)
    return html.unescape(s)


def latest_markdown() -> str:
    entry = latest()
    lines = []
    for section in entry["sections"]:
        heading = _html_to_markdown(section.get("heading", ""))
        if heading:
            lines.append(f"## {heading}")
            lines.append("")
        for para in section.get("intro", []):
            lines.append(_html_to_markdown(para))
            lines.append("")
        for item in section.get("items", []):
            lines.append(f"- {_html_to_markdown(item)}")
        if section.get("items"):
            lines.append("")
    return "\n".join(lines).strip() + "\n"


class Command(BaseCommand):
    help = "Emit release notes from aso/release_notes.py"

    def add_arguments(self, parser):
        parser.add_argument(
            "--markdown", action="store_true",
            help="Print the newest entry as GitHub-release markdown.",
        )
        parser.add_argument(
            "--check", action="store_true",
            help="Fail unless the newest entry matches settings.VERSION.",
        )

    def handle(self, *args, **options):
        entry = latest()
        if options["check"] or not options["markdown"]:
            if entry["version"] != settings.VERSION:
                raise CommandError(
                    f"Newest release-notes entry is v{entry['version']} but "
                    f"the app VERSION is {settings.VERSION}. Add the new "
                    "release's entry to aso/release_notes.py BEFORE "
                    "releasing - a release must never ship without notes."
                )
            self.stdout.write(
                f"OK: release notes present for v{entry['version']}."
            )
        if options["markdown"]:
            self.stdout.write(latest_markdown())
