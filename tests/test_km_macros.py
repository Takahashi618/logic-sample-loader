"""Keyboard Maestro の補助のマクロ（integration/km_macros.py・km_helper.py）の確認。

**本物の Keyboard Maestro には触らない**（定義ファイルも開く処理も一時フォルダと差し替え）。
"""

from __future__ import annotations

import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from integration import km_helper, km_macros as K  # noqa: E402

import install_qs_helper  # noqa: E402


class DefinitionTest(unittest.TestCase):
    def test_distribution_macros_are_separate_from_the_local_ones(self):
        # 手元の導入（logic_samples 補助）と識別子が重なると、片方を取り込んだときにもう一方に混ざる
        local = {m.uid for m in install_qs_helper.MACROS} | {install_qs_helper.GROUP_UID}
        dist = {m.uid for m in K.DIST_MACROS} | {K.DIST_GROUP_UID}
        self.assertFalse(local & dist)
        self.assertEqual([m.keycode for m in K.DIST_MACROS], [m.keycode for m in install_qs_helper.MACROS])
        self.assertEqual([m.args for m in K.DIST_MACROS], ["", "--flex", "--follow"])

    def test_macro_runs_the_command_and_speaks_its_line(self):
        m = K.DIST_MACROS[1]
        plist = K.macro_plist(m, K.dist_command(Path("/x/km_helper.sh"), m), K.DIST_SAY_VARIABLE)
        shell, speak = plist["Actions"]
        self.assertEqual(shell["Text"], '/bin/sh "/x/km_helper.sh" --flex --quiet; exit 0')
        self.assertEqual(shell["Variable"], K.DIST_SAY_VARIABLE)
        self.assertIn(f'getvariable("{K.DIST_SAY_VARIABLE}")', speak["Text"])
        self.assertIn('Application("VoiceOver").output(msg)', speak["Text"])
        self.assertEqual(plist["Triggers"][0]["Modifiers"], K.CONTROL_SHIFT)
        group = K.group_plist(K.DIST_GROUP_UID, K.DIST_GROUP_NAME, [plist])[0]
        self.assertEqual(group["Targeting"]["TargetingApps"][0]["BundleIdentifier"], "com.apple.logic10")

    def test_helper_script_runs_the_app_and_explains_a_missing_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Logicサンプルローダー's.app" / "LogicSampleLoader"
            exe.parent.mkdir()
            exe.write_text('#!/bin/sh\necho "args: $*"\n', encoding="utf-8")
            exe.chmod(0o755)
            script = Path(tmp) / "km_helper.sh"
            self.assertTrue(km_helper.write_script(str(exe), script))
            self.assertFalse(km_helper.write_script(str(exe), script))       # 同じ中身なら書かない
            self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
            out = subprocess.run(["/bin/sh", str(script), "--flex", "--quiet"], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), "args: --helper --flex --quiet")
            exe.unlink()                                                      # .app を動かした・消した
            out = subprocess.run(["/bin/sh", str(script), "--flex"], capture_output=True, text=True)
            self.assertIn("見つかりません", out.stdout)
            self.assertEqual(out.returncode, 0)                               # Keyboard Maestro の失敗の通知を出さない

    def test_refresh_only_when_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "km_helper.sh"
            self.assertFalse(km_helper.refresh_script("/Applications/A.app/Contents/MacOS/x", script))
            self.assertFalse(script.exists())                                 # 入れていなければ作らない
            km_helper.write_script("/Applications/A.app/Contents/MacOS/x", script)
            self.assertTrue(km_helper.refresh_script("/Applications/Test/A.app/Contents/MacOS/x", script))
            self.assertIn("/Applications/Test/A.app", script.read_text(encoding="utf-8"))


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.km_app = self.tmp / "Keyboard Maestro.app"
        self.km_app.mkdir()
        self.km_plist = self.tmp / "Keyboard Maestro Macros.plist"
        self.opened = []
        self.kw = dict(script=self.tmp / "km_helper.sh", import_file=self.tmp / "import.kmmacros",
                       km_plist=self.km_plist, km_app=self.km_app, opener=self.opened.append)

    def test_only_missing_macros_are_handed_over(self):
        installed = K.DIST_MACROS[0]
        self.km_plist.write_bytes(plistlib.dumps({"MacroGroups": [{"Name": K.DIST_GROUP_NAME, "IsActive": True,
                                                                    "Macros": [{"UID": installed.uid}]}]}))
        ok, message = km_helper.install("/A.app/Contents/MacOS/x", **self.kw)
        self.assertTrue(ok, message)
        self.assertEqual(self.opened, [["open", "-a", str(self.km_app), str(self.tmp / "import.kmmacros")]])
        group = plistlib.loads((self.tmp / "import.kmmacros").read_bytes())[0]
        self.assertEqual([m["UID"] for m in group["Macros"]], [m.uid for m in K.DIST_MACROS[1:]])
        self.assertIn("無効の状態で入ります", message)
        self.assertTrue((self.tmp / "km_helper.sh").is_file())

    def test_nothing_is_imported_twice(self):
        self.km_plist.write_bytes(plistlib.dumps({"MacroGroups": [{"Name": K.DIST_GROUP_NAME, "IsActive": True,
                                                                    "Macros": [{"UID": m.uid, "IsActive": False}
                                                                               for m in K.DIST_MACROS]}]}))
        ok, message = km_helper.install("/A.app/Contents/MacOS/x", **self.kw)
        self.assertTrue(ok)
        self.assertEqual(self.opened, [])
        self.assertIn("もう入っています", message)
        self.assertIn("無効", message)                                          # 有効にし忘れていることが分かる

    def test_without_keyboard_maestro(self):
        self.km_app.rmdir()
        ok, message = km_helper.install("/A.app/Contents/MacOS/x", **self.kw)
        self.assertFalse(ok)
        self.assertIn("Keyboard Maestro が見つかりません", message)
        self.assertEqual(self.opened, [])

    def test_broken_definition_file_is_read_as_empty(self):
        self.km_plist.write_text("壊れている", encoding="utf-8")
        self.assertEqual(K.installed_macros(self.km_plist), {})
        self.assertEqual(K.installed_macros(self.tmp / "none.plist"), {})


if __name__ == "__main__":
    unittest.main()
