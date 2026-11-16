import sys
import os
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preflight import check_file_security, load_env, overall_status  # noqa: E402


class PreflightTest(unittest.TestCase):
    def test_load_env_does_not_expose_values_in_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("OPENALEX_API_KEY='secret'\nexport UNPAYWALL_EMAIL=user@example.com\n", encoding="utf-8")
            values = load_env(path)
        self.assertEqual(values, {"OPENALEX_API_KEY": "secret", "UNPAYWALL_EMAIL": "user@example.com"})

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics do not apply on Windows")
    def test_env_file_permissions_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("KEY=value\n", encoding="utf-8")
            path.chmod(0o600)
            self.assertEqual(check_file_security(path)["status"], "ok")
            path.chmod(0o644)
            self.assertEqual(check_file_security(path)["status"], "degraded")

    @unittest.skipUnless(os.name == "nt", "Windows ACL reporting")
    def test_windows_does_not_claim_posix_acl_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("KEY=value", encoding="utf-8")
            value = check_file_security(path)
        self.assertEqual(value["status"], "degraded")
        self.assertIn("ACL", value["reason"])

    def test_overall_status_prefers_blocked(self):
        self.assertEqual(overall_status({"a": {"status": "ok"}, "b": {"status": "degraded"}}), "degraded")
        self.assertEqual(overall_status({"a": {"status": "blocked"}, "b": {"status": "degraded"}}), "blocked")


if __name__ == "__main__":
    unittest.main()
