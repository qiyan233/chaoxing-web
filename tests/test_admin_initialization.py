"""Regression coverage for administrator password startup migration."""
import unittest
from unittest.mock import patch

import tests.support  # noqa: F401 -- isolate config before application imports
from sqlalchemy import create_engine

from webapp.db import _ensure_default_admin_password
from webapp.models.settings import AppSetting
from webapp.services.credential import hash_admin_password, verify_admin_password


MARKER_KEY = "default_admin_password_20260510"
PASSWORD_KEY = AppSetting.KEY_ADMIN_PASSWORD


class AdminInitializationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        # Legacy databases may contain NULL even though the current model does not.
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)"
            )

    def tearDown(self):
        self.engine.dispose()

    def seed(self, key, value):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO app_settings (key, value) VALUES (?, ?)", (key, value)
            )

    def initialize(self):
        with self.engine.begin() as connection:
            _ensure_default_admin_password(connection)

    def values(self):
        with self.engine.connect() as connection:
            return dict(connection.exec_driver_sql("SELECT key, value FROM app_settings").fetchall())

    def test_fresh_database_initializes_default_password_and_marker(self):
        self.initialize()
        values = self.values()
        self.assertTrue(verify_admin_password("admin123", values[PASSWORD_KEY]))
        self.assertEqual(values[MARKER_KEY], "done")

    def test_legacy_password_survives_missing_marker(self):
        previous_hash = hash_admin_password("existing-admin-password")
        self.seed(PASSWORD_KEY, previous_hash)
        self.initialize()
        values = self.values()
        self.assertEqual(values[PASSWORD_KEY], previous_hash)
        self.assertTrue(verify_admin_password("existing-admin-password", values[PASSWORD_KEY]))
        self.assertEqual(values[MARKER_KEY], "done")

    def test_marked_database_preserves_changed_password(self):
        changed_hash = hash_admin_password("changed-admin-password")
        self.seed(PASSWORD_KEY, changed_hash)
        self.seed(MARKER_KEY, "done")
        self.initialize()
        self.assertEqual(self.values()[PASSWORD_KEY], changed_hash)

    def test_repeated_initialization_is_idempotent(self):
        self.initialize()
        first_values = self.values()
        self.initialize()
        self.assertEqual(self.values(), first_values)

    def test_empty_or_null_password_is_initialized(self):
        for value in ("", None):
            with self.subTest(value=value):
                with self.engine.begin() as connection:
                    connection.exec_driver_sql("DELETE FROM app_settings")
                self.seed(PASSWORD_KEY, value)
                self.initialize()
                self.assertTrue(verify_admin_password("admin123", self.values()[PASSWORD_KEY]))

    def test_marker_does_not_prevent_repairing_missing_or_empty_password(self):
        for state in ("missing", "empty", "null"):
            with self.subTest(state=state):
                with self.engine.begin() as connection:
                    connection.exec_driver_sql("DELETE FROM app_settings")
                self.seed(MARKER_KEY, "done")
                if state != "missing":
                    self.seed(PASSWORD_KEY, "" if state == "empty" else None)
                self.initialize()
                self.assertTrue(verify_admin_password("admin123", self.values()[PASSWORD_KEY]))

    def test_hash_failure_does_not_record_success(self):
        with patch("webapp.services.credential.hash_admin_password", side_effect=RuntimeError("hash failed")):
            with self.assertRaisesRegex(RuntimeError, "hash failed"):
                self.initialize()
        self.assertNotIn(MARKER_KEY, self.values())
        self.assertNotIn(PASSWORD_KEY, self.values())

    def test_password_write_failure_does_not_record_success(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER reject_password BEFORE INSERT ON app_settings "
                "WHEN NEW.key = 'admin_password_hash' "
                "BEGIN SELECT RAISE(ABORT, 'password write failed'); END"
            )
        with self.assertRaisesRegex(Exception, "password write failed"):
            self.initialize()
        self.assertNotIn(MARKER_KEY, self.values())

    def test_marker_write_failure_rolls_back_password_initialization(self):
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER reject_marker BEFORE INSERT ON app_settings "
                "WHEN NEW.key = 'default_admin_password_20260510' "
                "BEGIN SELECT RAISE(ABORT, 'marker write failed'); END"
            )
        with self.assertRaisesRegex(Exception, "marker write failed"):
            self.initialize()
        self.assertEqual(self.values(), {})


if __name__ == "__main__":
    unittest.main()
