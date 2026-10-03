"""The one Pro button in the top bar (docs/development/APP_SHELL_PLAN.md,
UI_REDESIGN_PLAN.md 11.2).

Mac app without a license: Get Pro, to the pricing page. Expired: Renew Pro.
Licensed: nothing. Docker, where Pro cannot run: Get Pro for Mac, to the Pro
page with the Mac download. Shown on every page, the Pro pages included.
Free-tier test: no aso_pro or licensing import at module level.
"""

import os
import re
from pathlib import Path
from unittest import mock, skipUnless

from django.apps import apps as django_apps
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from aso.copy_rules import dash_punctuation_in
from aso.links import PRICING_URL, PRO_PAGE_URL, RENEW_URL

PRICE = re.compile(r"[$€£]\s?\d|\d\s?(kr|SEK|USD|EUR)\b")
WORDS = ("Get Pro", "Renew Pro", "Get Pro for Mac")


def button(html):
    match = re.search(r'<a href="([^"]+)"[^>]*data-pro-button[^>]*>(.*?)</a>', html, re.DOTALL)
    if not match:
        return None
    return match.group(1), re.sub(r"<[^>]+>", "", match.group(2)).strip()


class TheButtonTest(TestCase):
    def page(self, name="aso:dashboard"):
        return self.client.get(reverse(name)).content.decode()

    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_the_mac_app_without_a_license_offers_pro(self):
        self.assertEqual(button(self.page()), (PRICING_URL, "Get Pro"))

    @override_settings(IS_NATIVE_APP=False)
    def test_docker_offers_the_mac_app(self):
        self.assertEqual(button(self.page()), (PRO_PAGE_URL, "Get Pro for Mac"))

    @override_settings(IS_NATIVE_APP=True, DEBUG=True, DEBUG_SKIP_LICENSE=True)
    def test_pro_users_see_nothing(self):
        self.assertIsNone(button(self.page()))

    @skipUnless(django_apps.is_installed("licensing"), "the Mac build only")
    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_an_expired_license_is_asked_to_renew(self):
        from types import SimpleNamespace

        expired = SimpleNamespace(is_valid=False, is_expired=True, is_refunded=False,
                                  is_revoked=False, months=12, days_until_expiry=-3)
        # Patched where each reader looks it up: the license context
        # processor binds the function at import.
        with mock.patch("licensing.decorators.get_license_info", return_value=expired), \
             mock.patch("licensing.middleware.get_license_info", return_value=expired):
            html = self.page()
        self.assertEqual(button(html), (RENEW_URL, "Renew Pro"))
        self.assertIn("Your Pro license has ended.", html)
        self.assertIn('data-license-notice="expired"', html)

    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_it_shows_on_every_page_the_pro_pages_included(self):
        names = ["aso:dashboard", "aso:apps", "aso:opportunity", "aso:methodology"]
        if django_apps.is_installed("aso_pro"):
            names += ["aso_pro:ai_researcher", "aso_pro:simulator", "aso_pro:top_terms", "aso_pro:settings_license"]
        else:
            names += ["aso:pro_promo_researcher", "aso:pro_promo_simulator"]
        for name in names:
            with self.subTest(page=name):
                self.assertEqual(button(self.page(name)), (PRICING_URL, "Get Pro"))

    def test_the_words_carry_no_price_and_no_dash(self):
        for text in WORDS:
            self.assertIsNone(PRICE.search(text), text)
            self.assertEqual(dash_punctuation_in(text), "")


class OnePricingAddressTest(TestCase):
    def test_no_template_or_module_types_it_out(self):
        offenders = []
        for folder in ("aso", "aso_pro", "core", "static/js", "_public_overrides"):
            for root, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, folder)):
                if "/tests" in root:
                    continue
                for name in files:
                    if not name.endswith((".py", ".html", ".js")) or name == "links.py":
                        continue
                    text = Path(os.path.join(root, name)).read_text(encoding="utf-8", errors="ignore")
                    if 'href="https://respectaso.com/pricing' in text or '= "https://respectaso.com/pricing' in text:
                        offenders.append(os.path.join(root, name))
        self.assertEqual(offenders, [], "Link to aso.links.PRICING_URL ({{ pricing_url }}) instead")
