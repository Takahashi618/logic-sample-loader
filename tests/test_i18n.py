"""表示の言語（core/i18n.py）の確認。

- 言語の決め方（Mac の「優先する言語」の上から、日本語か英語の先に出るほう。どちらも無ければ英語）
- 補助（tools/qs_sample_menu.py）が写して持っている決め方が、core/i18n.py と食い違っていないこと
- **英語で起動したとき、画面・メニュー・読み上げに日本語が混ざらないこと**（別のプロセスで英語にして集める。
  tests/en_probe.py。言語は起動したときに決まるので、このプロセスの中では切り替えられない）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core import i18n  # noqa: E402
import qs_sample_menu as qs  # noqa: E402

JAPANESE = re.compile(r"[ぁ-んァ-ヶ一-龠]")


class PickLanguageTest(unittest.TestCase):
    CASES = [
        (["ja-JP", "en-JP"], "ja"),
        (["en-US", "ja-JP"], "en"),
        (["en-GB"], "en"),
        (["fr-FR", "ja-JP"], "ja"),        # 日本語と英語のうち、先に出るほう
        (["fr-FR", "de-DE"], "en"),        # どちらも無ければ英語
        ([], "en"),
        (["ja_JP.UTF-8"], "ja"),           # 環境変数 LANG の書き方
        (["zh-Hans-CN", "en-CN"], "en"),
    ]

    def test_cases(self):
        for langs, want in self.CASES:
            with self.subTest(langs=langs):
                self.assertEqual(i18n.pick_language(langs), want)

    def test_helper_copy_agrees(self):
        """補助は core を読み込めないので、同じ決め方を写して持っている。食い違えば画面と補助で言語が変わる。"""
        for langs, want in self.CASES:
            with self.subTest(langs=langs):
                self.assertEqual(qs.pick_language(langs), want)
        self.assertEqual(qs.LANG_ENV_NAME, i18n.ENV_NAME)
        self.assertEqual(qs.LANG, i18n.LANG)

    def test_tests_run_in_japanese(self):
        """テストは日本語の文言で書いてある（tests/__init__.py が LOGICLOADER_LANG=ja にする）。"""
        self.assertEqual(i18n.LANG, "ja")
        self.assertEqual(i18n.sentences(["Audio 1", "トラック 2。"]), "Audio 1。トラック 2。")


def _probe(lang: str) -> dict:
    env = dict(os.environ, LOGICLOADER_LANG=lang, LOGICLOADER_SILENT="1", SM_SILENT="1")
    done = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tests" / "en_probe.py")], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])


class EnglishScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import wx  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("wxPython が無い")
        cls.en = _probe("en")

    def test_language_is_english_in_app_and_helper(self):
        self.assertEqual(self.en["lang"], ["en", "en"])

    def test_no_japanese_anywhere(self):
        """画面の部品・メニュー・設定の画面・補助の文・組み立てた読み上げのどこにも、日本語が残っていない。"""
        for group, texts in self.en.items():
            for text in texts:
                with self.subTest(group=group, text=text[:60]):
                    self.assertIsNone(JAPANESE.search(text))

    def test_menus_and_key_names(self):
        self.assertEqual(self.en["menus"], ["Actions", "Help"])
        self.assertIn("Next Pad\tCtrl+]", self.en["menu_items"])
        # 記号のキーは名前で書く（VoiceOver が ] と [ を読まなかった）
        self.assertIn("Command-Right Bracket (])", self.en["key_help"][0])
        self.assertIsNone(re.search(r"Command-[\[\]]", self.en["key_help"][0]))

    def test_every_shortcut_in_english_help_exists_in_menu(self):
        """英語のキー操作の一覧に書いたキーも、どれもメニューにある（日本語の一覧と同じ確かめ方）。"""
        accels = {t.split("\t", 1)[1] for t in self.en["menu_items"] if "\t" in t}
        names = {"Right Bracket": "]", "Left Bracket": "["}
        found = re.findall(r"(?<!Option-)Command-(Shift-)?([A-Z0-9](?![a-z])|Right Bracket|Left Bracket)",
                           self.en["key_help"][0])
        self.assertGreater(len(found), 10)
        for shift, key in found:
            accel = "Ctrl+" + ("Shift+" if shift else "") + names.get(key, key)
            with self.subTest(key=accel):
                self.assertIn(accel, accels)

    def test_sentences_read_naturally(self):
        phrases = self.en["phrases"]
        self.assertEqual(phrases[0], "Audio 1, track 4, audio, mono. Bar 3 has Loop (from the start of bar 3 to the "
                                     "start of bar 5). Other tracks are also selected")
        self.assertIn("Playhead at beat 2 of bar 3", phrases[1])
        self.assertIn("kick.wav, playing, at 0.0 s, length 8.0 s", self.en["controller"])
        self.assertIn("90 BPM to half of 120 BPM (60 BPM). Key stays F# minor", self.en["fit"])
        self.assertIn("Logic Sample Loader", self.en["window"][0])
        self.assertIn("Made with AI (Anthropic's Claude)", self.en["window"][2])


if __name__ == "__main__":
    unittest.main()
