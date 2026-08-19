"""Tests for Apple Ads settings storage (shared settings.json handling)."""

import json
import stat
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from aso.apple_ads import storage


class StorageTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        storage.reset_cache()
        from aso.models import AppleTopTerm

        AppleTopTerm.clear_floor_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._override.disable()
        storage.reset_cache()
        self._tmp.cleanup()

    @property
    def settings_path(self):
        return self.data_dir / "settings.json"


class ApplSettingsRoundTripTest(StorageTestBase):
    def test_defaults_when_missing(self):
        data = storage.load_apple_settings()
        # Fresh installs default to the internal estimate (unset retired).
        self.assertEqual(data["popularity_source"], "internal")
        block = data["apple_ads"]
        self.assertFalse(block["tested_ok"])
        self.assertFalse(block["credentials_rejected"])
        self.assertFalse(block["estimate_opt_out"])
        self.assertFalse(block["legacy_upgrade_pending"])
        self.assertEqual(block["client_id"], "")
        self.assertEqual(block["active_weeks"], {})
        self.assertEqual(block["backfill"], {})
        self.assertEqual(block["coverage"]["week"], "")
        self.assertFalse(block["impression_share"]["has_data"])

    def test_preserves_foreign_owner_keys(self):
        """aso_pro's LLM keys must survive our writes (shared file)."""
        self.settings_path.write_text(json.dumps({
            "llm_provider": "anthropic",
            "api_keys": {"anthropic": "sk-ant-secret"},
        }))
        storage.reset_cache()
        storage.save_apple_settings(
            popularity_source="internal",
            apple_ads={"client_id": "SEARCHADS.x"},
        )
        raw = json.loads(self.settings_path.read_text())
        self.assertEqual(raw["llm_provider"], "anthropic")
        self.assertEqual(raw["api_keys"]["anthropic"], "sk-ant-secret")
        self.assertEqual(raw["popularity_source"], "internal")
        self.assertEqual(raw["apple_ads"]["client_id"], "SEARCHADS.x")

    def test_partial_apple_update_keeps_other_apple_keys(self):
        storage.save_apple_settings(apple_ads={"client_id": "SEARCHADS.x"})
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["client_id"], "SEARCHADS.x")
        self.assertTrue(block["tested_ok"])

    def test_file_permissions_600(self):
        storage.save_apple_settings(popularity_source="internal")
        mode = stat.S_IMODE(self.settings_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_invalid_source_rejected(self):
        with self.assertRaises(ValueError):
            storage.save_apple_settings(popularity_source="banana")

    def test_corrupt_file_yields_defaults(self):
        self.settings_path.write_text("{not json")
        storage.reset_cache()
        data = storage.load_apple_settings()
        self.assertEqual(data["popularity_source"], "internal")

    def test_mtime_cache_invalidated_on_save(self):
        storage.save_apple_settings(popularity_source="internal")
        self.assertEqual(storage.get_popularity_source(), "internal")
        storage.save_apple_settings(popularity_source="apple")
        self.assertEqual(storage.get_popularity_source(), "apple")

    def test_nested_defaults_merged(self):
        storage.save_apple_settings(apple_ads={
            "coverage": {"terms": 12},
            "impression_share": {"status": "completed"},
        })
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["coverage"]["terms"], 12)
        self.assertEqual(block["coverage"]["tracked_total"], 0)  # default kept
        self.assertEqual(block["impression_share"]["status"], "completed")
        self.assertFalse(block["impression_share"]["has_data"])


class ConnectionStateTest(StorageTestBase):
    def test_apple_source_ready_gating(self):
        self.assertFalse(storage.apple_source_ready())
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        self.assertTrue(storage.apple_source_ready())
        storage.save_apple_settings(apple_ads={"credentials_rejected": True})
        self.assertFalse(storage.apple_source_ready())

    def test_has_credentials_requires_ids_and_key(self):
        from unittest import mock

        self.assertFalse(storage.has_credentials())
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t", "key_id": "k",
        })
        self.assertFalse(storage.has_credentials())  # no key file yet
        with mock.patch("aso.apple_ads.keys.has_private_key", return_value=True):
            self.assertTrue(storage.has_credentials())

    def test_api_credentials_assembly(self):
        from unittest import mock

        self.assertIsNone(storage.api_credentials())
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t", "key_id": "k",
        })
        with mock.patch("aso.apple_ads.keys.has_private_key", return_value=True), \
                mock.patch("aso.apple_ads.keys.load_private_key_pem",
                           return_value="PEM"):
            credentials = storage.api_credentials()
        self.assertEqual(credentials, {
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t",
            "key_id": "k", "private_key_pem": "PEM",
        })

    def test_mark_credentials_rejected(self):
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        storage.mark_credentials_rejected()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["credentials_rejected"])
        self.assertTrue(block["credentials_rejected_at"])
        self.assertFalse(block["tested_ok"])


class LegacyMigrationTest(StorageTestBase):
    """Cookie-era settings.json files upgrade cleanly to the v1 model."""

    def _write_legacy(self, tested_ok=True, source="apple"):
        self.settings_path.write_text(json.dumps({
            "popularity_source": source,
            "apple_ads": {
                "cookies": [{"name": "myacinfo", "value": "tok"}],
                "primary_app_id": "123",
                "tested_ok": tested_ok,
                "session_expired": False,
                "session_expired_at": "",
                "last_sync_at": "2026-08-01T00:00:00",
            },
            "llm_provider": "anthropic",
        }))
        storage.reset_cache()

    def test_cookie_era_verified_install_migrates(self):
        self._write_legacy(tested_ok=True)
        storage.migrate_legacy_settings()
        raw = json.loads(self.settings_path.read_text())
        block = raw["apple_ads"]
        for key in storage.LEGACY_KEYS:
            self.assertNotIn(key, block)
        self.assertTrue(block["legacy_upgrade_pending"])
        self.assertFalse(block["tested_ok"])  # old test proved a dead session
        self.assertEqual(block["last_sync_at"], "2026-08-01T00:00:00")
        self.assertEqual(raw["llm_provider"], "anthropic")  # foreign keys kept
        self.assertFalse(storage.apple_source_ready())

    def test_cookie_era_unverified_install_migrates_quietly(self):
        self._write_legacy(tested_ok=False, source="internal")
        storage.migrate_legacy_settings()
        block = json.loads(self.settings_path.read_text())["apple_ads"]
        self.assertNotIn("cookies", block)
        self.assertFalse(block.get("legacy_upgrade_pending"))

    def test_unset_source_becomes_internal(self):
        self.settings_path.write_text(json.dumps({
            "popularity_source": "", "apple_ads": {},
        }))
        storage.reset_cache()
        storage.migrate_legacy_settings()
        raw = json.loads(self.settings_path.read_text())
        self.assertEqual(raw["popularity_source"], "internal")

    def test_idempotent_and_noop_on_fresh_install(self):
        self._write_legacy()
        storage.migrate_legacy_settings()
        first = self.settings_path.read_text()
        storage.migrate_legacy_settings()
        self.assertEqual(first, self.settings_path.read_text())
        # Fresh install: no file, migration must not create one.
        self.settings_path.unlink()
        storage.reset_cache()
        storage.migrate_legacy_settings()
        self.assertFalse(self.settings_path.exists())

    def test_v1_era_file_untouched(self):
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "tested_ok": True,
        })
        before = self.settings_path.read_text()
        storage.migrate_legacy_settings()
        self.assertEqual(before, self.settings_path.read_text())
