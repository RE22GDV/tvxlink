"""The web page, the launcher and the vendor helper without a meter or network.
python -m unittest tests.test_ui"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI = (ROOT / "tvxlink" / "ui.html").read_text(encoding="utf-8")
SCRIPT = UI[UI.index("<script>") + 8:UI.rindex("</script>")]


class PageTest(unittest.TestCase):
    def test_every_referenced_element_exists(self):
        ids = set(re.findall(r'\bid="([\w-]+)"', UI))
        used = set(re.findall(r"\$\('([\w-]+)'\)", SCRIPT))
        self.assertEqual(sorted(used - ids), [])

    def test_no_external_resources(self):
        # The page works offline: no CDN scripts, fonts or trackers.
        self.assertNotRegex(UI, r'<script[^>]+src=|<link[^>]+href="http')

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_javascript_syntax(self):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(SCRIPT)
        try:
            r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        finally:
            os.unlink(f.name)


class LauncherTest(unittest.TestCase):
    def test_check_reports_without_installing(self):
        state = tempfile.mkdtemp()
        env = {**os.environ, "TVXLINK_STATE": state, "TVXLINK_DATA": tempfile.mkdtemp(), "PYTHONUTF8": "1"}
        r = subprocess.run([sys.executable, str(ROOT / "launcher.py"), "--check", "--lang", "en"],
                           capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        info = json.loads(r.stdout[r.stdout.index("{"):])
        self.assertEqual(info["state_dir"], state)
        self.assertEqual(os.listdir(state), [])              # --check creates nothing

    def test_vendor_status_on_an_empty_folder(self):
        from tvxlink import vendor
        old = os.environ.get("TVXLINK_STATE")
        os.environ["TVXLINK_STATE"] = tempfile.mkdtemp()
        try:
            self.assertFalse(vendor.ready())
            self.assertTrue(all(v == "missing" for v in vendor.status().values()))
            self.assertEqual(vendor.driver_inf().name, "usbuart3410.inf")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(vendor.main(["--check"]), 0)
        finally:
            if old is None:
                os.environ.pop("TVXLINK_STATE", None)
            else:
                os.environ["TVXLINK_STATE"] = old

    def test_vendor_hashes_are_pinned(self):
        from tvxlink import vendor
        self.assertRegex(vendor.NETUPDATE[1], r"^[0-9a-f]{64}$")
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", d) for _, d in vendor.FILES.values() if d))


if __name__ == "__main__":
    unittest.main()
