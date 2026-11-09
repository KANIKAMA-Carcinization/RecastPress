"""Keep disposable DB fixture clients on their explicitly selected transports."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from database_smoke import disposable_binlog_settings, disposable_client_command


class DisposableClientTransportTests(unittest.TestCase):
    def test_mariadb_client_and_shutdown_admin_explicitly_disable_tls(self):
        client = disposable_client_command(
            "mariadb", "mariadb.exe", "client.ini", "--batch", "--execute=SELECT 1"
        )
        admin = disposable_client_command("mariadb", "mariadb-admin.exe", "client.ini", "shutdown")

        self.assertEqual(client[:3], ["mariadb.exe", "--defaults-file=client.ini", "--skip-ssl"])
        self.assertEqual(client[3:], ["--batch", "--execute=SELECT 1"])
        self.assertEqual(admin, ["mariadb-admin.exe", "--defaults-file=client.ini", "--skip-ssl", "shutdown"])

    def test_mysql_client_keeps_ca_tls_and_login_path_isolation(self):
        client = disposable_client_command(
            "mysql", "mysql.exe", "client.ini", "--batch", no_login_paths=True
        )
        admin = disposable_client_command("mysql", "mysqladmin.exe", "client.ini", "shutdown")

        self.assertEqual(client, ["mysql.exe", "--defaults-file=client.ini", "--no-login-paths", "--batch"])
        self.assertEqual(admin, ["mysqladmin.exe", "--defaults-file=client.ini", "shutdown"])
        self.assertNotIn("--skip-ssl", client)
        self.assertNotIn("--skip-ssl", admin)

    def test_unknown_backend_is_refused(self):
        with self.assertRaises(ValueError):
            disposable_client_command("other", "client", "client.ini", "shutdown")

    def test_default_mariadb_profile_is_log_enabled_and_mysql_profile_is_unchanged(self):
        mariadb = disposable_binlog_settings("mariadb", False, "fixture/probe-bin")
        mysql = disposable_binlog_settings("mysql", False, "fixture/probe-bin")
        mysql_probe = disposable_binlog_settings("mysql", True, "fixture/probe-bin")

        self.assertIn("log-bin=fixture/probe-bin", mariadb)
        self.assertIn("binlog-format=ROW", mariadb)
        self.assertIn("binlog-row-image=FULL", mariadb)
        self.assertNotIn("skip-log-bin", mariadb)
        self.assertEqual(mysql, "skip-log-bin\n")
        self.assertIn("server-id=7", mysql_probe)
        self.assertIn("log-bin=fixture/probe-bin", mysql_probe)


if __name__ == "__main__":
    unittest.main()
