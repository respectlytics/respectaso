"""RespectASO runs on the user's own computer and has an open source edition,
so its Django admin stays at Django's default /admin/ in both editions, and
nowhere else: a product people run themselves never carries the path the
hosted products hide their admin behind (loheden-security skill, The admin).
This test needs no knowledge of that path, so it is safe in the public
edition, where the sync's Docker smoke test runs it too.
"""

from django.test import SimpleTestCase
from django.urls import URLResolver, get_resolver, resolve


def _admin_prefixes(patterns, prefix=""):
    """Every URL prefix the admin site is mounted at."""
    found = []
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            here = prefix + str(pattern.pattern)
            if pattern.app_name == "admin":
                found.append(here)
            else:
                found += _admin_prefixes(pattern.url_patterns, here)
    return found


class AdminAtDefaultPathTest(SimpleTestCase):
    def test_the_admin_is_at_admin(self):
        self.assertEqual(resolve("/admin/").namespace, "admin")
        self.assertEqual(resolve("/admin/login/").url_name, "login")

    def test_the_admin_is_mounted_nowhere_else(self):
        self.assertEqual(_admin_prefixes(get_resolver().url_patterns), ["admin/"])
