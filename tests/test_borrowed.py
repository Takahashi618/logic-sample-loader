"""借りた部品の包み（ui/borrowed.py）の確認。**本物の部品は差し替える**（読み上げ名の付き方だけを見る）。"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LOGICLOADER_SILENT", "1")

from ui import borrowed  # noqa: E402


class _Ctrl:
    def __init__(self):
        self.names = []

    def SetName(self, name):
        self.names.append(name)


class LabelControlTest(unittest.TestCase):
    """名前が付いたら、ツールチップと空欄表示は入れない（VoiceOver が同じ文を二度読む。2026-10-13 に実測）。
    2026-10-24 のレビューで「入れてから消す」をやめ、名前が付かなかったときだけ保険を入れるようにした。"""

    def setUp(self):
        self.saved = borrowed._ax_label
        self.calls = []

    def tearDown(self):
        borrowed._ax_label = self.saved

    def test_the_borrowed_part_decides(self):
        borrowed._ax_label = SimpleNamespace(
            label_control=lambda ctrl, label: self.calls.append(label))
        borrowed.label_control(_Ctrl(), "検索語")
        self.assertEqual(self.calls, ["検索語"])

    def test_without_the_borrowed_part_only_the_wx_name(self):
        borrowed._ax_label = None
        ctrl = _Ctrl()
        borrowed.label_control(ctrl, "検索語")
        self.assertEqual(ctrl.names, ["検索語"])


if __name__ == "__main__":
    unittest.main()
