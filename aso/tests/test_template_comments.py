"""Guard: Django `{# ... #}` comments must open and close on one line.

Django's lexer only recognises the short comment syntax when the whole
comment sits on a single line. A comment that wraps onto a second line is
not a comment at all: the template engine emits it verbatim, and the page
shows the raw `{# ... #}` text to the user (this happened once in the
dashboard's App Summary panel). Multi-line notes belong in
`{% comment %} ... {% endcomment %}`.

Scans every template of every installed app, so it holds for both the Pro
and the Free build without naming any Pro-only app.
"""

from pathlib import Path

from django.apps import apps
from django.template import engines
from django.test import SimpleTestCase


def _template_files():
    for app in apps.get_app_configs():
        templates_dir = Path(app.path) / "templates"
        if templates_dir.is_dir():
            yield from sorted(templates_dir.rglob("*.html"))


class TemplateCommentSyntaxTest(SimpleTestCase):
    def test_short_comments_close_on_the_same_line(self):
        offenders = []
        for path in _template_files():
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                cursor = 0
                while (start := line.find("{#", cursor)) != -1:
                    end = line.find("#}", start + 2)
                    if end == -1:
                        offenders.append(f"{path}:{lineno}: {line.strip()}")
                        break
                    cursor = end + 2
        self.assertEqual(
            offenders,
            [],
            "Django `{# #}` comments must open and close on one line; use "
            "{% comment %}...{% endcomment %} for longer notes:\n" + "\n".join(offenders),
        )

    def test_app_summary_renders_no_raw_comment_text(self):
        # The panel that once leaked its comment. Rendering it through the
        # real engine proves the fix at the layer where the bug lived.
        template = engines["django"].get_template("aso/_app_summary.html")
        html = template.render({"summary": {"app_name": "Sample", "countries": []}})
        self.assertNotIn("{#", html)
        self.assertNotIn("#}", html)
