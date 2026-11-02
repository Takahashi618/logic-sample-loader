"""core/settings.py の単体テスト。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import settings as S  # noqa: E402


class PolicyTest(unittest.TestCase):
    def test_defaults_keep_speed_for_loops_only(self):
        d = S.LoadSettings()
        self.assertEqual((d.mode, d.flex, d.follow, d.after, d.auto_preview), ("original", "auto", "off", "back", False))
        self.assertEqual((d.target, d.pad_advance), ("qs", True))
        self.assertTrue(S.flex_for(d, "loop"))
        self.assertFalse(S.flex_for(d, "one_shot"))
        self.assertFalse(S.flex_for(d, "fx"))
        self.assertFalse(S.flex_for(d, ""))
        self.assertFalse(S.follow_for(d))

    def test_always_and_never(self):
        self.assertTrue(S.flex_for(replace(S.LoadSettings(), flex="on"), "one_shot"))
        self.assertFalse(S.flex_for(replace(S.LoadSettings(), flex="off"), "loop"))
        self.assertTrue(S.follow_for(replace(S.LoadSettings(), follow="on")))

    def test_describe_is_japanese(self):
        text = S.describe(S.LoadSettings())
        self.assertIn("Original", text)
        self.assertIn("Flex は素材に合わせる", text)
        self.assertIn("Follow Tempo は切る", text)
        self.assertIn("このアプリに戻る", text)
        self.assertIn("自動で試聴", S.describe(replace(S.LoadSettings(), auto_preview=True)))

    def test_track_settings(self):
        d = S.LoadSettings()
        self.assertEqual((d.fit_tempo, d.snap, d.playhead), (True, "off", "end"))   # 位置は Logic の決まり。貼ったら進める
        self.assertIn("track", S.TARGET_LABELS)
        track = replace(d, target="track")
        text = S.describe(track)
        self.assertTrue(text.startswith("テンポを合わせる、貼る位置は再生位置のある小節の頭、"
                                        "再生位置は貼ったオーディオの終わりへ進める"))
        self.assertIn("再生位置は貼ったオーディオの頭に置く", S.describe(replace(track, playhead="start")))
        self.assertIn("再生位置は動かさない", S.describe(replace(track, playhead="stay")))
        self.assertEqual(list(S.PLAYHEAD_LABELS), ["end", "start", "stay"])   # 設定の画面に並ぶ順
        self.assertNotIn("Flex", text)                             # いまの読み込み先に効かないものは言わない
        self.assertIn("いちばん近い小節の頭", S.describe(replace(track, snap="bar", fit_tempo=False)))
        self.assertIn("テンポは合わせない", S.describe(replace(track, fit_tempo=False)))
        self.assertIn("パッドは次へ進む", S.describe(replace(d, target="dmd")))
        self.assertNotIn("パッド", S.describe(d))

    def test_every_choice_has_a_label(self):
        for key, labels in S.CHOICES.items():
            with self.subTest(key=key):
                self.assertIn(getattr(S.LoadSettings(), key), labels)
                self.assertTrue(all(labels.values()))


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "sub" / "settings.json"

    def test_round_trip(self):
        want = S.LoadSettings(mode="optimized", flex="on", follow="on", after="stay", auto_preview=True,
                              pack_order="name", target="track", pad_advance=False, fit_tempo=False, snap="bar",
                              playhead="start")
        S.save_settings(want, self.path)
        self.assertEqual(S.load_settings(self.path), want)
        self.assertFalse(self.path.with_name("settings.json.tmp").exists())

    def test_old_advance_setting_is_carried_over(self):
        # 1.4 までは「貼ったら再生位置を進める」の入り切りだった。書いてあった値を、そのまま引き継ぐ
        self.path.parent.mkdir(parents=True)
        for advance, want in ((True, "end"), (False, "stay")):
            with self.subTest(advance=advance):
                self.path.write_text(json.dumps({"target": "track", "advance": advance}), encoding="utf-8")
                self.assertEqual(S.load_settings(self.path).playhead, want)
        self.path.write_text(json.dumps({"advance": False, "playhead": "start"}), encoding="utf-8")
        self.assertEqual(S.load_settings(self.path).playhead, "start")      # 新しい書き方が優先
        self.path.write_text(json.dumps({"playhead": "somewhere"}), encoding="utf-8")
        self.assertEqual(S.load_settings(self.path).playhead, "end")        # 知らない値は既定に戻す
        S.save_settings(S.load_settings(self.path), self.path)
        self.assertNotIn("advance", json.loads(self.path.read_text(encoding="utf-8")))

    def test_missing_or_broken_file_gives_defaults(self):
        self.assertEqual(S.load_settings(self.path), S.LoadSettings())
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{壊れている", encoding="utf-8")
        self.assertEqual(S.load_settings(self.path), S.LoadSettings())
        self.path.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(S.load_settings(self.path), S.LoadSettings())

    def test_unknown_values_fall_back(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"mode": "fast", "flex": "on", "auto_preview": "yes", "extra": 1,
                                         "target": "battery", "pad_advance": 0, "snap": "beat",
                                         "fit_tempo": "no"}), encoding="utf-8")
        got = S.load_settings(self.path)
        self.assertEqual((got.snap, got.fit_tempo), ("off", True))
        self.assertEqual(got.mode, "original")
        self.assertEqual(got.flex, "on")
        self.assertFalse(got.auto_preview)
        self.assertEqual(got.target, "qs")
        self.assertTrue(got.pad_advance)

    def test_splice_root_is_kept(self):
        S.save_settings(S.LoadSettings(splice_root="/Volumes/Samples/Splice"), self.path)
        self.assertEqual(S.load_settings(self.path).splice_root, "/Volumes/Samples/Splice")
        self.path.write_text(json.dumps({"splice_root": 3}), encoding="utf-8")
        self.assertEqual(S.load_settings(self.path).splice_root, "")       # 文字でなければ自動に戻す


class SpliceRootTest(unittest.TestCase):
    """Splice のフォルダ（2026-10-24。Splice のフォルダは利用者が動かせるので、配布版で選び直せるようにした）。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_choosing_the_splice_folder_itself_uses_its_packs(self):
        packs = self.home / "Splice" / "sounds" / "packs"
        packs.mkdir(parents=True)
        self.assertEqual(S.resolve_splice_root(str(self.home / "Splice")), packs)
        self.assertEqual(S.resolve_splice_root(str(packs)), packs)          # packs を選んでもそのまま
        old = self.home / "Old" / "Samples" / "packs"
        old.mkdir(parents=True)
        self.assertEqual(S.resolve_splice_root(str(self.home / "Old")), old)
        plain = self.home / "MySamples"
        plain.mkdir()
        self.assertEqual(S.resolve_splice_root(str(plain)), plain)          # 中に packs が無ければ、選んだフォルダ

    def test_empty_means_the_first_candidate_that_exists(self):
        saved = S.SPLICE_CANDIDATES
        try:
            S.SPLICE_CANDIDATES = (self.home / "a" / "packs", self.home / "b" / "packs")
            self.assertEqual(S.resolve_splice_root(""), self.home / "a" / "packs")     # どれも無ければ最初
            (self.home / "b" / "packs").mkdir(parents=True)
            self.assertEqual(S.resolve_splice_root(""), self.home / "b" / "packs")
        finally:
            S.SPLICE_CANDIDATES = saved

    def test_index_of_the_distribution_is_outside_the_app(self):
        self.assertEqual(S.INDEX_PATH.parent, S.SETTINGS_PATH.parent)
        self.assertNotIn(".app", str(S.INDEX_PATH))


if __name__ == "__main__":
    unittest.main()
