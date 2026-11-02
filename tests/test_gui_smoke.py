"""画面の組み立ての確認（窓は出さない。Logic にも Splice にも触らない）。"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LOGICLOADER_SILENT", "1")

try:
    import wx
except ImportError:  # wx の無い環境では飛ばす
    wx = None

from tests.test_controller import FakeEngine, FakeLibrary  # noqa: E402
from ui.controller import Outcome as C_Outcome  # noqa: E402


@unittest.skipIf(wx is None, "wxPython が無い")
class GuiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = wx.App(False)

    def setUp(self):
        from ui.controller import AppController
        from ui.main_frame import MainFrame

        tmp = Path(tempfile.mkdtemp())
        self.controller = AppController(library_factory=lambda: FakeLibrary([]), engine=lambda: FakeEngine(),
                                        trusted=lambda: True, settings_path=tmp / "s.json", stage_root=tmp / "st")
        self.frame = MainFrame(self.controller)

    def tearDown(self):
        self.frame.Destroy()

    def _menu_texts(self) -> list[str]:
        bar = self.frame.GetMenuBar()
        texts = []
        for i in range(bar.GetMenuCount()):
            for item in bar.GetMenu(i).GetMenuItems():
                texts.append(item.GetItemLabel())
        return texts

    def test_menus(self):
        bar = self.frame.GetMenuBar()
        self.assertEqual([bar.GetMenuLabel(i) for i in range(bar.GetMenuCount())], ["操作", "ヘルプ"])

    def test_every_shortcut_in_help_exists_in_menu(self):
        """キー操作の一覧に書いたコマンドのキーは、どれもメニューにある（書いただけで効かない、を防ぐ）。

        一覧はカタカナで書く（「コマンド+シフト+K」「コマンド+右ブラケット（]）」）。記号のキーを
        記号だけで書くと VoiceOver が読まない（2026-09-25 の指摘）。
        """
        from ui.main_frame import KEY_HELP

        accels = {t.split("\t", 1)[1] for t in self._menu_texts() if "\t" in t}
        names = {"右ブラケット": "]", "左ブラケット": "["}
        # 「オプション+コマンド+A」は Logic のキー（新しいオーディオトラック）なので数えない
        found = re.findall(r"(?<!オプション\+)コマンド\+(シフト\+)?([A-Z0-9]|右ブラケット|左ブラケット)", KEY_HELP)
        self.assertGreater(len(found), 10)
        for shift, key in found:
            accel = "Ctrl+" + ("Shift+" if shift else "") + names.get(key, key)
            with self.subTest(key=accel):
                self.assertIn(accel, accels)

    def test_help_does_not_leave_bare_symbol_keys(self):
        """記号のキーは名前を添える（「コマンド+]」のような書き方を残さない）。"""
        from ui.main_frame import KEY_HELP

        self.assertIsNone(re.search(r"(コマンド|Cmd)\+[\[\]]", KEY_HELP))
        self.assertNotIn("Cmd+", KEY_HELP)

    def test_family_keys(self):
        """3 つのアプリと揃えたキー（main_frame.py の表）。"""
        accels = {t.split("\t", 1)[1] for t in self._menu_texts() if "\t" in t}
        for key in ("Ctrl+S", "Ctrl+R", "Ctrl+T", "Ctrl+C", "Ctrl+I", "Ctrl+Shift+G", "Ctrl+Shift+K", "Ctrl+Q"):
            with self.subTest(key=key):
                self.assertIn(key, accels)
        self.assertNotIn("Ctrl+L", accels)  # macOS と競合する

    def test_target_and_pad_controls(self):
        self.assertEqual(self.frame._target.GetCount(), 3)
        self.assertEqual(self.frame._pad.GetCount(), 48)
        self.assertFalse(self.frame._pad.IsEnabled())         # 既定は Quick Sampler の画面
        self.assertFalse(self.frame._fit_key.IsEnabled())     # キーと移調はオーディオトラックのときだけ
        self.assertFalse(self.frame._transpose.IsEnabled())
        accels = {t.split("\t", 1)[1] for t in self._menu_texts() if "\t" in t}
        self.assertIn("Ctrl+]", accels)
        self.assertIn("Ctrl+[", accels)

    def test_track_target_enables_its_controls(self):
        self.controller.set_target("track")
        self.frame._refresh_pad_choice()
        self.assertTrue(self.frame._fit_key.IsEnabled())
        self.assertTrue(self.frame._transpose.IsEnabled())
        self.assertFalse(self.frame._flex_btn.IsEnabled())      # トラックに貼るときは Flex を使わない
        self.assertFalse(self.frame._pad.IsEnabled())
        self.assertEqual(self.frame._load_btn.GetName(), "オーディオトラックに貼る")
        self.assertEqual(self.frame._fit_key.GetCount(), 26)
        self.assertEqual(self.frame._transpose.GetStringSelection(), "移調しない")
        self.assertTrue(self.frame._track.IsEnabled())             # 貼り付け先のトラックの欄
        labels = {t.split("\t", 1)[0] for t in self._menu_texts() if "\t" in t}
        self.assertIn("次のトラックへ", labels)                     # キーを押すと VoiceOver が読む項目名
        self.assertIn("前のトラックへ", labels)
        self.assertNotIn("次のパッドへ", labels)
        self.controller.set_target("dmd")
        self.frame._refresh_pad_choice()
        labels = {t.split("\t", 1)[0] for t in self._menu_texts() if "\t" in t}
        self.assertIn("次のパッドへ", labels)
        self.assertFalse(self.frame._track.IsEnabled())

    def test_tempo_choice_and_key(self):
        """「テンポ」の欄（ハーフ・Even・ダブル）。既定は Even。コマンド+シフト+T で順送りし、欄も動く。"""
        self.controller.set_target("track")
        self.frame._refresh_pad_choice()
        self.assertTrue(self.frame._tempo.IsEnabled())
        self.assertEqual([self.frame._tempo.GetString(i).split("（")[0] for i in range(self.frame._tempo.GetCount())],
                         ["ハーフ", "Even", "ダブル"])
        self.assertTrue(self.frame._tempo.GetStringSelection().startswith("Even"))
        self.frame._on_tempo_key(None)
        self.assertEqual(self.controller.tempo_scale, 2.0)
        self.assertTrue(self.frame._tempo.GetStringSelection().startswith("ダブル"))
        self.frame._tempo.SetSelection(0)
        self.frame._on_tempo_change(None)
        self.assertEqual(self.controller.tempo_scale, 0.5)
        self.controller.set_target("qs")
        self.frame._refresh_pad_choice()
        self.assertFalse(self.frame._tempo.IsEnabled())

    def test_space_and_shift_space_in_the_list(self):
        """一覧でスペースは元の音、シフト+スペースは合わせた音（2026-10-13）。読み込み先がオーディオトラックでも。"""
        self.controller.set_target("track")
        calls = []
        self.frame._on_preview = lambda e: calls.append("元の音")
        self.frame._on_preview_fit = lambda e: calls.append("合わせた音")
        self.frame._on_load = lambda e: calls.append("貼る")
        import ui.main_frame as M
        saved = M.wx.Window.FindFocus
        M.wx.Window.FindFocus = staticmethod(lambda: self.frame._results)
        try:
            for shift, key in ((False, wx.WXK_SPACE), (True, wx.WXK_SPACE), (False, wx.WXK_RETURN)):
                evt = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
                evt.SetKeyCode(key)
                evt.SetShiftDown(shift)
                self.frame._on_char_hook(evt)
        finally:
            M.wx.Window.FindFocus = saved
        self.assertEqual(calls, ["元の音", "合わせた音", "貼る"])
        labels = [t.split("\t", 1)[0] for t in self._menu_texts()]
        self.assertIn("試聴 元の音 再生/停止", labels)
        self.assertIn("試聴 プロジェクトに合わせた音 再生/停止", labels)

    def _pump_until(self, predicate, seconds=3.0):
        import time
        end = time.time() + seconds
        while time.time() < end and not predicate():
            wx.GetApp().Yield(True)
            time.sleep(0.01)
        return predicate()

    def test_choosing_the_same_row_still_selects_in_logic(self):
        # アプリが覚えている選択は、Logic で選び直すと古くなる。同じ行でも選び直す（2026-09-26 のレビュー）
        engine = FakeEngine()
        self.controller._engine = lambda: engine       # 同じ偽の Logic を使い続ける（呼ぶたびに作らない）
        self.controller.set_target("track")
        self.frame._refresh_pad_choice()
        self.controller.refresh_project()
        self.frame._refresh_track_choice()
        row = self.controller.selected_track_row()
        self.frame._track.SetSelection(row)
        self.frame._on_track_change(None)
        self.assertTrue(self._pump_until(lambda: not self.frame._job_running))
        self.assertIn("choose", [c[0] for c in engine.calls])

    def test_worker_exceptions_do_not_leave_the_app_busy(self):
        from ui.main_frame import _guarded

        def broken():
            raise OSError(28, "No space left on device")

        outcome = _guarded(broken)
        self.assertFalse(outcome)
        self.assertIn("思わぬ理由で止まりました", outcome.message_ja)
        self.frame._start_quick_job(broken)
        self.assertTrue(self._pump_until(lambda: not self.frame._job_running))   # 「操作中」のまま残らない

    def test_names_are_short(self):
        """タブで通るたびに読まれる名前は短く（2026-10-13。長い名前とヒントの二度読みで 1 か所 10 秒を超えた）。"""
        self.assertEqual(self.frame._settings_btn.GetName(), "読み込みの設定")
        self.controller.set_target("track")
        self.frame._refresh_pad_choice()
        for ctrl in (self.frame._results, self.frame._target, self.frame._track, self.frame._tempo,
                     self.frame._fit_key, self.frame._transpose, self.frame._load_btn, self.frame._settings_btn,
                     self.frame._target_btn, self.frame._flex_btn, self.frame._file_btn):
            with self.subTest(name=ctrl.GetName()):
                self.assertLessEqual(len(ctrl.GetName()), 20)
                self.assertFalse(ctrl.GetToolTipText())          # ヒントで同じ文を二度読ませない
        self.frame._set_target_name(C_Outcome(False, "[エラー] Drum Machine Designer の画面が開いていません。"
                                                     "DMD のトラックでハイフンを押して開いてから、もう一度読み込んでください"))
        self.assertLessEqual(len(self.frame._target_btn.GetName()), 40)

    def test_target_button_says_why_it_could_not_check(self):
        """調べられなかったときは理由を添え、前に読んだ「いまは …」を残さない（2026-10-13 のレビュー）。"""
        engine = FakeEngine()
        self.controller._engine = lambda: engine
        self.controller.set_target("track")
        self.controller.refresh_project()
        self.frame._set_target_name(C_Outcome(True, ""))
        self.assertEqual(self.frame._target_btn.GetName(), "読み込み先を調べる。いまは Audio 1")
        self.frame._set_target_name(C_Outcome(False, "[エラー] Logic Pro が起動していません。Logic でプロジェクトを開いてから"))
        self.assertEqual(self.frame._target_btn.GetName(), "読み込み先を調べる。Logic Pro が起動していません")
        # 断られただけ（Logic を操作中）なら、貼れないとは言わない
        self.frame._set_target_name(C_Outcome(False, "いま Logic を操作しています。終わるまでお待ちください。"))
        self.assertEqual(self.frame._target_btn.GetName(), "読み込み先を調べる。いまは Audio 1")
        engine.can_import = False                            # 選んでいるのがオーディオトラックでない
        self.controller.refresh_project()
        self.frame._set_target_name(C_Outcome(False, "Audio 1、トラック 2。オーディオトラックではないので、貼れません。"))
        self.assertEqual(self.frame._target_btn.GetName(), "読み込み先を調べる。いまは Audio 1（貼れません）")
        self.controller.set_target("qs")
        self.frame._set_target_name(C_Outcome(False, "アクセシビリティの許可がまだありません"))   # 起動したとき
        self.assertEqual(self.frame._target_btn.GetName(), "読み込み先を調べる。アクセシビリティの許可がまだありません")
        self.controller.set_target("dmd")
        self.frame._set_target_name(C_Outcome(False, "[エラー] Drum Machine Designer の画面が開いていません。"))
        self.assertNotIn("いまは パッド", self.frame._target_btn.GetName())

    def test_auto_preview_cancels_a_fitted_sound_being_made(self):
        """シフト+スペースで作っている途中に、自動で試聴が別の素材の元の音を鳴らしたら、できあがった合わせた音は鳴らさない。"""
        from core.settings import LoadSettings
        from tests.test_controller import Entry, FakePlayer
        player = FakePlayer()
        self.controller._player_factory = lambda: player
        self.controller.update_settings(LoadSettings(auto_preview=True))
        self.controller.results = [Entry("/x/a.wav", "a.wav"), Entry("/x/b.wav", "b.wav")]
        self.frame._results.Set(["a", "b"])
        self.frame._preview_token = 7                       # A の合わせた音を作っている途中（token 7）
        self.frame._results.SetSelection(1)
        self.frame._on_select(None)                          # B を選び直す → 自動で B の元の音
        self.assertEqual(player.current_path, "/x/b.wav")
        self.assertNotEqual(self.frame._preview_token, 7)    # A の結果（token 7）は捨てられる
        self.frame._finish_fitted_preview(7, "/x/a__120bpm_even.wav", "/x/a.wav", "")
        self.assertEqual(player.current_path, "/x/b.wav")    # B の元の音のまま

    def test_window_fits_in_the_work_area(self):
        """窓は作業領域の中に置く（下が画面の外に出ていた。2026-10-13 のレビュー）。"""
        area = wx.Display(max(wx.Display.GetFromWindow(self.frame), 0)).GetClientArea()
        rect = self.frame.GetRect()
        self.assertGreaterEqual(rect.GetTop(), area.GetTop())
        self.assertLessEqual(rect.GetBottom(), area.GetBottom())

    def test_key_help_sections(self):
        """キー操作の一覧: パックの欄の説明は「探す」に、「ボタン」の節は検索を実行がタブで止まることと食い違わない。"""
        from ui.main_frame import KEY_HELP
        sections = {block.split("\n", 1)[0]: block for block in KEY_HELP.split("\n\n")}
        self.assertIn("パックの欄は", sections["探す"])
        self.assertNotIn("パックの欄は", sections["タブで止まる場所"])
        self.assertIn("検索を実行", sections["ボタン"])

    def test_tab_stops_only_at_fields(self):
        """キーで代わりがあるボタンには、タブで止まらない（2026-10-13 の要望。一周 16 か所 → 10 か所）。"""
        for btn in (self.frame._load_btn, self.frame._settings_btn, self.frame._target_btn,
                    self.frame._flex_btn, self.frame._file_btn):
            with self.subTest(name=btn.GetName()):
                self.assertFalse(btn.AcceptsFocusFromKeyboard())
        self.assertTrue(self.frame._text.AcceptsFocusFromKeyboard())
        # 検索を実行は止まる（種別やパックを選んだあと、そのまま実行する。2026-10-13 の指摘）
        self.assertTrue(self.frame._search_btn.AcceptsFocusFromKeyboard())
        self.assertTrue(self.frame._results.AcceptsFocusFromKeyboard())
        texts = dict(reversed(t.split("\t", 1)) for t in self._menu_texts() if "\t" in t)
        self.assertEqual(texts["Ctrl+F"], "検索語の欄へ移動")
        self.assertEqual(texts["Ctrl+O"], "ファイルを選んで読み込む")      # ボタンの代わりのキー

    def test_command_f_goes_back_to_the_search_field(self):
        self.frame._text.SetValue("kick")
        self.frame._results.SetFocus()
        self.frame._on_focus_search(None)
        self.assertEqual(self.frame._text.GetStringSelection(), "kick")   # そのまま打てば置き換わる

    def test_key_match_and_target_cycle_keys(self):
        """コマンド+K でキーを合わせる・合わせないを切り替え、コマンド+シフト+L で読み込み先を順送りする。"""
        texts = dict(reversed(t.split("\t", 1)) for t in self._menu_texts() if "\t" in t)
        self.assertEqual(texts["Ctrl+K"], "キーをプロジェクトに合わせるかを切り替える")
        self.assertEqual(texts["Ctrl+Shift+L"], "読み込み先を順送り")
        self.frame._on_key_match_key(None)
        self.assertEqual(self.controller.key_choice, "off")
        self.assertEqual(self.frame._fit_key.GetSelection(), 1)
        self.frame._on_key_match_key(None)
        self.assertEqual(self.controller.key_choice, "project")
        self.frame._fit_key.SetSelection(5)                  # 24 のキーのどれかを選んでいても、プロジェクトに戻る
        self.frame._on_fit_key_change(None)
        self.frame._on_key_match_key(None)
        self.assertEqual(self.controller.key_choice, "project")
        start = self.controller.settings.target
        seen = []
        for _ in range(3):
            self.frame._on_target_cycle(None)
            self.assertTrue(self._pump_until(lambda: not self.frame._job_running))
            seen.append(self.controller.settings.target)
        self.assertEqual(sorted(seen), ["dmd", "qs", "track"])
        self.assertEqual(seen[-1], start)

    def test_target_keys_switch_the_target(self):
        """コマンド+1・2・3 で読み込み先を切り替える（2026-09-26 の要望）。項目名だけで何になるか分かること
        （VoiceOver はキーを押すと項目名を読む）。オーディオトラックにしたら、貼り付け先まで言う。"""
        texts = dict(reversed(t.split("\t", 1)) for t in self._menu_texts() if "\t" in t)
        self.assertEqual(texts["Ctrl+1"], "読み込み先を Quick Sampler にする")
        self.assertEqual(texts["Ctrl+2"], "読み込み先を DMD のパッドにする")
        self.assertEqual(texts["Ctrl+3"], "読み込み先をオーディオトラックにする")
        self.frame._on_target_key("track")
        self.assertTrue(self._pump_until(lambda: not self.frame._job_running))
        self.assertTrue(self.controller.track_mode)
        self.assertEqual(self.frame._target.GetSelection(), self.frame._target_values.index("track"))
        self.assertTrue(self.frame._fit_key.IsEnabled())
        self.assertTrue(self.frame._status.GetLabel().startswith("読み込み先をオーディオトラックにしました。貼り付け先は"),
                        self.frame._status.GetLabel())
        self.frame._on_target_key("qs")
        self.assertFalse(self.controller.track_mode)
        self.assertEqual(self.frame._status.GetLabel(), "読み込み先を Quick Sampler の画面にしました。")
        self.assertEqual(self.controller.settings.target, "qs")     # 設定に残る（次に開いたときも同じ）

    def test_menu_items_for_the_distribution(self):
        """Splice のフォルダはどの版でも選べる。Keyboard Maestro の補助を入れる項目は配布版だけ
        （手元の版は tools/install_qs_helper.py で入れる別のグループがあり、両方を入れるとキーが二重になる）。"""
        from ui.main_frame import KEY_HELP, MainFrame
        self.assertIn("Splice のフォルダを選ぶ…", self._menu_texts())
        self.assertNotIn("Keyboard Maestro の補助を入れる…", self._menu_texts())
        dist = MainFrame(self.controller, distribution=True)
        try:
            bar = dist.GetMenuBar()
            labels = [item.GetItemLabel() for i in range(bar.GetMenuCount()) for item in bar.GetMenu(i).GetMenuItems()]
        finally:
            dist.Destroy()
        self.assertIn("Keyboard Maestro の補助を入れる…", labels)
        for word in ("Splice のフォルダを選ぶ", "Keyboard Maestro の補助を入れる"):
            self.assertIn(word, KEY_HELP)

    def test_key_help_names_every_setting(self):
        """キー操作の一覧のコマンド+S の説明に、設定の項目が全部ある（「自動で試聴」が抜けていた。2026-10-24 のレビュー）。"""
        from ui.main_frame import KEY_HELP
        section = next(block for block in KEY_HELP.split("\n\n") if block.startswith("コマンドキー"))
        for word in ("Original", "Flex", "Follow Tempo", "読み込んだあと", "貼る位置", "貼ったあとの再生位置",
                     "自動で試聴", "次のパッドへ進む", "テンポを合わせる"):
            self.assertIn(word, section)

    def test_choosing_a_target_in_the_field_waits_for_voiceover(self):
        """「読み込み先」の欄で選んだ結果は、VoiceOver が欄の新しい値を読み終えてから読む（800 ミリ秒あける。
        ほかの欄と同じ。それまではすぐ読ませて、欄の値の読み上げを遮っていた。2026-10-24 のレビュー）。"""
        import ui.main_frame as M

        reports, detects = [], []
        self.frame._report = lambda message, **kw: reports.append(kw.get("delay_ms", 0))
        self.frame._start_detect = lambda announce_it, **kw: detects.append(kw)
        self.frame._target.SetSelection(self.frame._target_values.index("qs"))
        self.frame._on_target_change(None)
        self.assertEqual(len(reports), 1)
        self.assertGreater(reports[0], M.PAD_STEP_DELAY_MS - 200)
        self.frame._target.SetSelection(self.frame._target_values.index("track"))
        self.frame._on_target_change(None)
        self.assertIsNotNone(detects[-1].get("pressed_at"))
        self.assertTrue(detects[-1].get("brief"))

    def test_launch_puts_focus_in_the_first_field(self):
        """起動したらすぐタブで動けること。どの欄にもフォーカスが無ければ、検索語の欄に置く（2026-09-26 の指摘）。"""
        import ui.main_frame as M

        calls = []
        self.frame._text.SetFocus = lambda: calls.append("text")   # 窓は出さないので、呼ばれたかで見る
        saved = M._focused_window
        try:
            M._focused_window = lambda: None
            self.frame._focus_first()
            self.assertEqual(calls, ["text"])
            M._focused_window = lambda: self.frame._results           # 欄にあれば動かさない
            self.frame._focus_first()
            self.assertEqual(calls, ["text"])
        finally:
            M._focused_window = saved

    def test_settings_dialog_has_the_playhead_choice(self):
        from dataclasses import replace
        from ui.settings_dialog import FIELDS, SettingsDialog

        self.assertIn(("playhead", "オーディオトラックに貼ったあとの再生位置"), FIELDS)
        dialog = SettingsDialog(self.frame, replace(self.controller.settings, playhead="start"))
        try:
            choice = dialog._choices["playhead"]
            self.assertEqual(choice.GetStringSelection(), "貼ったオーディオの頭に置く（すぐ再生して聴ける）")
            choice.SetSelection(2)
            self.assertEqual(dialog.result().playhead, "stay")
            self.assertFalse(hasattr(dialog, "_advance"))             # 入り切りのチェックボックスは無くした
        finally:
            dialog.Destroy()

    def test_settings_dialog_builds_and_returns_current_values(self):
        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.frame, self.controller.settings)
        try:
            self.assertEqual(dialog.result(), self.controller.settings)
        finally:
            dialog.Destroy()


if __name__ == "__main__":
    unittest.main()
