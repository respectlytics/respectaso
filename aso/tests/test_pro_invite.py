"""The button under the navbar that invites a free user to Pro.

Mac app without a license: a Go Pro button to the pricing page. Docker,
where Pro cannot run: the Pro page with the Mac download. Nothing for Pro
users, for an expired license (it has its own banner), or on pages that
already make the offer. Free-tier test: no aso_pro or licensing import at
module level.
"""

import os
import re
from unittest import mock, skipUnless

from django.apps import apps as django_apps
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from aso.context_processors import PRO_INVITE_DOCKER, PRO_INVITE_MAC
from aso.links import PRICING_URL, PRO_PAGE_URL

DASHES = ("—", "–", " - ")
PRICE = re.compile(r"[$€£]\s?\d|\d\s?(kr|SEK|USD|EUR)\b")


def invite(html):
    match = re.search(r'<a href="([^"]+)"[^>]*data-pro-invite[^>]*>(.*?)</a>', html, re.S)
    if not match:
        return None
    text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
    return match.group(1), text


class TheInviteTest(TestCase):
    def page(self, name="aso:dashboard"):
        return self.client.get(reverse(name)).content.decode()

    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_the_mac_app_without_a_license_invites_to_pricing(self):
        self.assertEqual(invite(self.page()), (PRICING_URL, PRO_INVITE_MAC))

    @override_settings(IS_NATIVE_APP=False)
    def test_docker_invites_to_the_mac_app(self):
        self.assertEqual(invite(self.page()), (PRO_PAGE_URL, PRO_INVITE_DOCKER))

    @override_settings(IS_NATIVE_APP=True, DEBUG=True, DEBUG_SKIP_LICENSE=True)
    def test_pro_users_see_nothing(self):
        self.assertIsNone(invite(self.page()))

    @skipUnless(django_apps.is_installed("licensing"), "the Mac build only")
    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_an_expired_license_gets_its_banner_not_the_invite(self):
        from types import SimpleNamespace

        expired = SimpleNamespace(is_valid=False, is_expired=True, is_refunded=False,
                                  is_revoked=False, months=12, days_until_expiry=-3)
        # Patched where each reader looks it up: the license context
        # processor binds the function at import.
        with mock.patch("licensing.decorators.get_license_info", return_value=expired), \
             mock.patch("licensing.middleware.get_license_info", return_value=expired):
            html = self.page()
        self.assertIsNone(invite(html))
        self.assertIn("Your Pro license has expired", html)

    @override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
    def test_pages_that_make_the_offer_do_not_repeat_it(self):
        for name in ("aso:pro_promo_researcher", "aso:pro_promo_simulator"):
            with self.subTest(page=name):
                self.assertIsNone(invite(self.page(name)))

    def test_the_words_carry_no_price_and_no_dash(self):
        for text in (PRO_INVITE_MAC, PRO_INVITE_DOCKER):
            self.assertIsNone(PRICE.search(text), text)
            for dash in DASHES:
                self.assertNotIn(dash, text)


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
                    text = open(os.path.join(root, name), encoding="utf-8", errors="ignore").read()
                    if 'href="https://respectaso.com/pricing' in text or '= "https://respectaso.com/pricing' in text:
                        offenders.append(os.path.join(root, name))
        self.assertEqual(offenders, [], "Link to aso.links.PRICING_URL ({{ pricing_url }}) instead")
