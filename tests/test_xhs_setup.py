import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import xhs_setup  # noqa: E402


class QuoteTest(unittest.TestCase):
    def test_single_quotes_are_doubled(self):
        self.assertEqual(xhs_setup.ps_quote("C:\\Tools\\xhs"), "'C:\\Tools\\xhs'")
        self.assertEqual(xhs_setup.ps_quote("it's"), "'it''s'")


class CookieTest(unittest.TestCase):
    def write(self, directory, value):
        path = Path(directory) / "cookies.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_v2_file_with_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"version": 2, "cookies": [{"name": "web_session"}]})
            self.assertTrue(xhs_setup.cookie_populated(path))

    def test_v2_file_with_empty_cookies(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, {"version": 2, "cookies": []})
            self.assertFalse(xhs_setup.cookie_populated(path))

    def test_legacy_bare_list(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write(directory, [{"name": "web_session"}])
            self.assertTrue(xhs_setup.cookie_populated(path))

    def test_missing_and_broken_files(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(xhs_setup.cookie_populated(Path(directory) / "absent.json"))
            broken = Path(directory) / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            self.assertFalse(xhs_setup.cookie_populated(broken))


class AssetTest(unittest.TestCase):
    def test_names_follow_platform(self):
        cases = {
            "win32": ("xiaohongshu-mcp-windows-amd64.exe", "xiaohongshu-login-windows-amd64.exe"),
            "darwin": ("xiaohongshu-mcp-darwin-arm64", "xiaohongshu-login-darwin-arm64"),
            "linux": ("xiaohongshu-mcp-linux-amd64", "xiaohongshu-login-linux-amd64"),
        }
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual((xhs_setup.asset_names()["mcp"], xhs_setup.asset_names()["login"]), cases["win32"])
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual((xhs_setup.asset_names()["mcp"], xhs_setup.asset_names()["login"]), cases["darwin"])
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual((xhs_setup.asset_names()["mcp"], xhs_setup.asset_names()["login"]), cases["linux"])


class LeaklessDirTest(unittest.TestCase):
    def test_only_leakless_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "leakless-amd64-abc").mkdir()
            (Path(directory) / "other-dir").mkdir()
            (Path(directory) / "leakless-amd64-file").write_text("x", encoding="utf-8")
            with mock.patch("tempfile.gettempdir", return_value=directory):
                found = [p.name for p in xhs_setup.leakless_dirs()]
            self.assertEqual(found, ["leakless-amd64-abc"])


class RuntimeToolDirTest(unittest.TestCase):
    def test_configured_dir_wins(self):
        with mock.patch.object(xhs_setup, "runtime_settings", return_value={"xiaohongshu_tool_dir": "D:/xhs"}):
            self.assertEqual(str(xhs_setup.runtime_tool_dir()).replace("\\", "/"), "D:/xhs")

    def test_default_dir_used_without_config(self):
        with mock.patch.object(xhs_setup, "runtime_settings", return_value={}):
            self.assertEqual(xhs_setup.runtime_tool_dir(), xhs_setup.default_tool_dir())


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DownloadTest(unittest.TestCase):
    def test_hash_mismatch_is_rejected_and_nothing_is_left_behind(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.exe"
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"tampered")):
                with self.assertRaises(SystemExit):
                    xhs_setup.download("https://example/x", target, "0" * 64)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cached_copy_is_reused_only_when_the_hash_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "tool.exe"
            target.write_bytes(b"good")
            good = hashlib.sha256(b"good").hexdigest()
            self.assertIn("校验通过", xhs_setup.download("https://example/x", target, good))
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"good")):
                xhs_setup.download("https://example/x", target, good)  # still fine after re-download path
            target.write_bytes(b"evil")
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(b"good")):
                self.assertIn("已下载并校验", xhs_setup.download("https://example/x", target, good))
            self.assertEqual(target.read_bytes(), b"good")


class FakeLogin:
    """Stands in for the login executable: optionally writes a cookie file after a few polls."""

    def __init__(self, cookie, writes_after=None):
        self.cookie, self.writes_after, self.polls, self.terminated = cookie, writes_after, 0, False

    def poll(self):
        self.polls += 1
        if self.writes_after is not None and self.polls >= self.writes_after:
            self.cookie.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "new"}]}), encoding="utf-8")
            return 0
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


class BrowserLoginTest(unittest.TestCase):
    def run_login(self, writes_after, timeout):
        directory = tempfile.mkdtemp()
        cookie = Path(directory) / "cookies.json"
        cookie.write_text(json.dumps({"cookies": [{"name": "web_session", "value": "old"}]}), encoding="utf-8")
        fake = FakeLogin(cookie, writes_after)
        with mock.patch.object(xhs_setup, "verify_installed"), \
                mock.patch.object(xhs_setup, "stop_service"), \
                mock.patch("subprocess.Popen", return_value=fake), \
                mock.patch.object(xhs_setup, "emit"):
            ok, message = xhs_setup.browser_login(Path(directory), 18060, timeout=timeout, poll=0)
        return ok, message, cookie, fake

    def test_an_old_session_is_never_mistaken_for_a_new_scan(self):
        ok, _, cookie, _ = self.run_login(writes_after=3, timeout=5)
        self.assertTrue(ok)
        self.assertIn('"new"', cookie.read_text(encoding="utf-8"))
        self.assertFalse((cookie.parent / "cookies.previous.json").exists())

    def test_failed_scan_restores_the_previous_login_and_closes_the_browser(self):
        ok, message, cookie, fake = self.run_login(writes_after=None, timeout=0.01)
        self.assertFalse(ok)
        self.assertIn("恢复", message)
        self.assertIn('"old"', cookie.read_text(encoding="utf-8"))
        self.assertTrue(fake.terminated)


class ServiceOwnershipTest(unittest.TestCase):
    def test_process_image_survives_non_utf8_console_output(self):
        gbk_no_match = "信息: 没有运行的任务匹配指定标准。".encode("gbk")
        listed = '"xiaohongshu-mcp-windows-amd64.exe","1236","Console","1","20,000 K"'.encode("gbk")
        with mock.patch.object(sys, "platform", "win32"):
            for output, expected in ((gbk_no_match, ""), (listed, "xiaohongshu-mcp-windows-amd64.exe"), (None, "")):
                with mock.patch("subprocess.run", return_value=mock.Mock(stdout=output)):
                    self.assertEqual(xhs_setup.process_image(1236), expected)

    def test_foreign_process_on_the_port_is_never_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            xhs_setup.save_state(Path(directory), {"pid": 4242})
            with mock.patch.object(xhs_setup, "process_image", return_value="nginx.exe"), \
                    mock.patch.object(xhs_setup, "port_open", return_value=True), \
                    mock.patch("subprocess.run") as run, mock.patch("time.sleep"):
                message = xhs_setup.stop_service(Path(directory), 18060)
            run.assert_not_called()
            self.assertIn("不会去结束", message)

    def test_service_binds_to_localhost_only(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(xhs_setup, "owned_pid", return_value=None), \
                    mock.patch.object(xhs_setup, "port_open", return_value=False), \
                    mock.patch.object(xhs_setup, "verify_installed"), \
                    mock.patch("subprocess.Popen") as popen:
                popen.return_value.pid = 7
                xhs_setup.start_service(Path(directory), 18060)
            command = popen.call_args[0][0]
            self.assertEqual(command[command.index("-port") + 1], "127.0.0.1:18060")


class ExclusionTest(unittest.TestCase):
    def test_only_single_files_are_excluded_and_legacy_broad_ones_can_be_undone(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(sys, "platform", "win32"):
            tool = Path(directory)
            with mock.patch.object(xhs_setup, "run_elevated", return_value=(True, "OK")) as elevated:
                xhs_setup.add_exclusions([Path("C:/T/leakless-x/leakless.exe")], tool)
            lines = elevated.call_args[0][1]
            self.assertEqual(len(lines), 1)
            self.assertIn("leakless.exe'", lines[0])
            self.assertNotIn("ExclusionProcess", lines[0])
            state = xhs_setup.load_state(tool)
            state["excluded"] = ["C:\\Temp\\leakless-old", "leakless.exe"]  # what the first version added
            xhs_setup.save_state(tool, state)
            with mock.patch.object(xhs_setup, "run_elevated", return_value=(True, "OK")) as elevated:
                xhs_setup.remove_exclusions(tool)
            undo = " ".join(elevated.call_args[0][1])
            self.assertIn("leakless-old", undo)
            self.assertIn("-ExclusionProcess 'leakless.exe'", undo)
            self.assertIn("leakless-x", undo)

    def test_setup_never_changes_security_settings_without_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(xhs_setup, "install", return_value=[]), \
                    mock.patch.object(xhs_setup, "trigger_leakless", return_value=[Path(directory) / "missing.exe"]), \
                    mock.patch.object(xhs_setup, "run_elevated") as elevated, mock.patch.object(xhs_setup, "emit"):
                message = xhs_setup.setup(Path(directory), 18060, allow_exclusion=False)
            elevated.assert_not_called()
            self.assertIn("--allow-defender-exclusion", message)


if __name__ == "__main__":
    unittest.main()