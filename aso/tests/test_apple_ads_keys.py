"""Tests for local Apple Ads key pair handling (aso.apple_ads.keys)."""

import stat
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from aso.apple_ads import keys


class KeysTestBase(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)


class KeyPairTest(KeysTestBase):
    def test_generate_save_load_delete_roundtrip(self):
        self.assertFalse(keys.has_private_key())
        public_pem = keys.generate_key_pair()
        self.assertIn("BEGIN PUBLIC KEY", public_pem)
        self.assertTrue(keys.has_private_key())
        pem = keys.load_private_key_pem()
        self.assertIn("PRIVATE KEY", pem)
        keys.delete_private_key()
        self.assertFalse(keys.has_private_key())
        with self.assertRaises(keys.AppleKeyError):
            keys.load_private_key_pem()

    def test_key_file_permissions_600(self):
        keys.generate_key_pair()
        mode = stat.S_IMODE(
            (self.data_dir / keys.KEY_FILENAME).stat().st_mode
        )
        self.assertEqual(mode, 0o600)

    def test_public_key_derivation_is_stable(self):
        first = keys.generate_key_pair()
        self.assertEqual(first, keys.public_key_pem())
        self.assertEqual(keys.public_key_pem(), keys.public_key_pem())

    def test_generate_replaces_existing_key(self):
        first = keys.generate_key_pair()
        second = keys.generate_key_pair()
        self.assertNotEqual(first, second)
        self.assertEqual(second, keys.public_key_pem())


class ImportValidationTest(KeysTestBase):
    def test_valid_ec_key_imports(self):
        keys.generate_key_pair()
        pem = keys.load_private_key_pem()
        keys.delete_private_key()
        keys.save_private_key(pem)
        self.assertTrue(keys.has_private_key())

    def test_garbage_rejected_with_friendly_message(self):
        with self.assertRaises(keys.AppleKeyError) as ctx:
            keys.save_private_key("not a key at all")
        self.assertIn("PEM", str(ctx.exception))
        self.assertFalse(keys.has_private_key())

    def test_rsa_key_rejected(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_pem = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        ).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with self.assertRaises(keys.AppleKeyError) as ctx:
            keys.save_private_key(rsa_pem)
        self.assertIn("elliptic-curve", str(ctx.exception))

    def test_wrong_curve_rejected(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        p384_pem = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with self.assertRaises(keys.AppleKeyError) as ctx:
            keys.save_private_key(p384_pem)
        self.assertIn("P-256", str(ctx.exception))
