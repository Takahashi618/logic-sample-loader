"""qs_sample_menu の単体テスト（AX を使わない部分）。

座標の期待値は 2026-09-24 に Logic Pro 12.3 で実測した値:
  エディタ表示の窓 (596, 258) 1028x796、Sample name の欄 (1012, 355) 294x30。

    python3.14 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import qs_sample_menu as qs  # noqa: E402

MEASURED_WINDOW = qs.Frame(596, 258, 1028, 796)
MEASURED_POPUP = qs.Frame(1012, 355, 294, 30)


class ProbePointsTest(unittest.TestCase):
    def test_first_point_hits_measured_popup(self):
        x, y = qs.probe_points(MEASURED_WINDOW)[0]
        self.assertTrue(MEASURED_POPUP.contains(x, y), (x, y))

    def test_neighbours_stay_inside_popup_at_100_percent(self):
        # 最初の 7 点（中心とその周り）は、どれも欄の中に落ちる
        for x, y in qs.probe_points(MEASURED_WINDOW)[:7]:
            with self.subTest(point=(x, y)):
                self.assertTrue(MEASURED_POPUP.contains(x, y))

    def test_scales_with_window_width(self):
        # 表示サイズ 150% を想定: 窓の幅が 1.5 倍なら、中心も描画域の左上から 1.5 倍の距離
        big = qs.Frame(100, 100, 1028 * 1.5, 796 * 1.5)
        x, y = qs.probe_points(big)[0]
        self.assertAlmostEqual(x, 100 + 2 + 561 * 1.5, places=3)
        self.assertAlmostEqual(y, 100 + 91 + 21 * 1.5, places=3)

    def test_coarse_scan_covers_upper_area(self):
        pts = qs.probe_points(MEASURED_WINDOW)
        self.assertGreater(len(pts), 50)
        self.assertTrue(all(MEASURED_WINDOW.contains(x, y) for x, y in pts))


class LooksLikePopupTest(unittest.TestCase):
    def test_empty_and_loaded(self):
        self.assertTrue(qs.looks_like_sample_popup("AXPopUpButton", "No audio file loaded", 294, 1.0))
        self.assertTrue(qs.looks_like_sample_popup("AXPopUpButton", "OS_SHE_synth_bass_moog_C.wav", 294, 1.0))
        self.assertTrue(qs.looks_like_sample_popup("AXPopUpButton", "Kick.AIF", 120, 1.0))

    def test_rejects_other_controls(self):
        # Snap の欄（Zero-X）は幅が狭い。ボタンや文字は論外
        self.assertFalse(qs.looks_like_sample_popup("AXPopUpButton", "Zero-X", 110, 1.0))
        self.assertFalse(qs.looks_like_sample_popup("AXButton", "No audio file loaded", 294, 1.0))
        self.assertFalse(qs.looks_like_sample_popup(None, None, 0, 1.0))

    def test_unknown_text_falls_back_to_width(self):
        # 表示言語が違う・拡張子が無い名前でも、欄の幅があれば欄とみなす
        self.assertTrue(qs.looks_like_sample_popup("AXPopUpButton", "Recording #1", 290, 1.0))
        self.assertFalse(qs.looks_like_sample_popup("AXPopUpButton", "Recording #1", 150, 1.0))


class PickWindowTest(unittest.TestCase):
    def test_prefers_focused(self):
        same = lambda a, b: a == b
        self.assertEqual(qs.pick_window(["a", "b"], "b", same), "b")

    def test_falls_back_to_front(self):
        same = lambda a, b: a == b
        self.assertEqual(qs.pick_window(["a", "b"], "z", same), "a")
        self.assertEqual(qs.pick_window(["a"], None, same), "a")
        self.assertIsNone(qs.pick_window([], "a", same))


# 2026-09-25 に実測した Flex の群（窓は MEASURED_WINDOW と同じ）
MEASURED_FLEX = qs.Frame(1337, 651, 28, 21)
MEASURED_FOLLOW = qs.Frame(1381, 651, 130, 22)


class FollowProbeTest(unittest.TestCase):
    def test_first_points_hit_measured_follow_tempo(self):
        # 最初の 7 点（中心とその周り）は、どれも Follow Tempo の中に落ちる
        for x, y in qs.follow_probe_points(MEASURED_WINDOW)[:7]:
            with self.subTest(point=(x, y)):
                self.assertTrue(MEASURED_FOLLOW.contains(x, y))

    def test_coarse_scan_reaches_flex_and_stays_in_window(self):
        pts = qs.follow_probe_points(MEASURED_WINDOW)
        self.assertTrue(all(MEASURED_WINDOW.contains(x, y) for x, y in pts))
        # 粗い走査は Flex のボタンの上も通る（群の場所がずれても群には当たる）
        self.assertTrue(any(MEASURED_FLEX.contains(x, y) for x, y in pts))

    def test_scales_with_window_width(self):
        big = qs.Frame(100, 100, 1028 * 1.5, 796 * 1.5)
        x, y = qs.follow_probe_points(big)[0]
        self.assertAlmostEqual(x, 100 + 2 + 848 * 1.5, places=3)
        self.assertAlmostEqual(y, 100 + 91 + 313 * 1.5, places=3)


class PickFlexSiblingsTest(unittest.TestCase):
    same = staticmethod(lambda a, b: a == b)

    def test_measured_group(self):
        # 実測の並び: 名前なし（説明は空白 1 文字）→ Follow Tempo → Speed
        entries = [("AXCheckBox", " ", "flex"), ("AXCheckBox", "Follow Tempo", "follow"),
                   ("AXPopUpButton", "", "speed")]
        self.assertEqual(qs.pick_flex_siblings(entries, "follow", self.same), ("flex", "speed"))

    def test_named_checkbox_is_not_flex(self):
        entries = [("AXCheckBox", "Something", "other"), ("AXCheckBox", "Follow Tempo", "follow")]
        self.assertEqual(qs.pick_flex_siblings(entries, "follow", self.same), (None, None))

    def test_follow_itself_is_never_flex(self):
        entries = [("AXCheckBox", "", "follow")]
        self.assertEqual(qs.pick_flex_siblings(entries, "follow", self.same)[0], None)


class LoadedNameTest(unittest.TestCase):
    def test_exact_and_converted_copy(self):
        self.assertTrue(qs.loaded_name_matches("Loop_120.wav", "Loop_120.wav"))
        # サンプルレートが違うと Logic は「名前-44k.wav」を読み込む
        self.assertTrue(qs.loaded_name_matches("Loop_120-44k.wav", "Loop_120.wav"))

    def test_other_values(self):
        self.assertFalse(qs.loaded_name_matches("No audio file loaded", "Loop_120.wav"))
        self.assertFalse(qs.loaded_name_matches("", "Loop_120.wav"))
        self.assertFalse(qs.loaded_name_matches(None, "Loop_120.wav"))
        self.assertFalse(qs.loaded_name_matches("Loop_1200.wav", "Loop_120.wav"))
        self.assertFalse(qs.loaded_name_matches("Other.wav", "Loop_120.wav"))


class FlexWordingTest(unittest.TestCase):
    def test_flex_message_says_what_happens_to_speed(self):
        off = qs.FlexState(flex=False, follow=True, follow_enabled=False)
        on = qs.FlexState(flex=True, follow=False, follow_enabled=True)
        on_follow = qs.FlexState(flex=True, follow=True, follow_enabled=True)
        self.assertIn("速さも変わります", qs.flex_message(off))
        self.assertIn("速さは変わりません", qs.flex_message(on))
        self.assertNotIn("Follow Tempo", qs.flex_message(on))
        self.assertIn("プロジェクトのテンポ", qs.flex_message(on_follow))

    def test_follow_message(self):
        self.assertIn("入れました", qs.follow_message(qs.FlexState(True, True, True)))
        self.assertIn("元の速さ", qs.follow_message(qs.FlexState(True, False, True)))

    def test_state_phrase(self):
        self.assertEqual(qs.state_phrase(qs.FlexState(False, True, False)), "Flex は切り")
        self.assertEqual(qs.state_phrase(qs.FlexState(True, True, True)), "Flex は入り、Follow Tempo は入り")
        self.assertEqual(qs.state_phrase(None), "Flex の状態は分かりません")

    def test_on_off(self):
        self.assertIsNone(qs._on_off(None))
        self.assertIsNone(qs._on_off("toggle"))
        self.assertTrue(qs._on_off("on"))
        self.assertFalse(qs._on_off("off"))


class ContractTest(unittest.TestCase):
    def test_every_failure_has_a_message(self):
        codes = [v for k, v in vars(qs).items() if k.startswith("EXIT_") and k != "EXIT_OK"]
        self.assertEqual(len(codes), 26)
        for code in codes:
            with self.subTest(code=code):
                self.assertTrue(qs.MESSAGES[code])

    def test_scale_has_floor(self):
        self.assertEqual(qs.scale_of(qs.Frame(0, 0, 10, 10)), 0.25)
        self.assertAlmostEqual(qs.scale_of(MEASURED_WINDOW), 1.0)



# 2026-09-25 に実測した空のキットのパッドの見出し（窓の左上から。1 ページ目 16 個と 2 ページ目の 6 個）
MEASURED_PAD_LABELS = [
    (98, 106, "C2 - PERC 5"), (98, 175, "G♯1 - PEDAL HIHAT"), (98, 244, "E1 - SNARE 2"), (98, 313, "C1 - KICK 1"),
    (353, 106, "C♯2 - CRASH"), (353, 175, "A1 - PERC 3"), (353, 244, "F1 - PERC 1"), (353, 313, "C♯1 - RIM"),
    (608, 106, "D2 - SHAKER 2"), (608, 175, "A♯1 - OPEN HIHAT"), (608, 244, "F♯1 - CLOSED HIHAT"),
    (608, 313, "D1 - SNARE 1"),
    (863, 106, "D♯2 - RIDE"), (863, 175, "B1 - PERC 4"), (863, 244, "G1 - PERC 2"), (863, 313, "D♯1 - CLAP 1"),
    (1118, 106, "E3 - TIMBALE HIGH"), (1118, 175, "C3 - BONGO HIGH"), (1118, 244, "G♯2 - FX 4"),
    (1118, 313, "E2 - SNARE 3"),
    (1373, 106, "F3 - TIMBALE MID"), (1373, 175, "C♯3 - BONGO LOW"),
    (0, 0, "Empty Pad"), (185, 106, ""),
]
_NOTE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _midi(note: str) -> int:
    """Logic の音名（C3 = 60）を MIDI の番号にする。"""
    base = _NOTE[note[0]]
    rest = note[1:]
    if rest[:1] in ("♯", "#"):
        base, rest = base + 1, rest[1:]
    elif rest[:1] in ("♭", "b"):
        base, rest = base - 1, rest[1:]
    return base + (int(rest) + 2) * 12


class DmdPadTest(unittest.TestCase):
    def test_measured_labels_map_to_chromatic_pads(self):
        # 既定のキットでは、パッド n の音は C1（36）から n-1 半音上。位置からの割り当てがそれと合うか
        pads = qs.assign_pads(MEASURED_PAD_LABELS)
        self.assertEqual(len(pads), 22)
        for index, (note, _name) in pads.items():
            with self.subTest(pad=index, note=note):
                self.assertEqual(_midi(note), 35 + index)
        self.assertEqual(pads[1], ("C1", "KICK 1"))
        self.assertEqual(pads[16], ("D♯2", "RIDE"))
        self.assertEqual(pads[17], ("E2", "SNARE 3"))

    def test_scroll_offset_does_not_change_assignment(self):
        shifted = [(x - 1020, y + 7, t) for x, y, t in MEASURED_PAD_LABELS]   # 2 ページ目を表示した状態
        self.assertEqual(qs.assign_pads(shifted), qs.assign_pads(MEASURED_PAD_LABELS))

    def test_pad_points_match_logic_magician(self):
        # LM の Pad1 は窓の左上から (133, 299)、Pad9 は (133, 161)
        win = qs.Frame(446, 353, 1028, 732)
        self.assertEqual(qs.pad_point(win, 1), (446 + 133, 353 + 299))
        self.assertEqual(qs.pad_point(win, 9), (446 + 133, 353 + 161))
        self.assertEqual(qs.pad_point(win, 16), (446 + 133 + 255 * 3, 353 + 299 - 69 * 3))
        # 2 ページ目のパッド 17 は、ページを切り替えればパッド 1 と同じ位置
        self.assertEqual(qs.pad_point(win, 17), qs.pad_point(win, 1))

    def test_pad_position_range(self):
        self.assertEqual(qs.pad_position(1), (0, 0, 0))
        self.assertEqual(qs.pad_position(48), (2, 3, 3))
        for bad in (0, 49):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                qs.pad_position(bad)

    def test_parse_pad_label(self):
        self.assertEqual(qs.parse_pad_label("G♯1 - PEDAL HIHAT"), ("G♯1", "PEDAL HIHAT"))
        self.assertEqual(qs.parse_pad_label("C#2 - CRASH"), ("C#2", "CRASH"))
        self.assertEqual(qs.parse_pad_label("C-1 - LOW"), ("C-1", "LOW"))
        self.assertIsNone(qs.parse_pad_label("Empty Pad"))
        self.assertIsNone(qs.parse_pad_label(""))


class PanelEntryTest(unittest.TestCase):
    def test_nfc_and_candidates(self):
        decomposed = "パック"          # 「パ」が分解形（macOS のファイル名で起こる）
        self.assertEqual(qs.norm_name(decomposed), "パック")
        self.assertEqual(qs.entry_candidates("Kick.wav"), ("Kick.wav", "Kick"))
        self.assertEqual(qs.entry_candidates("Music", "ミュージック"), ("Music", "ミュージック"))

    def test_match_entry_prefers_real_name(self):
        rows = ["Kick", "Kick.wav", "Snare.wav"]
        self.assertEqual(qs.match_entry(rows, qs.entry_candidates("Kick.wav")), 1)
        # 拡張子を隠す設定なら、拡張子なしの名前で当たる
        self.assertEqual(qs.match_entry(["Kick", "Snare"], qs.entry_candidates("Kick.wav")), 0)
        self.assertEqual(qs.match_entry(["ミュージック"], qs.entry_candidates("Music", "ミュージック")), 0)
        self.assertIsNone(qs.match_entry(["Other.wav", None, ""], qs.entry_candidates("Kick.wav")))



class TrackPasteTest(unittest.TestCase):
    """オーディオトラックに貼る部分の読み方（2026-09-25 に Logic Pro 12.3 で読んだ表示そのまま）。"""

    def test_lcd_values(self):
        self.assertEqual(qs.parse_track_desc("Track 2 “Audio 1”"), (2, "Audio 1"))
        self.assertEqual(qs.parse_track_desc("Track 12 “Kick “A””"), (12, "Kick “A”"))
        self.assertIsNone(qs.parse_track_desc("Tracks header"))
        self.assertEqual(qs.parse_number("120.0"), 120.0)
        self.assertIsNone(qs.parse_number(None))
        self.assertEqual(qs.parse_meter("4/4"), (4, 4))
        self.assertEqual(qs.parse_meter("6/8"), (6, 8))
        self.assertIsNone(qs.parse_meter("0/4"))
        self.assertEqual(qs.parse_rate("44.1 kHz"), 44100)
        self.assertEqual(qs.parse_rate("48 kHz"), 48000)
        self.assertEqual(qs.parse_rate("88.2 kHz"), 88200)
        self.assertIsNone(qs.parse_rate("?"))
        self.assertEqual(qs.parse_division("/16"), 16)
        self.assertIsNone(qs.parse_division("16"))
        self.assertEqual(qs.key_phrase("C Major"), "C メジャー")
        self.assertEqual(qs.key_phrase("F♯ Minor"), "F♯ マイナー")

    def test_ticks_round_trip(self):
        for meter, division in (((4, 4), 16), ((3, 4), 16), ((6, 8), 16), ((4, 4), 8), ((4, 4), 4)):
            per_beat = max(1, (3840 // meter[1]) // (3840 // division))   # 1 拍の分割の数
            for pos in ((1, 1, 1, 1), (5, 2, 2, 100), (17, meter[0], 1, 1)):
                if pos[2] > per_beat:
                    continue
                with self.subTest(meter=meter, division=division, pos=pos):
                    ticks = qs.position_to_ticks(pos, meter, division)
                    self.assertEqual(qs.ticks_to_position(ticks, meter, division), pos)
        # 4/4・16 分: 1 拍 960、1 分割 240 ティック（LCD の tick は 1〜240）
        self.assertEqual(qs.position_to_ticks((2, 1, 1, 1), (4, 4), 16), 3840)
        self.assertEqual(qs.position_to_ticks((1, 2, 3, 100), (4, 4), 16), 960 + 480 + 99)

    def test_snap(self):
        m, d = (4, 4), 16
        self.assertEqual(qs.snap_position((17, 4, 1, 1), m, d, "bar"), (18, 1, 1, 1))
        self.assertEqual(qs.snap_position((17, 2, 4, 240), m, d, "bar"), (17, 1, 1, 1))
        self.assertEqual(qs.snap_position((17, 3, 1, 1), m, d, "bar"), (18, 1, 1, 1))   # ちょうど半分なら先へ
        self.assertEqual(qs.snap_position((5, 2, 3, 100), m, d, "beat"), (5, 3, 1, 1))
        self.assertEqual(qs.snap_position((5, 2, 2, 1), m, d, "beat"), (5, 2, 1, 1))
        self.assertEqual(qs.snap_position((5, 2, 3, 100), m, d, "off"), (5, 2, 3, 100))
        self.assertEqual(qs.snap_position((1, 3, 1, 1), (3, 4), d, "bar"), (2, 1, 1, 1))
        self.assertEqual(qs.SNAP_MODES, ("off", "bar"))

    def test_region_words(self):
        # 実測: 「Region starts at 1 bar  and ends at 3 bars , Audio region. …」
        start, end = qs.parse_region_help("Region starts at 1 bar  and ends at 3 bars , Audio region. An editable")
        self.assertEqual((start, end), ((1, 1, 1, 1), (3, 1, 1, 1)))
        span = qs.parse_region_help("Region starts at 1 bar  and ends at 2 bars 4 beats 4 divisions 177 ticks , Audio")
        self.assertEqual(span, ((1, 1, 1, 1), (2, 4, 4, 177)))
        self.assertIsNone(qs.parse_region_help("Workspace."))
        self.assertEqual(qs.parse_length_words("2 bars "), (2, 0, 0, 0))
        self.assertEqual(qs.parse_length_words("1 bar 3 beats 3 divisions 176 ticks "), (1, 3, 3, 176))
        self.assertEqual(qs.length_phrase((2, 0, 0, 0)), "2 小節")
        self.assertEqual(qs.length_phrase((1, 3, 3, 176)), "1 小節と 3 拍と少し")
        self.assertEqual(qs.length_phrase((0, 0, 2, 0)), "1 拍に満たない")
        self.assertEqual(qs.position_phrase((5, 1, 1, 1)), "5 小節目の頭")
        self.assertEqual(qs.position_phrase((5, 3, 1, 1)), "5 小節目の 3 拍目")
        self.assertEqual(qs.position_phrase((5, 2, 3, 100)), "5 小節目の 2 拍目の途中")

    def test_project_phrase(self):
        st = qs.ProjectState(0, tempo=120.0, key="C Major", meter=(4, 4), sample_rate=44100, division=16,
                             position=(1, 1, 1, 1), track_index=2, track_name="Audio 1", can_import=True, checked=True)
        self.assertEqual(qs.project_phrase(st), "貼り付け先は Audio 1（トラック 2）。プロジェクトは 120 BPM、"
                                               "C メジャー、4/4、44.1 kHz。再生位置は 1 小節目の頭")
        inst = qs.ProjectState(0, tempo=120.0, key="C Major", meter=(4, 4), track_index=1,
                               track_name="Classic Electric Piano", can_import=False, checked=True)
        self.assertIn("オーディオトラックではないので、貼れません", qs.project_phrase(inst))
        # インスペクタが出ていないと、メニューの値（古いことがある）を信じずに「確かめられません」と言う
        hidden = qs.ProjectState(0, tempo=120.0, track_index=2, track_name="Audio 1", can_import=True, checked=False)
        self.assertIn("インスペクタが出ていないので、オーディオトラックかどうか確かめられません", qs.project_phrase(hidden))
        self.assertNotIn("貼り付け先は", qs.project_phrase(hidden))
        self.assertTrue(qs.project_phrase(qs.ProjectState(0)).startswith("トラックが選ばれていません"))


def _el(**attrs):
    """AX の部品の代わり（属性の名前 → 値）。子は AXChildren に並べる。"""
    return dict(attrs)


class LogicJapaneseTest(unittest.TestCase):
    """Logic を日本語の表示にしたときの部品の名前と表示（2026-11-02 に Logic Pro 12.3 で読んだ表示そのまま）。"""

    def setUp(self):
        self._attr, self._main_window = qs.attr, qs.main_window
        qs.attr = lambda el, name: el.get(name) if el else None

    def tearDown(self):
        qs.attr, qs.main_window = self._attr, self._main_window

    def test_track_desc(self):
        self.assertEqual(qs.parse_track_desc("トラック4の“Audio 1”"), (4, "Audio 1"))
        self.assertEqual(qs.parse_track_desc("トラック12の“Kick “A””"), (12, "Kick “A”"))
        self.assertIsNone(qs.parse_track_desc("トラックヘッダ"))

    def test_region_words(self):
        # 実測: 1 は英語のまま、2 以上は日本語（「リージョンの開始位置は1 bar 、終了位置は3 小節 です, …」）
        help_ = "リージョンの開始位置は1 bar 、終了位置は3 小節 です, オーディオリージョン. 録音または読み込んだ"
        self.assertEqual(qs.parse_region_help(help_), ((1, 1, 1, 1), (3, 1, 1, 1)))
        span = qs.parse_region_help("リージョンの開始位置は5 小節 3拍 、終了位置は7 小節 2ディビジョン 100ティック です")
        self.assertEqual(span, ((5, 3, 1, 1), (7, 1, 2, 100)))
        self.assertEqual(qs.parse_length_words("2 小節 "), (2, 0, 0, 0))
        self.assertEqual(qs.parse_length_words("1 bar 3拍 3ディビジョン 176ティック "), (1, 3, 3, 176))
        self.assertEqual(qs.parse_length_words("1 bar 1 beat 1 division 1 tick "), (1, 1, 1, 1))

    def test_key_and_channel(self):
        self.assertEqual(qs.key_phrase("Cメジャー"), "C メジャー")
        self.assertEqual(qs.key_phrase("F♯マイナー"), "F♯ マイナー")
        self.assertEqual(qs.key_phrase("B♭メジャー"), "B♭ メジャー")
        self.assertEqual(qs.key_phrase("カスタム"), "カスタム")      # 読めないものはそのまま
        self.assertEqual(qs.CHANNEL_WORDS["モノラル"], "モノラル")
        self.assertEqual(qs.CHANNEL_WORDS["ステレオ"], "ステレオ")

    def test_names_have_both_languages(self):
        for names in (qs.MAIN_TITLE_SUFFIXES, qs.PART_CONTROL_BAR, qs.PART_INSPECTOR, qs.PART_TRACKS,
                      qs.TRACKS_HEADER, qs.TRACKS_CONTENTS, qs.PLAY_NAMES, qs.INPUT_SLOT_HELP, qs.RECORD_NAMES,
                      qs.CHANNEL_MODE_NAMES, qs.VIEW_CONTROLS, qs.VIEW_EDITOR, qs.WHERE_TITLES, *qs.IMPORT_MENU,
                      *qs.LCD_NAMES.values(), *qs.STRIP_HELP.values(), *qs.IMPORT_ALERTS.values()):
            with self.subTest(names=names):
                self.assertEqual(len(names), 2)
                self.assertTrue(names[0].isascii())
                self.assertFalse(names[1].isascii())
        self.assertTrue(qs.is_named("トラックヘッダ", qs.TRACKS_HEADER))
        self.assertFalse(qs.is_named(None, qs.TRACKS_HEADER))
        self.assertTrue(qs.starts_named("オーディオファイル…", qs.IMPORT_MENU[2]))

    def test_lcd_fields_in_japanese(self):
        def slider(desc):
            return _el(AXDescription=desc)
        playhead = _el(AXDescription="再生ヘッドの位置", AXHelp="再生ヘッドの位置（小節）, 再生ヘッド位置ディスプレイ. ",
                       AXChildren=[slider("bar"), slider("beat"), slider("ディビジョン"), slider("tick")])
        smpte = _el(AXDescription="再生ヘッドの位置", AXHelp="再生ヘッドの位置（SMPTE）, タイムディスプレイ. ")
        tempo, key = _el(AXDescription="テンポ", AXValue=120.0), _el(AXDescription="キー", AXValue="Cメジャー")
        meter, rate = _el(AXDescription="拍子", AXValue="4/4"), _el(AXDescription="サンプルレート", AXValue="48 kHz")
        division = _el(AXDescription="", AXHelp="ディビジョン, ディビジョンディスプレイ. ", AXValue="/16")
        inner = _el(AXRole="AXGroup", AXDescription="コントロールバー",
                    AXChildren=[playhead, smpte, rate, tempo, meter, key, division])
        play = _el(AXRole="AXCheckBox", AXDescription="再生", AXValue=1)
        win = _el(AXChildren=[_el(AXDescription="コントロールバー", AXChildren=[inner, play])])
        fields = qs.lcd_fields(win)
        self.assertEqual({k: fields[k] for k in ("Tempo", "Key Signature", "Time Signature", "Sample Rate")},
                         {"Tempo": tempo, "Key Signature": key, "Time Signature": meter, "Sample Rate": rate})
        self.assertIs(fields["Division"], division)
        self.assertIs(fields["Playhead"], playhead)
        self.assertEqual(sorted(qs._playhead_parts(fields)), ["bar", "beat", "division", "tick"])
        self.assertTrue(qs.transport_playing(win))

    def test_output_track_strips_are_both_right(self):
        # 実測: Stereo Out を選ぶと、左右の両方が「インスペクタの右チャンネルストリップ」になる
        right = "インスペクタの右チャンネルストリップ. インスペクタの左チャンネルストリップの出力"
        out = _el(AXDescription="Stereo Out", AXHelp=right)
        master = _el(AXDescription="Master", AXHelp=right)
        mixer = _el(AXDescription="ミキサー", AXChildren=[out, master])
        win = _el(AXChildren=[_el(AXDescription="インスペクタ", AXChildren=[_el(AXChildren=[mixer])])])
        qs.main_window = lambda app: win
        self.assertIs(qs.inspector_strip(None, "Left"), out)
        self.assertIs(qs.inspector_strip(None, "Right"), out)
        # ふつうのトラックでは名前どおり
        left = _el(AXDescription="Audio 1", AXHelp="インスペクタの左チャンネルストリップ. 選択したトラックの")
        mixer["AXChildren"] = [left, out]
        self.assertIs(qs.inspector_strip(None, "Left"), left)
        self.assertIs(qs.inspector_strip(None, "Right"), out)
        # インスペクタにストリップが無ければ None（出ていない）
        mixer["AXChildren"] = []
        self.assertIsNone(qs.inspector_strip(None, "Left"))

    def test_selected_pad_without_label(self):
        # 実測: チャンネルだけあって音源の無いパッドを選ぶと、見出し「D1 - SNARE 1」の代わりに
        # 「Input:」「D1」「Output:」「D1」が出る。見出しの位置の「D1」だけを、名前の分からない見出しにする
        texts = [(1054, 459, "D2 - SHAKER 2"), (1054, 597, "F♯1 - CLOSED HIHAT"),
                 (544, 666, "C1 - KICK 1"), (799, 666, "C♯1 - RIM"), (1309, 666, "D♯1 - CLAP 1"),
                 (1004, 666, "Input:"), (1054, 666, "D1"), (1087, 666, "Output:"), (1141, 666, "D1"),
                 (1054, 626, "Inst 5")]
        spots = qs.pad_label_spots(texts)
        self.assertIn((1054, 666, "D1", "", 6), spots)
        self.assertNotIn(8, [i for *_, i in spots])            # 出力の「D1」は見出しの列に無い
        self.assertEqual(len(spots), 6)
        indexes = qs.pad_indexes([(x, y, f"{note} - {name or '?'}") for x, y, note, name, _ in spots])
        self.assertEqual(dict(zip([s[2] for s in spots], indexes))["D1"], 3)

    def test_import_alert(self):
        alert = _el(AXChildren=[
            _el(AXRole="AXStaticText", AXValue="追加するオーディオファイルにはテンポ情報が含まれています。"),
            _el(AXRole="AXStaticText", AXValue="テンポ情報をこのプロジェクトに読み込みますか?"),
            _el(AXRole="AXButton", AXTitle="読み込む"),
            _el(AXRole="AXButton", AXTitle="読み込まない"),
        ])
        app = _el(AXWindows=[_el(AXChildren=[]), alert])
        kind, button = qs.import_alert(app)
        self.assertEqual((kind, button["AXTitle"]), ("tempo", "読み込まない"))
        alert["AXChildren"][0]["AXValue"] = "The added audio file contains markers."
        alert["AXChildren"][3]["AXTitle"] = "Don’t Import"
        self.assertEqual(qs.import_alert(app)[0], "markers")
        self.assertIsNone(qs.import_alert(_el(AXWindows=[_el(AXChildren=[])])))
        self.assertEqual(qs.import_alert_remark(["tempo", "markers"]),
                         "Logic がファイルのテンポ情報とマーカーを読み込むか尋ねたので、「読み込まない」と答えました。"
                         "プロジェクトは変えていません")
        self.assertEqual(qs.import_alert_remark([]), "")

    def test_dismiss_import_alerts_answers_each_in_turn(self):
        # テンポ情報に答えると、続けてマーカーを尋ねられる（2026-11-02 実測）。両方に「読み込まない」で答える
        def alert(text):
            return _el(AXChildren=[_el(AXRole="AXStaticText", AXValue=text),
                                   _el(AXRole="AXButton", AXTitle="読み込む"),
                                   _el(AXRole="AXButton", AXTitle="読み込まない")])
        queue = [alert("追加するオーディオファイルにはテンポ情報が含まれています。"),
                 alert("追加するオーディオファイルにはマーカーが含まれています。")]
        main = _el(AXChildren=[])
        app = _el(AXWindows=[queue[0], main])
        pressed = []

        def press(button, timeout=0.5):
            pressed.append(button["AXTitle"])
            queue.pop(0)
            app["AXWindows"] = queue[:1] + [main]
            return 0

        saved = qs.press_async, qs.same_element
        qs.press_async, qs.same_element = press, (lambda a, b: a is b)
        try:
            self.assertEqual(qs.dismiss_import_alerts(app, lambda m: None), ["tempo", "markers"])
        finally:
            qs.press_async, qs.same_element = saved
        self.assertEqual(pressed, ["読み込まない", "読み込まない"])
        self.assertIsNone(qs.import_alert(app))


class TrackChoiceTest(unittest.TestCase):
    """貼り付け先のトラックと、貼る位置に何があるか（2026-09-25）。"""

    HELP_3_7 = "Region starts at 3 bars  and ends at 7 bars , Audio region. An editable"

    def test_paste_position_follows_logic(self):
        m, d = (4, 4), 16
        self.assertEqual(qs.paste_position((17, 4, 1, 1), m, d, "off"), (17, 1, 1, 1))   # Logic の決まり
        self.assertEqual(qs.paste_position((17, 4, 1, 1), m, d, "bar"), (18, 1, 1, 1))
        self.assertIsNone(qs.paste_position(None, m, d))

    def test_advance_goal(self):
        self.assertEqual(qs.advance_goal((7, 1, 1, 1)), (7, 1, 1, 1))
        self.assertEqual(qs.advance_goal((5, 1, 2, 30)), (6, 1, 1, 1))     # 途中で終わるなら次の小節の頭
        self.assertIsNone(qs.advance_goal(None))

    def test_playhead_goal(self):
        # 貼ったあとの再生位置: 終わり（次に貼る位置）・頭（すぐ聴ける）・動かさない（2026-09-26 の要望）
        self.assertEqual(qs.PLAYHEAD_MODES, ("end", "start", "stay"))
        start, end = (5, 1, 1, 1), (7, 3, 1, 1)
        self.assertEqual(qs.playhead_goal("end", start, end), (8, 1, 1, 1))
        self.assertEqual(qs.playhead_goal("start", start, end), start)
        self.assertIsNone(qs.playhead_goal("stay", start, end))
        self.assertIsNone(qs.playhead_goal("start", None, end))           # 分からなければ動かさない
        self.assertIsNone(qs.playhead_goal("end", start, None))

    def test_occupancy(self):
        m, d = (4, 4), 16
        regions = [("Loop_A", self.HELP_3_7)]
        self.assertEqual(qs.occupancy_phrase(regions, (5, 1, 1, 1), m, d),
                         "5 小節目には Loop_A があります（3 小節目の頭から 7 小節目の頭まで）")
        self.assertEqual(qs.occupancy_phrase(regions, (7, 1, 1, 1), m, d), "7 小節目は空いています")   # 終わりの位置は空き
        self.assertEqual(qs.occupancy_phrase(regions, (1, 1, 1, 1), m, d), "1 小節目は空いています")
        self.assertEqual(qs.occupancy_phrase([], (1, 1, 1, 1), None, d), "")

    def test_track_phrase(self):
        st = qs.ProjectState(0, meter=(4, 4), division=16, track_index=2, track_name="Audio 1", can_import=True,
                             checked=True, channel_mode="Mono", regions=[("Loop_A", self.HELP_3_7)],
                             paste_at=(3, 1, 1, 1))
        # トラックの名前を最初に言う（キーで移ったとき、どこに移ったかが先に分かるように。2026-09-26 の指摘）
        self.assertEqual(qs.track_phrase(st), "Audio 1、トラック 2、オーディオ、モノラル。"
                                             "3 小節目には Loop_A があります（3 小節目の頭から 7 小節目の頭まで）")
        inst = qs.ProjectState(0, track_index=1, track_name="Classic Electric Piano", can_import=False, checked=True)
        self.assertEqual(qs.track_phrase(inst), "Classic Electric Piano、トラック 1。オーディオトラックではないので、貼れません")
        hidden = qs.ProjectState(0, track_index=2, track_name="Audio 1", can_import=True, checked=False)
        self.assertEqual(qs.track_phrase(hidden), "Audio 1、トラック 2。インスペクタが出ていないので、"
                                                 "オーディオトラックかどうか確かめられません")
        # 2 つ以上選ばれていたら、そう言う（貼るのはインスペクタに出ている、いまのトラック）
        many = qs.ProjectState(0, track_index=3, track_name="Audio 2", can_import=True, checked=True, selected_count=2)
        self.assertEqual(qs.track_phrase(many), "Audio 2、トラック 3、オーディオ。ほかにも選ばれているトラックがあります")
        self.assertEqual(qs.track_phrase(qs.ProjectState(0)), "トラックが選ばれていません")


class CurrentTrackTest(unittest.TestCase):
    """選ばれているトラックが 2 つ以上あるとき、Logic が貼るのはインスペクタに出ているトラック（2026-09-26 実測）。"""

    ROWS = [(1, "Classic Electric Piano", True, "r1"), (2, "Audio 1", False, "r2"), (3, "Audio 2", True, "r3")]

    def _run(self, strip_name):
        saved = (qs.inspector_strip, qs.attr)
        qs.inspector_strip = lambda app, side: "strip" if strip_name is not None else None
        qs.attr = lambda el, name: strip_name if (el == "strip" and name == "AXDescription") else None
        try:
            return qs.current_track(qs.Target(qs.EXIT_OK, 1, "app", "win"), self.ROWS)
        finally:
            qs.inspector_strip, qs.attr = saved

    def test_inspector_track_wins(self):
        row, count = self._run("Audio 2")
        self.assertEqual((row[0], row[1], count), (3, "Audio 2", 2))       # 見出しの最初（1）ではない

    def test_without_inspector_first_selected(self):
        row, count = self._run(None)
        self.assertEqual((row[0], count), (1, 2))

    def test_nothing_selected(self):
        saved = self.ROWS
        self.ROWS = [(1, "A", False, "r1")]
        try:
            self.assertEqual(self._run("A"), (None, 0))
        finally:
            self.ROWS = saved

class StepWhenLogicLagsTest(unittest.TestCase):
    """Logic が遅れて、インスペクタが前のトラックのままのとき（2026-09-26 の朝、5 分ほど続いた）。

    **インスペクタに残った前のトラックの種類で決めない。** 前が音源のトラックだと、次のオーディオトラックを
    「オーディオではない」と飛ばしてしまい、前がオーディオだと、音源のトラックで止まってしまう。
    """

    def setUp(self):
        self.rows = [(1, "Classic Electric Piano", True, "r1"), (2, "Audio 1", False, "r2"),
                     (3, "Audio 2", False, "r3")]
        self.strip = "Classic Electric Piano"
        self.kinds = {"Classic Electric Piano": "other", "Audio 1": "audio", "Audio 2": "audio"}
        self.lagging, self.stuck, self.calls = set(), set(), []
        names = ("find_main", "track_rows", "inspector_strip", "strip_kind", "select_track", "_read_project")
        self.saved = {n: getattr(qs, n) for n in names}
        qs.find_main = lambda: qs.Target(qs.EXIT_OK, 1, "app", "win")
        qs.track_rows = lambda win: list(self.rows)
        qs.inspector_strip = lambda app, side: "strip"
        qs.strip_kind = lambda app, name=None: (None if name is not None and name != self.strip
                                                else self.kinds[self.strip])

        def select_track(t, index, log=lambda m: None):
            self.calls.append(index)
            if index not in [i for i, _, _, _ in self.rows]:
                return qs.SELECT_MISSING
            if index in self.stuck:
                return qs.SELECT_STUCK
            self.rows = [(i, n, i == index, e) for i, n, _, e in self.rows]
            if index in self.lagging:
                return qs.SELECT_LAGGING               # 選べたが、インスペクタは前のトラックのまま
            self.strip = next(n for i, n, _, _ in self.rows if i == index)
            return qs.SELECT_OK

        def read_project(t, snap="off"):
            chosen, count = qs.current_track(t, self.rows, self.strip)
            kind = qs.strip_kind(t.app, chosen[1]) if chosen else None
            return qs.ProjectState(qs.EXIT_OK, track_index=chosen[0] if chosen else None,
                                   track_name=chosen[1] if chosen else "", can_import=kind == "audio",
                                   checked=kind is not None, selected_count=count, strip_name=self.strip)

        qs.select_track, qs._read_project = select_track, read_project

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(qs, name, value)

    def test_normal_step_skips_instrument_tracks(self):
        self.rows = [(1, "Classic Electric Piano", False, "r1"), (2, "Audio 1", False, "r2"), (3, "Audio 2", True, "r3")]
        self.strip = "Audio 2"
        st = qs.step_audio_track(1)
        self.assertEqual(self.calls, [1, 2])                     # 1 は音源なので飛ばす
        self.assertTrue(st.message.startswith("Audio 1、トラック 2、オーディオ"), st.message)

    def test_lagging_inspector_stops_there_and_says_so(self):
        self.lagging = {2}
        st = qs.step_audio_track(1)
        self.assertEqual(self.calls, [2])                        # 前の版は音源のチャンネルを見て 2 を飛ばし、3 へ進んだ
        self.assertEqual(st.code, qs.EXIT_OK)
        self.assertTrue(st.message.startswith("Audio 1、トラック 2。Logic のインスペクタがまだ "
                                              "Classic Electric Piano のままなので"), st.message)
        self.assertIn("コマンド+T で確かめ直せます", st.message)
        self.assertFalse(st.checked)                             # 貼れるとは言わない

    def test_logic_that_does_not_answer_is_reported_with_the_name(self):
        self.stuck = {2}
        st = qs.step_audio_track(1)
        self.assertEqual(self.calls, [2])                        # 先へ進めても同じなので、そこで止める
        self.assertEqual(st.code, qs.EXIT_LOGIC_SLOW)
        self.assertTrue(st.message.startswith("Audio 1、トラック 2 を選ぼうとしましたが、Logic が応えません"), st.message)

    def test_choose_track_lagging_and_stuck(self):
        self.lagging = {3}
        st = qs.choose_track(3)
        self.assertTrue(st.message.startswith("Audio 2、トラック 3。Logic のインスペクタがまだ"), st.message)
        self.stuck = {2}
        self.assertEqual(qs.choose_track(2).code, qs.EXIT_LOGIC_SLOW)
        self.assertEqual(qs.choose_track(9).code, qs.EXIT_NO_TRACK)

    def test_select_track_releases_modifiers_before_writing(self):
        """Logic がコマンドを押したままと思い込んでいると、選ぶ書き込みが切り替えになる（2026-10-27）。"""
        qs.select_track = self.saved["select_track"]
        order = []

        class FakeAX:
            def AXUIElementSetAttributeValue(self, el, name, value):
                order.append(("write", name, value))

        names = ("_ax", "_find_by", "main_part", "release_modifiers", "wait_for")
        saved = {n: getattr(qs, n) for n in names}
        try:
            qs._ax = lambda: FakeAX()
            qs._find_by = lambda el, pred, depth=8: "header"
            qs.main_part = lambda win, desc: "tracks"
            qs.release_modifiers = lambda pid: order.append(("release", pid))
            qs.track_rows = lambda win: [(1, "Audio 1", True, "r1"), (2, "Audio 2", False, "r2")]
            qs.strip_kind = lambda app, name=None: "audio"
            # 書き込みのあと見出しが選ばれていなくても（ここでは選ばれないまま）、書く前に離したと伝えていること
            qs.wait_for = lambda pred, timeout, interval=0.05: True
            self.assertEqual(qs.select_track(qs.Target(qs.EXIT_OK, 42, "app", "win"), 2), qs.SELECT_OK)
        finally:
            for name, value in saved.items():
                setattr(qs, name, value)
        self.assertEqual(order, [("release", 42), ("write", "AXSelectedChildren", ["r2"])])

    def test_hidden_inspector_is_worded_differently(self):
        hidden = qs.ProjectState(0, track_index=2, track_name="Audio 1", can_import=True, checked=False)
        self.assertIn("インスペクタが出ていないので", qs.track_phrase(hidden))
        behind = qs.ProjectState(0, track_index=2, track_name="Audio 1", checked=False, strip_name="Audio 2")
        self.assertIn("インスペクタがまだ Audio 2 のままなので", qs.project_phrase(behind))
        self.assertNotIn("出ていない", qs.project_phrase(behind))


class PadLoadMessagesTest(unittest.TestCase):
    """DMD のパッドへの読み込みの言い方（2026-10-24 のレビュー）。"""

    def test_lost_pad_window_is_not_the_quick_sampler_advice(self):
        # パッドの画面は補助が開いたもの。「ハイフンで開いてから」（Quick Sampler 向けの案内）は読まない
        code, message = qs.pad_load_failure(qs.EXIT_NO_WINDOW, qs.MESSAGES[qs.EXIT_NO_WINDOW])
        self.assertEqual(code, qs.EXIT_PAD_WINDOW)
        self.assertNotIn("ハイフン", message)
        self.assertEqual(qs.pad_load_failure(qs.EXIT_OPEN_FAILED, "x"), (qs.EXIT_OPEN_FAILED, "x"))

    def test_flex_warning_is_kept_with_the_rename_warning(self):
        flex = qs.MESSAGES[qs.EXIT_NO_FLEX]
        self.assertEqual(qs.pad_notes(flex, True), flex)
        self.assertEqual(qs.pad_notes(flex, False), f"{flex}。パッドの名前は変えられませんでした")
        self.assertEqual(qs.pad_notes("", False), "パッドの名前は変えられませんでした")
        self.assertEqual(qs.pad_notes("", True), "")

    def test_pad_zero_is_refused_not_loaded_into_quick_sampler(self):
        import contextlib
        import io
        import tempfile

        # **Logic には絶対に触らせない**（直す前は Quick Sampler に読み込みにいった。画面が開いていれば本当に読み込む）
        touched = []
        saved = qs.load_file, qs.find_dmd_target
        qs.load_file = lambda *a, **k: touched.append("load_file") or qs.LoadResult(qs.EXIT_NO_WINDOW)
        qs.find_dmd_target = lambda: touched.append("dmd") or qs.Target(qs.EXIT_NO_WINDOW)
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = qs.main(["--load", f.name, "--pad", "0", "--quiet"])
        finally:
            qs.load_file, qs.find_dmd_target = saved
        self.assertEqual(code, qs.EXIT_NO_PAD)               # 番号の誤りとして断る
        self.assertEqual(touched, [])
        self.assertEqual(out.getvalue().strip(), qs.MESSAGES[qs.EXIT_NO_PAD])


if __name__ == "__main__":
    unittest.main()
