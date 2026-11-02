"""ui/speech.py の単体テスト（VoiceOver には送らない。送る部分は差し替える）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ui import borrowed, speech  # noqa: E402


class SpeechTest(unittest.TestCase):
    def setUp(self):
        self.saved = (speech._post_native, borrowed.announce, os.environ.get(borrowed.SILENT_ENV))
        self.sent, self.fallback = [], []
        speech._post_native = lambda message, high: self.sent.append((message, high)) or True
        borrowed.announce = lambda message: self.fallback.append(message)
        os.environ.pop(borrowed.SILENT_ENV, None)

    def tearDown(self):
        speech._post_native, borrowed.announce, silent = self.saved
        if silent is None:
            os.environ.pop(borrowed.SILENT_ENV, None)
        else:
            os.environ[borrowed.SILENT_ENV] = silent

    def test_native_notification_first_with_high_priority(self):
        # System Events で AXAnnouncement を書く方法は -10006 で断られていた（2026-09-26）。通知で頼む
        self.assertEqual(speech.announce("Audio 2、トラック 3"), speech.NATIVE)
        self.assertEqual(self.sent, [("Audio 2、トラック 3", True)])   # 読み上げ中でも割り込む
        self.assertEqual(self.fallback, [])

    def test_fallback_only_when_the_notification_cannot_be_sent(self):
        speech._post_native = lambda message, high: False
        self.assertEqual(speech.announce("結果"), speech.FALLBACK)
        self.assertEqual(self.fallback, ["結果"])

    def test_silent_and_empty(self):
        self.assertEqual(speech.announce(""), speech.NOTHING)
        os.environ[borrowed.SILENT_ENV] = "1"
        self.assertEqual(speech.announce("結果"), speech.SILENT)
        self.assertEqual((self.sent, self.fallback), ([], []))       # 試験や代わりに動かすときは黙る

    def test_real_post_does_not_raise_without_a_window(self):
        # 窓の無いところ（テスト）で呼んでも例外にならない。送れたかどうかだけを返す
        speech._post_native = self.saved[0]
        self.assertIn(speech._post_native("", False), (True, False))


if __name__ == "__main__":
    unittest.main()
