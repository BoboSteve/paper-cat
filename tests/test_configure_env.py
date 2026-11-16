import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from configure_env import check_value, merge_env, redact, write_env
from openreview_enumerate import load_settings


class ConfigureEnvTest(unittest.TestCase):
    def test_merge_keeps_other_lines_and_replaces_keys(self):
        text = "# comment\nOPENALEX_API_KEY=abc\nOPENREVIEW_USERNAME=old@x.org\nOPENREVIEW_TOTP_SECRET=zzz\n"
        merged = merge_env(text, {
            "OPENREVIEW_USERNAME": "new@x.org",
            "OPENREVIEW_PASSWORD": "p#ss",
            "OPENREVIEW_TOTP_SECRET": None,
        })
        self.assertEqual(
            merged,
            "# comment\nOPENALEX_API_KEY=abc\nOPENREVIEW_USERNAME=new@x.org\nOPENREVIEW_PASSWORD=p#ss\n",
        )

    def test_written_values_round_trip_through_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sub" / ".env"
            write_env(path, {"OPENREVIEW_USERNAME": "a@b.org", "OPENREVIEW_PASSWORD": "Ab1#x=y"})
            settings = load_settings(path)
            self.assertEqual(settings["OPENREVIEW_PASSWORD"], "Ab1#x=y")
            self.assertEqual(settings["OPENREVIEW_USERNAME"], "a@b.org")

    def test_rejects_values_the_reader_would_alter(self):
        for value in ("", " pad", "line\nbreak", "'quoted'"):
            with self.assertRaises(ValueError):
                check_value("X", value)

    def test_skipped_totp_reaches_client_as_absent(self):
        import configure_env
        import openreview_enumerate

        seen = {}

        def fake_build_client(openreview, settings, api_version, baseurl):
            seen.update(settings)
            settings.get("OPENREVIEW_TOTP_SECRET", "").strip()
            group = type("Group", (), {"id": configure_env.OPENREVIEW_PROBE_GROUP})()
            return type("Client", (), {"get_group": lambda self, id: group})()

        original = openreview_enumerate.build_client
        openreview_enumerate.build_client = fake_build_client
        sys.modules.setdefault("openreview", type(sys)("openreview"))  # optional dependency; the client is faked
        try:
            configure_env.test_openreview({
                "OPENREVIEW_USERNAME": "a@b.org",
                "OPENREVIEW_PASSWORD": "pw",
                "OPENREVIEW_TOTP_SECRET": None,
            })
        finally:
            openreview_enumerate.build_client = original
        self.assertNotIn("OPENREVIEW_TOTP_SECRET", seen)

    def test_redact_hides_secrets(self):
        self.assertNotIn("s3cret", redact("bad login for s3cret", ["s3cret"]))


if __name__ == "__main__":
    unittest.main()
