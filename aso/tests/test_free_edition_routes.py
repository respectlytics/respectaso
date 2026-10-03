"""A free edition template names a Pro route only inside a Pro build check.

aso/templates is synced to the public repo, where aso_pro does not exist and
{% url 'aso_pro:...' %} raises NoReverseMatch. A license is not a Pro build:
the free edition's tests bypass the license check, so on 2026-10-02 a
keyword's detail row guarded by "has a license" crashed the public suite in
the Docker smoke test. This reads every template the public repo gets and
fails when a Pro route sits outside a branch that only the Pro build takes
({% if pro_edition %} or {% if 'aso_pro' in INSTALLED_APPS %}, alone or with
"and").
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
FREE_ROOTS = ("aso/templates", "_public_overrides/aso/templates")
TAG = re.compile(r"{%\s*(if|elif|else|endif)\b(.*?)%}|{%\s*url\s+'aso_pro:", re.DOTALL)
PRO_BUILD = re.compile(r"\bpro_edition\b|'aso_pro' in INSTALLED_APPS")


def _guards_pro_build(condition: str) -> bool:
    return bool(PRO_BUILD.search(condition)) and " or " not in condition and " not " not in f" {condition} "


def unguarded_pro_routes(text: str) -> list[int]:
    """Line numbers of Pro routes outside a Pro build branch."""
    stack, found = [], []
    for match in TAG.finditer(text):
        kind = match.group(1)
        if kind == "if":
            stack.append(_guards_pro_build(match.group(2)))
        elif kind in ("elif", "else"):
            if stack:
                stack[-1] = False
        elif kind == "endif":
            if stack:
                stack.pop()
        elif not any(stack):
            found.append(text.count("\n", 0, match.start()) + 1)
    return found


class ProRoutesOnlyInTheProBuildTest(SimpleTestCase):
    def test_every_pro_route_is_inside_a_pro_build_branch(self):
        bad = []
        for root in FREE_ROOTS:
            for path in sorted((BASE / root).rglob("*.html")):
                for line in unguarded_pro_routes(path.read_text(encoding="utf-8")):
                    bad.append(f"{path.relative_to(BASE)}:{line}")
        self.assertEqual(bad, [])

    def test_a_license_check_is_not_a_build_check(self):
        """Proved once, on the shape of the 2026-10-02 crash."""
        crash = "{% if keyword_limit_context.is_pro %}<a href=\"{% url 'aso_pro:ai_researcher' %}\">{% endif %}"
        fixed = "{% if pro_edition and keyword_limit_context.is_pro %}<a href=\"{% url 'aso_pro:ai_researcher' %}\">{% endif %}"
        other = "{% if pro_edition %}{% else %}<a href=\"{% url 'aso_pro:simulator' %}\">{% endif %}"
        self.assertEqual(unguarded_pro_routes(crash), [1])
        self.assertEqual(unguarded_pro_routes(fixed), [])
        self.assertEqual(unguarded_pro_routes(other), [1])
