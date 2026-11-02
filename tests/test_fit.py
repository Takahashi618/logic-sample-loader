"""core/fit.py の単体テスト（音には触らない）。

素材の値は Splice の実物（2026-09-25 に Logic へ貼って確かめたもの）:
  DS_LENNO_102_guitar_electric_wistful_muted_perfect_F#min.wav  102 BPM、F# マイナー、2 小節、
  44.1 kHz、207,529 サンプル。120 BPM・C メジャーのプロジェクトへ → 3 半音上げ（A マイナー）、
  長さ 0.85 倍、ちょうど 176,400 サンプル（4 秒 = 2 小節）
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fit  # noqa: E402

LENNO = fit.Source(name="DS_LENNO_102_guitar_electric_wistful_muted_perfect_F#min.wav", frames=207529,
                   sample_rate=44100, channels=2, kind="loop", bpm=102, bpm_confident=True,
                   key="F#", key_quality="minor", key_confident=True)
PROJECT = fit.Goal(tempo=120.0, key="Cmaj", sample_rate=44100)


class KeyWordsTest(unittest.TestCase):
    def test_logic_key_signature(self):
        self.assertEqual(fit.logic_key_query("C Major"), "Cmaj")
        self.assertEqual(fit.logic_key_query("A Minor"), "Am")
        self.assertEqual(fit.logic_key_query("F♯ Minor"), "F#m")
        self.assertEqual(fit.logic_key_query("B♭ Major"), "A#maj")
        self.assertEqual(fit.logic_key_query("Db major"), "C#maj")
        self.assertEqual(fit.logic_key_query(""), "")
        self.assertEqual(fit.logic_key_query("Custom"), "")
        # 日本語の表示の Logic（2026-11-02 実測。間を空けない）
        self.assertEqual(fit.logic_key_query("Cメジャー"), "Cmaj")
        self.assertEqual(fit.logic_key_query("F♯マイナー"), "F#m")
        self.assertEqual(fit.logic_key_query("B♭メジャー"), "A#maj")
        self.assertEqual(fit.logic_key_query("カスタム"), "")

    def test_labels_and_choices(self):
        self.assertEqual(fit.key_label("Am"), "Aマイナー")
        self.assertEqual(fit.key_label("C#maj"), "C#メジャー")
        choices = fit.key_choices()
        self.assertEqual(len(choices), 26)
        self.assertEqual([v for _, v in choices[:2]], [fit.KEY_PROJECT, fit.KEY_OFF])
        self.assertIn(("Aマイナーに合わせる", "Am"), choices)
        transpose = fit.transpose_choices()
        self.assertEqual([v for _, v in transpose], list(range(-12, 13)))
        self.assertEqual(transpose[12], ("移調しない", 0))
        self.assertEqual(transpose[13], ("1 半音上げる", 1))

    def test_format_bpm(self):
        self.assertEqual(fit.format_bpm(120.0), "120")
        self.assertEqual(fit.format_bpm(123.5), "123.5")


class PlanTest(unittest.TestCase):
    def test_measured_loop_into_project(self):
        p = fit.plan_fit(LENNO, PROJECT)
        self.assertAlmostEqual(p.ratio, 0.85)
        self.assertEqual((p.key_shift, p.semitones, p.result_key), (3, 3, "Am"))
        self.assertEqual(p.out_frames, 176400)          # ちょうど 2 小節（4 秒）
        self.assertTrue(p.needs_render)
        self.assertEqual(p.tag(), "120bpm_even_+3_Am")
        self.assertEqual(p.describe_ja(), "102 BPM を 120 BPM に。F#マイナーをAマイナーに（3 半音上げ）")
        self.assertIn("速さを大きく変えたので、音が荒れることがあります", p.notes)   # 15% 縮めた

    def test_relative_key_is_not_moved(self):
        # A マイナーの素材を C メジャーへ: 平行調なので移調しない
        p = fit.plan_fit(replace(LENNO, key="A"), fit.Goal(key="Cmaj", sample_rate=44100))
        self.assertEqual(p.semitones, 0)
        self.assertFalse(p.needs_render)
        self.assertEqual(p.describe_ja(), "キーはAマイナーのまま")

    def test_suggest_scale(self):
        self.assertEqual(fit.suggest_scale(87, 172), fit.TEMPO_HALF)
        self.assertEqual(fit.suggest_scale(172, 87), fit.TEMPO_DOUBLE)
        self.assertEqual(fit.suggest_scale(120, 90), fit.TEMPO_EVEN)
        self.assertEqual([fit.tempo_word(v) for _, v in fit.TEMPO_CHOICES], ["ハーフ", "Even", "ダブル"])

    def test_half_and_double_are_based_on_the_project_tempo(self):
        # 基準はプロジェクトの BPM（素材の BPM ではない）。120 のプロジェクトで、ハーフは 60、ダブルは 240 に合わせる
        half = fit.plan_fit(LENNO, replace(PROJECT, tempo_scale=fit.TEMPO_HALF))
        self.assertEqual(half.target_tempo, 60.0)
        self.assertAlmostEqual(half.ratio, 1.7)                    # 102 → 60（長くなる）
        self.assertEqual(half.out_frames, 352800)                  # ちょうど 2 小節が 60 BPM で 8 秒
        self.assertEqual(half.tag(), "120bpm_half_+3_Am")
        self.assertIn("102 BPM を、120 BPM のハーフ（60 BPM）に", half.describe_ja())
        double = fit.plan_fit(LENNO, replace(PROJECT, tempo_scale=fit.TEMPO_DOUBLE))
        self.assertEqual(double.target_tempo, 240.0)
        self.assertAlmostEqual(double.ratio, 0.425)
        self.assertEqual(double.out_frames, 88200)                 # 240 BPM で 2 秒
        self.assertEqual(double.tag(), "120bpm_double_+3_Am")
        self.assertIn("102 BPM を、120 BPM のダブル（240 BPM）に", double.describe_ja())
        # 3 つとも名前が違う（同じ名前だと、作ってある別の速さの音を使い回してしまう）
        self.assertEqual(len({half.tag(), double.tag(), fit.plan_fit(LENNO, PROJECT).tag()}), 3)

    def test_even_no_longer_reinterprets_half_counted_bpm(self):
        # 2026-10-12 までは 87 を「倍の 174」とみなして速さを変えなかった。いまはファイル名のとおり 87 → 174 にし、
        # ハーフを勧める。ハーフを選べば 87 のまま
        loop87 = replace(LENNO, bpm=87, frames=int(round(8 * 60 / 87 * 44100)))
        even = fit.plan_fit(loop87, fit.Goal(tempo=174.0, sample_rate=44100))
        self.assertAlmostEqual(even.ratio, 0.5)
        self.assertEqual(even.tag(), "174bpm_even")
        self.assertIn("ハーフにすると、伸び縮みが小さくなります", even.notes)
        half = fit.plan_fit(loop87, fit.Goal(tempo=174.0, tempo_scale=fit.TEMPO_HALF, sample_rate=44100))
        self.assertFalse(half.tempo_changed)
        self.assertFalse(half.needs_render)
        self.assertEqual(half.describe_ja(), "87 BPM は 174 BPM のハーフ（87 BPM）と同じなので、速さはそのまま")
        # 自分で選んだハーフ・ダブルには、別のものを勧めない
        self.assertNotIn("Evenにすると、伸び縮みが小さくなります",
                         fit.plan_fit(LENNO, replace(PROJECT, tempo_scale=fit.TEMPO_HALF)).notes)

    def test_one_shot_ignores_half_and_double(self):
        shot = replace(LENNO, kind="one_shot", frames=22050)
        p = fit.plan_fit(shot, replace(PROJECT, tempo_scale=fit.TEMPO_DOUBLE))
        self.assertFalse(p.tempo_changed)
        self.assertFalse(p.tempo_fitted)
        self.assertEqual(p.tag(), "+3_Am")
        # BPM がファイル名にあっても、「120 BPM のダブル（240 BPM）と同じなので」とは言わない（2026-10-13 のレビュー。
        # 140 BPM のワンショットをハーフで貼ると「140 BPM は 120 BPM のハーフ（60 BPM）と同じなので」と読んでいた）
        for scale in (fit.TEMPO_HALF, fit.TEMPO_DOUBLE):
            said = fit.plan_fit(replace(shot, bpm=140), replace(PROJECT, tempo_scale=scale)).describe_ja()
            self.assertNotIn("BPM", said, said)
            self.assertEqual(said, "F#マイナーをAマイナーに（3 半音上げ）")
        self.assertFalse(fit.tempo_applies("one_shot"))
        self.assertFalse(fit.tempo_applies("fx"))
        self.assertTrue(fit.tempo_applies("loop"))
        self.assertTrue(fit.tempo_applies(""))                     # 種別が分からなければ合わせる

    def test_transpose_on_top_and_unique_names(self):
        p = fit.plan_fit(LENNO, replace(PROJECT, transpose=12))
        self.assertEqual((p.semitones, p.result_key), (15, "Am"))
        self.assertEqual(p.tag(), "120bpm_even_+15_Am")      # 1 オクターブ上でも名前は別（同じ名前だと音を取り違える）
        self.assertNotEqual(p.tag(), fit.plan_fit(LENNO, PROJECT).tag())
        self.assertIn("音程を大きく変えたので、音色が変わって聞こえることがあります", p.notes)

    def test_unknown_key_uses_transpose_only(self):
        drum = replace(LENNO, key=None, key_quality=None)
        p = fit.plan_fit(drum, replace(PROJECT, transpose=-2))
        self.assertEqual((p.key_shift, p.semitones, p.result_key), (0, -2, ""))
        self.assertEqual(p.tag(), "120bpm_even_-2")
        self.assertIn("2 半音下げ", p.describe_ja())
        self.assertIn("素材のキーが分からないので、キーは合わせていません", p.notes)

    def test_sample_rate_only(self):
        p = fit.plan_fit(replace(LENNO, sample_rate=48000, frames=225882), fit.Goal(sample_rate=44100))
        self.assertTrue(p.needs_render)                  # Logic に変換させると、元の素材の隣に複製を書く
        self.assertEqual(p.tag(), "44.1k")
        self.assertEqual(p.out_frames, int(round(225882 * 44100 / 48000)))

    def test_one_shot_keeps_its_speed(self):
        shot = replace(LENNO, kind="one_shot", bpm=120, frames=22050)
        p = fit.plan_fit(shot, fit.Goal(tempo=90.0, key="Cmaj", sample_rate=44100))
        self.assertFalse(p.tempo_changed)
        self.assertEqual(p.semitones, 3)                 # キーだけ合わせる（ベースの単発音など）

    def test_nothing_to_do(self):
        p = fit.plan_fit(LENNO, fit.Goal(tempo=102.0, key="F#m", sample_rate=44100))
        self.assertFalse(p.needs_render)
        self.assertEqual(p.out_frames, LENNO.frames)

    def test_bpm_that_does_not_fit_the_length_is_reported(self):
        odd = replace(LENNO, frames=200000)              # 102 BPM の 2 小節より 7,529 サンプル短い
        p = fit.plan_fit(odd, PROJECT)
        self.assertIn("ループの長さが BPM と合わないので、テンポがずれるかもしれません", p.notes)
        self.assertEqual(p.out_frames, int(round(200000 * 0.85)))

    def test_unsure_source_values_are_reported(self):
        p = fit.plan_fit(replace(LENNO, bpm_confident=False, key_confident=False), PROJECT)
        self.assertIn("素材の BPM はファイル名から推し量った値です", p.notes)
        self.assertIn("素材のキーはファイル名から推し量った値です", p.notes)

    def test_rules_come_from_ptcore(self):
        # しきい値と小節の丸めは ptcore/prep.py のもの（写しを持たない。2026-09-26 のレビュー）
        from core.sibling import load_ptcore
        prep = load_ptcore("prep")
        self.assertFalse(hasattr(fit, "_nearest_bars"))
        self.assertFalse(hasattr(fit, "STRETCH_WARN"))
        just_over = replace(LENNO, bpm=102, frames=int(round(207529 * (1 + prep.BAR_FIT_TOLERANCE * 3))))
        self.assertIn("ループの長さが BPM と合わないので、テンポがずれるかもしれません",
                      fit.plan_fit(just_over, PROJECT).notes)

    def test_without_ptcore_nothing_is_fitted(self):
        import core.fit as module
        saved = module.load_ptcore
        module.load_ptcore = lambda name: None
        try:
            p = fit.plan_fit(replace(LENNO, sample_rate=48000, frames=225882), replace(PROJECT, transpose=2))
        finally:
            module.load_ptcore = saved
        self.assertEqual((p.ratio, p.semitones), (1.0, 0))              # テンポもキーも移調も合わせない
        self.assertEqual((p.out_rate, p.out_frames), (44100, int(round(225882 * 44100 / 48000))))
        self.assertIn(fit.NO_PTCORE_NOTE, p.notes)

    def test_source_from_entry(self):
        class E:
            filename, bpm, bpm_confidence = "x_120_Am.wav", 120, "high"
            key, key_quality, key_confidence, kind = "A", "minor", "medium", "loop"
            channels, sample_rate, duration = 2, 48000, 2.0
        s = fit.source_from_entry(E())
        self.assertEqual((s.frames, s.sample_rate, s.key_query, s.bpm_confident, s.key_confident),
                         (96000, 48000, "Am", True, False))
        self.assertEqual(fit.source_from_entry(E(), frames=95999, kind="one_shot").frames, 95999)


if __name__ == "__main__":
    unittest.main()
