"""Database TLS configuration: the MongoDB client and the PostgreSQL settings.

No network: MongoClient is replaced by a recorder, and settings are only evaluated, never connected.
"""
import os
import runpy
from pathlib import Path
from unittest import mock

import certifi
from django.conf import settings
from django.test import SimpleTestCase, override_settings

from core import mongo

INSECURE_OPTIONS = ("tlsInsecure", "tlsAllowInvalidCertificates", "tlsAllowInvalidHostnames")


class MongoTLSOptionTests(SimpleTestCase):
    def build(self, uri):
        """Create the client exactly as the app does and return the kwargs MongoClient received."""
        with override_settings(MONGODB_USE_MOCK=False, MONGODB_URI=uri), mock.patch.object(mongo, "MongoClient") as client_cls:
            mongo._create_client()
        client_cls.assert_called_once()
        args, kwargs = client_cls.call_args
        self.assertEqual(args, (uri,))  # URI passed through unchanged
        return kwargs

    def test_srv_uri_uses_certifi(self):
        kwargs = self.build("mongodb+srv://cluster0.example.mongodb.net/?retryWrites=true&w=majority")
        self.assertEqual(kwargs["tlsCAFile"], certifi.where())

    def test_tls_true_uses_certifi(self):
        self.assertEqual(self.build("mongodb://db1.example.com:27017,db2.example.com:27017/?tls=true")["tlsCAFile"], certifi.where())

    def test_ssl_true_uses_certifi(self):
        self.assertEqual(self.build("mongodb://db.example.com:27017/?ssl=TRUE")["tlsCAFile"], certifi.where())

    def test_local_non_tls_uri_unchanged(self):
        kwargs = self.build("mongodb://127.0.0.1:27017")
        self.assertNotIn("tlsCAFile", kwargs)
        self.assertEqual(kwargs["appname"], "seo-management-dashboard")

    def test_srv_with_tls_disabled_is_respected(self):
        self.assertNotIn("tlsCAFile", self.build("mongodb+srv://cluster0.example.mongodb.net/?tls=false"))

    def test_explicit_ca_file_in_uri_wins(self):
        self.assertNotIn("tlsCAFile", self.build("mongodb+srv://cluster0.example.mongodb.net/?tlsCAFile=/etc/ssl/custom.pem"))

    def test_verification_never_relaxed(self):
        for uri in ("mongodb+srv://c.example.mongodb.net/", "mongodb://h/?tls=true", "mongodb://127.0.0.1:27017"):
            kwargs = self.build(uri)
            for option in INSECURE_OPTIONS:
                self.assertNotIn(option, kwargs, uri)


class SharedClientTests(SimpleTestCase):
    def tearDown(self):
        mongo.reset_client()

    @override_settings(MONGODB_USE_MOCK=False, MONGODB_URI="mongodb+srv://cluster0.example.mongodb.net/", MONGODB_DB_NAME="seo_dashboard")
    def test_get_client_get_db_and_ping_share_one_tls_client(self):
        """init_mongodb and the app both go through get_client()/get_db()/ping()."""
        mongo.reset_client()
        with mock.patch.object(mongo, "MongoClient") as client_cls:
            client = mongo.get_client()
            db = mongo.get_db()
            mongo.ping()
            self.assertIs(mongo.get_client(), client)
        client_cls.assert_called_once()
        self.assertEqual(client_cls.call_args.kwargs["tlsCAFile"], certifi.where())
        client.__getitem__.assert_called_with("seo_dashboard")
        self.assertIs(db, client.__getitem__.return_value)
        client.admin.command.assert_called_once_with("ping")


SETTINGS_FILE = Path(settings.BASE_DIR) / "config" / "settings.py"


def database_settings(**env):
    """Evaluate config/settings.py under the given environment and return DATABASES["default"].

    Values given here win over .env (load_dotenv never overrides), and os.environ is restored afterwards.
    """
    with mock.patch.dict(os.environ, env):
        return runpy.run_path(str(SETTINGS_FILE))["DATABASES"]["default"]


class PostgresTLSSettingsTests(SimpleTestCase):
    def test_pooled_verify_full_uses_certifi_and_disables_server_side_cursors(self):
        db = database_settings(DATABASE_URL="postgresql://ep-app-pooler.example.com/neondb?sslmode=verify-full")
        self.assertEqual(db["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(db["OPTIONS"]["sslmode"], "verify-full")
        self.assertEqual(db["OPTIONS"]["sslrootcert"], certifi.where())
        self.assertTrue(db["DISABLE_SERVER_SIDE_CURSORS"])
        self.assertTrue(db["CONN_HEALTH_CHECKS"])

    def test_direct_endpoint_keeps_server_side_cursors(self):
        db = database_settings(DATABASE_URL="postgresql://ep-app.example.com/neondb?sslmode=verify-full")
        self.assertEqual(db["OPTIONS"]["sslrootcert"], certifi.where())
        self.assertFalse(db["DISABLE_SERVER_SIDE_CURSORS"])

    def test_explicit_sslrootcert_wins(self):
        db = database_settings(
            DATABASE_URL="postgresql://ep-app-pooler.example.com/neondb?sslmode=verify-full&sslrootcert=/etc/ssl/custom.pem"
        )
        self.assertEqual(db["OPTIONS"]["sslrootcert"], "/etc/ssl/custom.pem")

    def test_non_verifying_sslmode_gets_no_ca_bundle(self):
        db = database_settings(DATABASE_URL="postgresql://ep-app-pooler.example.com/neondb?sslmode=require")
        self.assertNotIn("sslrootcert", db["OPTIONS"])

    def test_sqlite_fallback_when_database_url_empty(self):
        db = database_settings(DATABASE_URL="")
        self.assertEqual(db["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(Path(db["NAME"]).name, "db.sqlite3")
        self.assertFalse(db.get("DISABLE_SERVER_SIDE_CURSORS"))
        self.assertNotIn("sslrootcert", db.get("OPTIONS", {}))
