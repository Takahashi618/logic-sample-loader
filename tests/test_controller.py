"""ui/controller.py の単体テスト。**Logic・Splice・音には触らない**（全部スタブ）。"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("LOGICLOADER_SILENT", "1")

from core.settings import LoadSettings  # noqa: E402
from integration import quick_sampler  # noqa: E402
from ui import controller as C  # noqa: E402


@dataclass
class Entry:
    path: str
    filename: str
    pack: str = ""
    kind: str | None = None
    duration: float = 0.0
    mtime: float = 0.0
    bpm: int | None = None
    bpm_confidence: str = "none"
    key: str | None = None
    key_quality: str | None = None
    key_confidence: str = "none"
    sample_rate: int = 44100
    channels: int = 2

    def summary(self) -> str:
        return f"{self.filename}、{self.kind}"


class FakeLibrary:
    def __init__(self, entries):
        self.entries = entries

    def load(self, quiet=False):
        return True

    def search(self, text=None, **_):
        return [e for e in self.entries if not text or text.lower() in e.filename.lower()]


class FakeEngine:
    EXIT_OK, EXIT_NO_WINDOW, EXIT_NO_LOGIC, EXIT_NO_PERMISSION = 0, 1, 2, 5
    EXIT_NO_DMD = 16
    EXIT_NO_TRACK, EXIT_NOT_AUDIO_TRACK = 23, 24
    MESSAGES = {EXIT_NO_DMD: "Drum Machine Designer の画面が開いていません",
                EXIT_NO_TRACK: "トラックが選ばれていません",
                EXIT_NOT_AUDIO_TRACK: "選んでいるトラックはオーディオトラックではありません"}

    def __init__(self):
        self.calls = []
        self.load_code = 0
        self.state = SimpleNamespace(flex=True, follow=False)
        self.gate = None
        self.dmd_open = True
        self.pad_titles = {1: "91V_HT_kick_candy", 2: "Inst 4"}
        # オーディオトラック（2026-09-25 の Untitled と同じ値）
        self.tempo, self.key, self.can_import, self.track = 120.0, "C Major", True, "Audio 1"
        self.selected = 2
        self.paste_code = 0

    # -- オーディオトラック --
    # トラックの一覧（Untitled と同じ並び）。オーディオは 2 と、試験用に足した 5
    TRACKS = [(1, "Classic Electric Piano", False), (2, "Audio", True), (3, "Deluxe Classic", False),
              (4, "Empty Kit", False), (5, "Audio 2", True)]

    def _state(self, snap="off"):
        tracks = [(i, n if i != 2 else self.track, i == self.selected) for i, n, _ in self.TRACKS]
        name = next((n for i, n, sel in tracks if sel), "")
        audio = dict((i, a) for i, _, a in self.TRACKS).get(self.selected, False) and self.can_import
        return SimpleNamespace(code=0, message=f"貼り付け先は {name}（トラック {self.selected}）",
                               summary=f"貼り付け先は {name}（トラック {self.selected}）。プロジェクトは {self.tempo:g} BPM",
                               tempo=self.tempo, key=self.key, meter=(4, 4), sample_rate=44100, division=16,
                               position=(1, 1, 1, 1), track_index=self.selected if self.track else None,
                               track_name=name, can_import=audio, tracks=tracks, snap=snap)

    def read_project(self, log=None, snap="off"):
        self.calls.append(("project", snap))
        return self._state(snap)

    def choose_track(self, number, snap="off", log=None):
        self.calls.append(("choose", number, snap))
        self.selected = number
        st = self._state(snap)
        st.message = f"トラック {number}、{st.track_name}" + ("（オーディオ）" if st.can_import else "。貼れません")
        return st

    def step_audio_track(self, delta, snap="off", log=None):
        self.calls.append(("step", delta, snap))
        audio = [i for i, _, a in self.TRACKS if a]
        self.selected = audio[(audio.index(self.selected) + delta) % len(audio)] if self.selected in audio else audio[0]
        st = self._state(snap)
        st.message = f"トラック {self.selected}、{st.track_name}（オーディオ）"
        return st

    def paste_audio(self, path, snap, log=None, playhead="stay"):
        self.calls.append(("paste", path, snap, playhead))
        if self.paste_code:
            return SimpleNamespace(code=self.paste_code, message="補助の文言", note="")
        return SimpleNamespace(code=0, message=f"{Path(path).stem} を {self.track} の 1 小節目の頭に貼りました。長さは 2 小節",
                               note="")

    # -- DMD --
    def find_dmd_target(self):
        if not self.dmd_open:
            return SimpleNamespace(code=self.EXIT_NO_WINDOW, title="", win=None)
        return SimpleNamespace(code=self.EXIT_OK, title="Empty Kit", win="dmd")

    def read_pads(self, win):
        notes = ["C1", "C♯1", "D1", "D♯1"]
        return {i: SimpleNamespace(index=i, note=notes[(i - 1) % 4], name=f"PAD {i}", title=self.pad_titles.get(i, ""))
                for i in range(1, 49)}

    def load_into_pad(self, path, index, optimized, flex, follow, log=None):
        self.calls.append(("pad", path, index, optimized, flex, follow))
        if self.load_code:
            return SimpleNamespace(code=self.load_code, message=self.MESSAGES.get(self.load_code, "補助の文言"))
        self.pad_titles[index] = Path(path).stem
        state = SimpleNamespace(flex=bool(flex), follow=bool(follow))
        return SimpleNamespace(code=0, message=f"{Path(path).name} を Empty Kit のパッド {index}（D1）に読み込みました",
                               title="Empty Kit", loaded=Path(path).name, state=state, switched=False, flex_note="")

    def load_file(self, path, optimized, flex, follow, log=None):
        if self.gate is not None:
            self.gate.wait(2)
        self.calls.append(("load", path, optimized, flex, follow))
        if self.load_code:
            return SimpleNamespace(code=self.load_code, message="補助の文言")
        state = None if self.state is None else SimpleNamespace(flex=bool(flex), follow=bool(follow))
        return SimpleNamespace(code=0, message="", title="Deluxe Classic", loaded=Path(path).name,
                               state=state, switched=False, flex_note="")

    def describe_target(self):
        return SimpleNamespace(code=0, message="Deluxe Classic の Quick Sampler。素材は x.wav。Flex は切り")

    def change_flex(self, which, want):
        self.calls.append(("flex", which, want))
        return SimpleNamespace(code=0, message="Flex を入れました。音程を変えても速さは変わりません")


class FakePlayer:
    def __init__(self):
        self.current_path = None
        self.is_active = False
        self.stopped = 0

    def load(self, path):
        self.current_path = str(path)

    def play(self):
        self.is_active = True

    def stop(self):
        self.is_active = False
        self.stopped += 1

    def toggle_play_stop(self):
        self.is_active = not self.is_active

    def describe_state_ja(self):
        return f"{Path(self.current_path).name}、{'再生中' if self.is_active else '停止中'}"


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.splice = self.tmp / "packs"
        self.stage = self.tmp / "stage"
        loop = self.splice / "Afro" / "Loop_120.wav"
        shot = self.splice / "Drums" / "Kick_OneShot.wav"
        longshot = self.splice / "Drums" / "Pad_one_shot.wav"
        for p in (loop, shot, longshot):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"RIFF")
        self.entries = [
            Entry(str(loop), loop.name, "Afro", "loop", 4.0),
            Entry(str(shot), shot.name, "Drums", "one_shot", 0.4),
            # ファイル名はワンショットでも 5 秒ある → 長さを優先してループ（3 つのアプリの規則）
            Entry(str(longshot), longshot.name, "Drums", "one_shot", 5.0),
        ]
        self.engine = FakeEngine()
        self.player = FakePlayer()
        self.prompts = []
        self._orig_prompt = quick_sampler.request_trust_prompt
        quick_sampler.request_trust_prompt = lambda: self.prompts.append(1)
        self.trusted = True
        self.preview = self.tmp / "preview"
        self.backend = "ffmpeg"
        self.renders = []
        self.render_ok = True
        self.c = C.AppController(
            library_factory=lambda: FakeLibrary(self.entries),
            engine=lambda: self.engine,
            player_factory=lambda: self.player,
            trusted=lambda: self.trusted,
            renderer=self._fake_render,
            backend=lambda: self.backend,
            settings_path=self.tmp / "settings.json",
            stage_root=self.stage,
            preview_root=self.preview,
            splice_root=self.splice,
        )

    def _fake_render(self, src, dest, plan):
        """音は作らない。何を頼まれたかを覚えて、印のファイルを書く。"""
        self.renders.append((Path(src), Path(dest), plan))
        if not self.render_ok:
            return SimpleNamespace(ok=False, path="", message="ffmpeg が失敗しました: テスト")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"RIFF-fitted")
        return SimpleNamespace(ok=True, path=str(dest), message="")

    def tearDown(self):
        quick_sampler.request_trust_prompt = self._orig_prompt

    # -- 検索 --

    def test_search_and_kind_by_duration(self):
        self.assertTrue(self.c.search(C.SearchForm()))
        self.assertEqual(len(self.c.results), 3)
        self.assertTrue(self.c.search(C.SearchForm(kind="loop")))
        self.assertEqual([e.filename for e in self.c.results], ["Loop_120.wav", "Pad_one_shot.wav"])
        # 一覧の表示も判定し直した種別
        self.assertIn("loop", self.c.result_labels()[1])

    def test_search_without_index(self):
        c = C.AppController(library_factory=lambda: None, engine=lambda: self.engine,
                            settings_path=self.tmp / "s2.json")
        res = c.search(C.SearchForm())
        self.assertFalse(res)
        self.assertTrue(res.message_ja.startswith("[エラー]"))
        # キーは読み上げで分かる書き方（「Cmd+R」は英語で読まれる。2026-10-24 のレビュー）
        self.assertIn("コマンド+R", res.message_ja)
        self.assertNotIn("Cmd+", res.message_ja)

    def test_fail_message_is_never_empty(self):
        # 補助が理由を返さなくても「[エラー] 。」とだけ言わない
        self.assertEqual(self.c._fail_message(self.engine, 99, ""), "[エラー] 理由は分かりません。")

    # -- Splice のフォルダ（2026-10-24。配布に向けて、選び直せるようにした） --

    def test_splice_folder_can_be_chosen_again(self):
        other = self.tmp / "Elsewhere" / "Splice"
        packs = other / "sounds" / "packs"
        packs.mkdir(parents=True)
        c = C.AppController(library_factory=lambda: FakeLibrary(self.entries), engine=lambda: self.engine,
                            settings_path=self.tmp / "s3.json")
        self.assertTrue(c.search(C.SearchForm()))
        res = c.set_splice_root(str(other))                    # Splice のフォルダそのものを選んでも
        self.assertTrue(res, res.message_ja)
        self.assertEqual(c.splice_root, packs)                 # 中のパックの並ぶフォルダを使う
        self.assertEqual(C.AppController(settings_path=self.tmp / "s3.json").splice_root, packs)   # 次に開いても
        self.assertFalse(c._library_tried)                     # 前のフォルダの索引は使わない
        self.assertEqual(c.results, [])
        self.assertFalse(c.set_splice_root(str(self.tmp / "nowhere")))
        self.assertEqual(c.splice_root, packs)

    def test_rescan_of_a_missing_folder_says_how_to_choose(self):
        c = C.AppController(library_factory=lambda: FakeLibrary([]), engine=lambda: self.engine,
                            settings_path=self.tmp / "s4.json", splice_root=self.tmp / "missing")
        res = c.rescan_library()
        self.assertFalse(res)
        self.assertIn("「Splice のフォルダを選ぶ」", res.message_ja)   # 「0 件」とだけ言わない

    def test_distribution_keeps_the_index_outside_the_app(self):
        from core.settings import INDEX_PATH
        if C.load_ptcore("library") is None:
            self.skipTest("ptcore が無い")
        saved = C.FROZEN
        try:
            C.FROZEN = True
            frozen = C._make_library(self.splice)
        finally:
            C.FROZEN = saved
        self.assertEqual(frozen.index_path, str(INDEX_PATH))
        local = C._make_library(self.splice)                   # 手元の版は借り先の索引を共有する
        self.assertTrue(local.index_path.endswith(str(Path("data") / "index.json")), local.index_path)

    def test_no_match_is_failure(self):
        res = self.c.search(C.SearchForm(text="zzz"))
        self.assertFalse(res)
        self.assertIn("ありませんでした", res.message_ja)

    # -- 読み込み --

    def test_loop_is_staged_and_loaded_with_flex(self):
        self.c.search(C.SearchForm(text="Loop_120"))
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        kind, path, optimized, flex, follow = self.engine.calls[-1]
        # **Splice の元ではなく、作業用フォルダの複製を読み込ませる**
        self.assertEqual(Path(path), self.stage / "Afro" / "Loop_120.wav")
        self.assertTrue(Path(path).is_file())
        self.assertEqual((optimized, flex, follow), (False, True, False))
        self.assertIn("ループなので、Flex を入れました", res.message_ja)
        self.assertIn("速さは変わりません", res.message_ja)

    def test_one_shot_turns_flex_off(self):
        self.c.search(C.SearchForm(text="Kick"))
        res = self.c.load_entry(0)
        self.assertEqual(self.engine.calls[-1][3:], (False, None))
        self.assertIn("ワンショットなので、Flex は切ってあります", res.message_ja)

    def test_settings_are_passed(self):
        self.c.update_settings(LoadSettings(mode="optimized", flex="on", follow="on"))
        self.c.search(C.SearchForm(text="Kick"))
        res = self.c.load_entry(0)
        self.assertEqual(self.engine.calls[-1][2:], (True, True, True))
        self.assertIn("Follow Tempo も入れてある", res.message_ja)
        self.assertNotIn("ワンショットなので", res.message_ja)  # 自分で選んだ設定なので理由は言わない

    def test_preview_stops_before_loading(self):
        self.c.search(C.SearchForm(text="Loop_120"))
        self.c.toggle_preview(self.c.result_path(0))
        self.assertTrue(self.player.is_active)
        self.c.load_entry(0)
        self.assertFalse(self.player.is_active)

    def test_no_window_speaks_app_wording(self):
        self.engine.load_code = FakeEngine.EXIT_NO_WINDOW
        self.c.search(C.SearchForm(text="Loop_120"))
        res = self.c.load_entry(0)
        self.assertFalse(res)
        self.assertTrue(res.message_ja.startswith("[エラー] Quick Sampler の画面が開いていません"))
        self.assertIn("もう一度読み込んでください", res.message_ja)

    def test_other_failure_uses_engine_wording(self):
        self.engine.load_code = 12
        self.c.search(C.SearchForm(text="Loop_120"))
        res = self.c.load_entry(0)
        self.assertEqual(res.message_ja, "[エラー] 補助の文言。")

    def test_flex_not_confirmed_is_warning(self):
        self.engine.state = None
        self.c.search(C.SearchForm(text="Loop_120"))
        res = self.c.load_entry(0)
        self.assertFalse(res)
        self.assertTrue(res.message_ja.startswith("[警告]"))

    def test_without_permission_prompts_once(self):
        self.trusted = False
        self.c.search(C.SearchForm(text="Loop_120"))
        first = self.c.load_entry(0)
        second = self.c.load_entry(0)
        self.assertFalse(first)
        self.assertIn("アクセシビリティ", first.message_ja)
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(self.engine.calls, [])
        self.assertFalse(second)

    def test_only_one_logic_job_at_a_time(self):
        self.engine.gate = threading.Event()
        self.c.search(C.SearchForm(text="Loop_120"))
        results = []
        worker = threading.Thread(target=lambda: results.append(self.c.load_entry(0)))
        worker.start()
        for _ in range(100):
            if self.c.busy:
                break
            threading.Event().wait(0.01)
        busy = self.c.detect_target()
        self.engine.gate.set()
        worker.join(3)
        self.assertFalse(busy)
        self.assertIn("いま Logic を操作しています", busy.message_ja)
        self.assertTrue(results and results[0])

    def test_missing_file(self):
        res = self.c.load_path(str(self.tmp / "nope.wav"), kind="loop")
        self.assertFalse(res)
        self.assertIn("見つかりません", res.message_ja)

    # -- 試聴・Flex・読み込み先 --

    def test_preview_toggle_and_auto_preview(self):
        path = self.entries[0].path
        self.assertIn("再生中", self.c.toggle_preview(path).message_ja)
        self.assertIn("停止中", self.c.toggle_preview(path).message_ja)
        self.assertIsNone(self.c.preview_selected(path))  # 既定は自動で鳴らさない
        self.c.update_settings(LoadSettings(auto_preview=True))
        self.assertIsNotNone(self.c.preview_selected(self.entries[1].path))
        self.assertEqual(self.player.current_path, self.entries[1].path)
        self.assertTrue(self.player.is_active)

    def test_change_flex_and_detect(self):
        res = self.c.change_flex("flex")
        self.assertTrue(res)
        self.assertEqual(self.engine.calls[-1], ("flex", "flex", None))
        self.assertTrue(self.c.detect_target().message_ja.startswith("読み込み先は Deluxe Classic"))

    # -- DMD のパッド --

    def test_target_and_pad_steps(self):
        self.assertFalse(self.c.dmd_mode)
        self.assertIn("DMD のパッドにしました", self.c.set_target("dmd").message_ja)
        self.assertTrue(self.c.dmd_mode)
        self.assertEqual(len(self.c.pad_labels()), 48)
        self.assertEqual(self.c.step_pad(-1).message_ja, "パッド 48。")     # 1 の前は 48（一覧を読む前）
        self.c.step_pad(1)
        self.assertEqual(self.c.pad_index, 1)
        self.assertFalse(self.c.set_target("battery"))

    def test_refresh_pads_and_labels(self):
        self.c.set_target("dmd")
        res = self.c.refresh_pads()
        self.assertTrue(res, res.message_ja)
        self.assertIn("Empty Kit の DMD", res.message_ja)
        labels = self.c.pad_labels()
        self.assertEqual(labels[0], "パッド 1、C1 PAD 1。中身は 91V_HT_kick_candy")
        self.assertEqual(labels[2], "パッド 3、D1 PAD 3。空き")
        self.engine.dmd_open = False
        res = self.c.detect_target()          # DMD のときは一覧を読み直す
        self.assertFalse(res)
        self.assertIn("Drum Machine Designer の画面が開いていません", res.message_ja)

    def test_load_into_pad_then_advance(self):
        self.c.set_target("dmd")
        self.c.set_pad(3)
        self.c.search(C.SearchForm(text="Kick"))
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        kind, path, index, optimized, flex, follow = self.engine.calls[-1]
        self.assertEqual((kind, index, flex), ("pad", 3, False))
        self.assertEqual(Path(path), self.stage / "Drums" / "Kick_OneShot.wav")   # 複製を読み込ませる
        self.assertIn("パッド 3（D1）に読み込みました", res.message_ja)
        self.assertNotIn("Flex", res.message_ja)       # パッドのワンショットでは Flex のことは言わない
        self.assertIn("次はパッド 4、D♯1 PAD 4。空き", res.message_ja)
        self.assertEqual(self.c.pad_index, 4)
        self.assertEqual(self.c.pad_labels()[2], "パッド 3、D1 PAD 3。中身は Kick_OneShot")

    def test_pad_advance_off_and_wrap(self):
        from core.settings import LoadSettings
        self.c.update_settings(LoadSettings(target="dmd", pad_advance=False))
        self.c.set_pad(48)
        self.c.search(C.SearchForm(text="Kick"))
        self.c.load_entry(0)
        self.assertEqual(self.c.pad_index, 48)
        self.c.update_settings(LoadSettings(target="dmd", pad_advance=True))
        self.c.load_entry(0)
        self.assertEqual(self.c.pad_index, 1)                 # 48 の次は 1

    def test_pad_failure_and_flex_refused(self):
        self.c.set_target("dmd")
        self.engine.load_code = FakeEngine.EXIT_NO_DMD
        self.c.search(C.SearchForm(text="Kick"))
        res = self.c.load_entry(0)
        self.assertFalse(res)
        self.assertTrue(res.message_ja.startswith("[エラー] Drum Machine Designer の画面が開いていません"))
        self.assertEqual(self.c.pad_index, 1)                 # 失敗したら進めない
        self.assertFalse(self.c.change_flex("flex"))

    def test_pad_step_says_what_is_inside(self):
        # パッドを移ったら、何が入っているかを言う（2026-09-25 の指摘）
        self.c.set_target("dmd")
        self.c.refresh_pads()
        # 「Inst 4」は空のパッドを押したときにできるチャンネルの名前。素材ではないので空きと言う
        self.assertEqual(self.c.step_pad(1).message_ja, "パッド 2、C♯1 PAD 2。空き（Inst 4）。")
        self.assertEqual(self.c.step_pad(1).message_ja, "パッド 3、D1 PAD 3。空き。")
        self.assertEqual(self.c.step_pad(-2).message_ja, "パッド 1、C1 PAD 1。中身は 91V_HT_kick_candy。")

    def test_pack_choices_show_counts(self):
        # パックの横に中のサンプルの数を書く（2026-09-25 の要望）。並びは中身の多い順
        self.assertEqual(self.c.pack_choices(), [("すべてのパック（3 件）", ""), ("Drums（2 件）", "Drums"),
                                                 ("Afro（1 件）", "Afro")])

    def test_pack_order_cycles_and_is_saved(self):
        # 既定は中身の多い順（Drums が 2 件、Afro が 1 件）
        self.assertEqual(self.c.packs(), ["Drums", "Afro"])
        self.assertIn("名前順", self.c.cycle_pack_order().message_ja)
        self.assertEqual(self.c.packs(), ["Afro", "Drums"])
        again = C.AppController(library_factory=lambda: FakeLibrary(self.entries), engine=lambda: self.engine,
                                settings_path=self.tmp / "settings.json")
        self.assertEqual(again.settings.pack_order, "name")

    # -- オーディオトラック（テンポとキーを合わせて貼る） --

    def _track_loop(self):
        """102 BPM・F# マイナーの 2 小節のループ（実物と同じ値）。"""
        loop = self.splice / "Lenno" / "DS_LENNO_102_guitar_F#min.wav"
        loop.parent.mkdir(parents=True, exist_ok=True)
        loop.write_bytes(b"RIFF")
        entry = Entry(str(loop), loop.name, "Lenno Nu Disco 2", "loop", 207529 / 44100, bpm=102, bpm_confidence="high",
                      key="F#", key_quality="minor", key_confidence="high")
        self.entries.append(entry)
        self.c.set_target("track")
        self.c.search(C.SearchForm(text="LENNO"))
        return entry

    def test_track_paste_fits_then_pastes(self):
        entry = self._track_loop()
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        src, dest, plan = self.renders[-1]
        self.assertEqual(src, Path(entry.path))                   # 元の素材から作る（元は書き換えない）
        self.assertEqual(dest, self.stage / "Lenno Nu Disco 2" / "DS_LENNO_102_guitar_F#min__120bpm_even_+3_Am.wav")
        self.assertEqual((plan.semitones, plan.out_frames), (3, 176400))
        self.assertEqual(self.engine.calls[-1], ("paste", str(dest), "off", "end"))   # 既定で再生位置を終わりへ進める
        self.assertIn("を Audio 1 の 1 小節目の頭に貼りました", res.message_ja)
        self.assertIn("102 BPM を 120 BPM に。F#マイナーをAマイナーに（3 半音上げ）", res.message_ja)
        self.assertEqual(Path(entry.path).read_bytes(), b"RIFF")

    def test_paste_reuses_what_was_previewed(self):
        entry = self._track_loop()
        fitted, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(note, "")
        self.assertEqual(Path(fitted), self.preview / "Lenno Nu Disco 2" / "DS_LENNO_102_guitar_F#min__120bpm_even_+3_Am.wav")
        self.assertEqual(len(self.renders), 1)
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        self.assertEqual(len(self.renders), 1)                    # 聴いた音を複製して貼る（作り直さない）
        final = self.stage / "Lenno Nu Disco 2" / Path(fitted).name
        self.assertEqual(final.read_bytes(), b"RIFF-fitted")
        # 同じ条件でもう一度貼ると、作業用フォルダのものを使い回す（前に貼ったプロジェクトが参照している）
        self.c.load_entry(0)
        self.assertEqual(len(self.renders), 1)

    def test_preview_toggles_and_is_cleared(self):
        entry = self._track_loop()
        signals = []
        fitted, _ = self.c.fitted_preview(entry.path, entry, "loop", entry.pack, on_render=lambda: signals.append(1))
        self.assertEqual(signals, [1])                            # 作り始めたら合図（音）
        self.c.fitted_preview(entry.path, entry, "loop", entry.pack, on_render=lambda: signals.append(2))
        self.assertEqual(signals, [1])                            # 作ってあれば合図しない
        self.assertTrue(self.c.play_fitted(fitted, entry.path))
        self.assertEqual(self.player.current_path, fitted)
        self.assertIsNone(self.c.stop_if_previewing("/other.wav"))
        self.assertTrue(self.c.stop_if_previewing(entry.path))    # スペースをもう一度押したら止まる
        self.assertIsNone(self.c.stop_if_previewing(entry.path))  # 止まっていれば何もしない（次は鳴らす）
        self.c.clear_previews()
        self.assertFalse(Path(fitted).exists())

    def test_not_an_audio_track(self):
        self._track_loop()
        self.engine.can_import = False
        res = self.c.load_entry(0)
        self.assertFalse(res)
        self.assertTrue(res.message_ja.startswith("[エラー] 選んでいるトラックはオーディオトラックではありません"))
        self.assertEqual(self.renders, [])
        self.assertNotIn("paste", [c[0] for c in self.engine.calls])

    def test_render_failure_does_not_paste(self):
        self._track_loop()
        self.render_ok = False
        res = self.c.load_entry(0)
        self.assertFalse(res)
        self.assertIn("テンポとキーを合わせた音を作れなかったので、貼りませんでした", res.message_ja)
        self.assertNotIn("paste", [c[0] for c in self.engine.calls])

    def test_nothing_to_fit_pastes_the_staged_copy(self):
        entry = self._track_loop()
        self.engine.tempo, self.engine.key = 102.0, "F♯ Minor"
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        self.assertEqual(self.renders, [])
        self.assertEqual(self.engine.calls[-1][1], str(self.stage / "Lenno Nu Disco 2" / Path(entry.path).name))
        self.assertIn("キーはF#マイナーのまま", res.message_ja)

    def test_key_choice_transpose_and_tempo_switch(self):
        self._track_loop()
        self.assertTrue(self.c.refresh_project())
        self.assertEqual(self.c.goal().key, "Cmaj")                # プロジェクトの Key Signature
        self.assertEqual(self.c.set_key_choice("Am").message_ja, "キーはAマイナーに合わせます。")
        self.assertEqual(self.c.goal().key, "Am")
        self.c.set_key_choice(C.fit.KEY_OFF)
        self.assertIsNone(self.c.goal().key)
        self.assertFalse(self.c.set_key_choice("H"))
        self.assertEqual(self.c.set_transpose(-2).message_ja, "さらに 2 半音下げます。")
        self.assertEqual(self.c.set_transpose(40).message_ja, "さらに 12 半音上げます。")
        self.assertEqual(self.c.goal().transpose, 12)
        from core.settings import LoadSettings
        self.c.update_settings(LoadSettings(target="track", fit_tempo=False))
        self.assertIsNone(self.c.goal().tempo)
        self.assertIn("テンポは合わせません", self.c.fit_phrase())

    def test_engine_warning_comes_last(self):
        self._track_loop()
        warn = "貼ったあと、Logic がプロジェクトのテンポを 120 から 102 に変えました"
        original = self.engine.paste_audio

        def paste_with_warning(path, snap, log=None, playhead="stay"):
            res = original(path, snap, playhead=playhead)
            res.message += f"。[警告] {warn}"
            res.note = warn
            return res

        self.engine.paste_audio = paste_with_warning
        res = self.c.load_entry(0)
        self.assertFalse(res)                                     # 失敗の音で知らせる
        self.assertEqual(res.message_ja.count("[警告]"), 1)
        self.assertTrue(res.message_ja.endswith(f"[警告] {warn}。"))
        self.assertIn("貼りました。長さは 2 小節。102 BPM を 120 BPM に", res.message_ja)

    def test_rubberband_renders_do_not_reuse_ffmpeg_files(self):
        self._track_loop()
        self.c.load_entry(0)                                      # ffmpeg で作って貼った
        old = self.stage / "Lenno Nu Disco 2" / "DS_LENNO_102_guitar_F#min__120bpm_even_+3_Am.wav"
        self.assertTrue(old.is_file())
        self.backend = "rubberband"                               # あとから rubberband を入れた
        self.c.load_entry(0)
        new = self.stage / "Lenno Nu Disco 2" / "rubberband" / old.name
        self.assertEqual(self.renders[-1][1], new)                # 作り直して、別のフォルダに置く
        self.assertEqual(self.engine.calls[-1][1], str(new))
        self.assertEqual(len(self.renders), 2)
        self.assertTrue(old.is_file())                            # 前に貼ったほうはそのまま（プロジェクトが参照する）

    def test_playhead_after_paste_is_passed_on(self):
        # 貼ったあとの再生位置は 3 つから選ぶ（2026-09-26 の要望）。既定は終わりへ進める（続けて貼ると並ぶ）
        self._track_loop()
        from core.settings import LoadSettings
        self.c.load_entry(0)
        self.assertEqual(self.engine.calls[-1][3], "end")
        for mode in ("start", "stay"):
            with self.subTest(mode=mode):
                self.c.update_settings(LoadSettings(target="track", playhead=mode))
                self.c.load_entry(0)
                self.assertEqual(self.engine.calls[-1][3], mode)

    def test_choose_and_step_tracks(self):
        self._track_loop()
        self.assertEqual(self.c.track_labels(), [])                   # 読む前は空
        self.c.refresh_project()
        self.assertEqual(self.c.track_labels()[:2], ["トラック 1、Classic Electric Piano", "トラック 2、Audio 1"])
        self.assertEqual(self.c.selected_track_row(), 1)
        res = self.c.step_track(1)                                     # 次のオーディオトラック（3・4 は飛ばす）
        self.assertTrue(res, res.message_ja)
        self.assertEqual(res.message_ja, "トラック 5、Audio 2（オーディオ）。")
        self.assertEqual(self.c.selected_track_row(), 4)
        self.assertEqual(self.engine.calls[-1], ("step", 1, "off"))
        self.assertEqual(self.c.step_track(1).message_ja, "トラック 2、Audio 1（オーディオ）。")   # 端から戻る
        res = self.c.choose_track(0)                                   # 欄で音源のトラックを選ぶ → 選ぶが、貼れないと言う
        self.assertFalse(res)
        self.assertEqual(self.engine.calls[-1], ("choose", 1, "off"))
        self.assertFalse(self.c.choose_track(9))
        self.assertEqual(self.c.project.track_name, "Classic Electric Piano")     # 選んだトラックを覚えている

    def test_track_list_is_checked_again_before_choosing(self):
        # 欄は最後に読んだ一覧のまま。Logic でトラックを足して番号がずれていたら、名前で選び直す（2026-09-26 のレビュー）
        self._track_loop()
        self.c.refresh_project()
        self.engine.TRACKS = [(1, "Classic Electric Piano", False), (2, "Audio", False), (3, "Audio 1", True),
                              (4, "Deluxe Classic", False), (5, "Empty Kit", False), (6, "Audio 2", True)]
        self.engine.track = "New Track"          # 偽の Logic はトラック 2 の名前をこれにする（2 番目に足した）
        self.engine.selected = 3
        row = next(i for i, t in enumerate(self.c.track_labels()) if t.endswith("Audio 2"))   # 欄では「トラック 5、Audio 2」
        res = self.c.choose_track(row)
        self.assertTrue(res, res.message_ja)
        self.assertEqual(self.engine.calls[-1], ("choose", 6, "off"))                         # 番号ではなく名前で
        self.assertEqual(self.c.track_labels()[1], "トラック 2、New Track")                   # 一覧も読み直した
        # 名前が見つからなければ選ばずに、読み直した一覧を出す
        self.engine.TRACKS = [(1, "Classic Electric Piano", False), (2, "Audio", True)]
        self.engine.selected = 2
        res = self.c.choose_track(5)
        self.assertFalse(res)
        self.assertIn("トラックの並びが変わっていて", res.message_ja)
        self.assertNotEqual(self.engine.calls[-1][0], "choose")
        self.assertEqual(len(self.c.track_labels()), 2)

    def test_unchecked_track_type_is_not_ok(self):
        # インスペクタが出ていないと種類を確かめられない。貼れると言わない（失敗の音）
        self._track_loop()
        original = self.engine._state

        def hidden(snap="off"):
            st = original(snap)
            st.checked = False
            return st

        self.engine._state = hidden
        self.assertFalse(self.c.refresh_project())
        self.assertFalse(self.c.step_track(1))

    def test_preview_does_not_reread_a_fresh_project(self):
        entry = self._track_loop()
        self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        reads = [c for c in self.engine.calls if c[0] == "project"]
        self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(len([c for c in self.engine.calls if c[0] == "project"]), len(reads))   # 1.5 秒以内は読み直さない
        self.c._project_read_at -= 10
        self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(len([c for c in self.engine.calls if c[0] == "project"]), len(reads) + 1)

    def test_detect_and_flex_in_track_mode(self):
        self._track_loop()
        res = self.c.detect_target()
        self.assertTrue(res, res.message_ja)
        self.assertIn("貼り付け先は Audio 1", res.message_ja)
        self.assertIn("テンポは Even、120 BPM に合わせます。キーはプロジェクトのキー、Cメジャーに合わせます", res.message_ja)
        self.assertFalse(self.c.change_flex("flex"))
        self.engine.can_import = False
        self.assertFalse(self.c.detect_target())                  # 貼れないトラックなら失敗の音

    def test_switching_to_track_mode_speaks_briefly(self):
        # 切り替えたときは短く（2026-10-13 の要望。全部言うと約 20 秒）。全部はコマンド+T（detect_target）
        self._track_loop()
        brief = self.c.detect_target(brief=True)
        self.assertTrue(brief, brief.message_ja)
        self.assertEqual(brief.message_ja, "貼り付け先は Audio 1。120 BPM、Cメジャー。")
        full = self.c.detect_target()
        self.assertIn("テンポは Even、120 BPM に合わせます", full.message_ja)
        self.assertGreater(len(full.message_ja), len(brief.message_ja))
        # 既定と違う合わせ方だけを足す
        self.c.set_tempo_scale(0.5)
        self.c.toggle_key_match()
        self.c.set_transpose(-2)
        self.assertEqual(self.c.detect_target(brief=True).message_ja,
                         "貼り付け先は Audio 1。120 BPM、Cメジャー。テンポはハーフ。キーは合わせません（素材のまま）。"
                         "さらに 2 半音下げます。")
        # 貼れないときは理由まで言う（プロジェクトの要約は言わない）。文は補助の track_phrase（トラックの名前から言う）
        self.engine.can_import = False
        self.engine.track_phrase = lambda st: f"{st.track_name}、トラック {st.track_index}。オーディオトラックではないので、貼れません"
        res = self.c.detect_target(brief=True)
        self.assertFalse(res)
        self.assertEqual(res.message_ja, "Audio 1、トラック 2。オーディオトラックではないので、貼れません。")
        # track_phrase の無い古い補助なら、要約の文の頭（本物の補助の project_phrase と同じ形）
        del self.engine.track_phrase
        made = self.engine._state

        def not_audio(snap="off"):
            st = made(snap)
            st.message = st.summary = ("選んでいるトラック Audio 1 はオーディオトラックではないので、貼れません。"
                                       "プロジェクトは 120 BPM、C メジャー、4/4、44.1 kHz。再生位置は 1 小節目の頭")
            return st
        self.engine._state = not_audio
        res = self.c.detect_target(brief=True)
        self.assertFalse(res)
        self.assertNotIn("プロジェクトは", res.message_ja)
        self.assertIn("貼れません", res.message_ja)

    def test_failed_render_is_not_called_the_fitted_sound(self):
        # 合わせた音を作れず元の音で鳴らすときに、「合わせた音、…」と言わない（前の計画も持ち越さない。2026-10-13 のレビュー）
        entry = self._track_loop()
        fitted, _ = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(list(self.c._fitted_plans), [fitted])          # 覚えるのは、いちばん新しい 1 つだけ
        self.render_ok = False
        self.c.set_tempo_scale(0.5)                                      # 作ったことの無い条件
        path, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(path, entry.path)
        self.assertTrue(note.startswith("[警告] 合わせた音を作れなかったので、元の音で鳴らします"), note)
        res = self.c.play_fitted(path, entry.path)
        self.assertTrue(res)
        self.assertNotIn("合わせた音", res.message_ja)
        self.assertTrue(self.c.stop_if_previewing(entry.path))            # シフト+スペースをもう一度で止まる
        self.c.clear_previews()
        self.assertEqual(self.c._fitted_plans, {})

    def test_preview_without_a_project_says_what_is_not_matched(self):
        # Logic を読めないとき、合わせられないものを全部言う（キーを「プロジェクトのキー」にしていたら、キーも。
        # それまでは「テンポは合わせずに」とだけ言っていた。2026-10-24 のレビュー）
        from core import fit
        entry = self._track_loop()
        self.trusted = False                                 # プロジェクトを読めない
        _, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(note, "Logic のプロジェクトが読めないので、テンポとキーは合わせずに鳴らします。")
        self.c.set_key_choice("Am")                          # キーを選んでいれば、キーは合わせられる
        _, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(note, "Logic のプロジェクトが読めないので、テンポは合わせずに鳴らします。")
        self.c.set_key_choice(fit.KEY_PROJECT)
        self.c.update_settings(LoadSettings(target="track", fit_tempo=False))
        _, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(note, "Logic のプロジェクトが読めないので、キーは合わせずに鳴らします。")
        self.c.set_key_choice(fit.KEY_OFF)
        _, note = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(note, "")

    def test_space_plays_the_original_and_shift_space_the_fitted_sound(self):
        # 2026-10-13: スペースは読み込み先がオーディオトラックでも元の音。合わせた音はシフト+スペース（fitted_preview）
        entry = self._track_loop()
        self.assertIn("再生中", self.c.toggle_preview(entry.path).message_ja)
        self.assertEqual(self.player.current_path, entry.path)
        self.assertEqual(self.renders, [])                         # 元の音を鳴らすだけ。作らない
        # 元の音が鳴っているときにシフト+スペース → 止めずに、合わせた音へ切り替える
        self.assertIsNone(self.c.stop_if_previewing(entry.path))
        fitted, _ = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        res = self.c.play_fitted(fitted, entry.path)
        self.assertTrue(res)
        self.assertTrue(res.message_ja.startswith("合わせた音、Even、120 BPM。"), res.message_ja)
        self.assertEqual(self.player.current_path, fitted)
        # 合わせた音が鳴っているときにスペース → 元の音へ切り替える（止めない）
        self.assertIn("再生中", self.c.toggle_preview(entry.path).message_ja)
        self.assertEqual(self.player.current_path, entry.path)
        self.assertIsNone(self.c.stop_if_previewing(entry.path))   # いまは元の音なので、シフト+スペースは合わせた音へ

    def test_auto_preview_plays_the_original_in_track_mode(self):
        entry = self._track_loop()
        self.c.update_settings(LoadSettings(target="track", auto_preview=True))
        self.assertIsNotNone(self.c.preview_selected(entry.path))
        self.assertEqual(self.player.current_path, entry.path)
        self.assertEqual(self.renders, [])

    def test_half_and_double_when_pasting(self):
        entry = self._track_loop()
        res = self.c.set_tempo_scale(0.5)
        self.assertTrue(res)
        self.c.refresh_project()
        self.assertIn("テンポはハーフ、120 BPM の半分の 60 BPM に合わせます", self.c.fit_phrase())
        res = self.c.load_entry(0)
        self.assertTrue(res, res.message_ja)
        _, dest, plan = self.renders[-1]
        self.assertEqual(dest.name, "DS_LENNO_102_guitar_F#min__120bpm_half_+3_Am.wav")
        self.assertEqual(plan.out_frames, 352800)                  # 2 小節が 60 BPM で 8 秒
        self.assertIn("102 BPM を、120 BPM のハーフ（60 BPM）に", res.message_ja)
        # 試聴（シフト+スペース）も同じ合わせ方の音
        fitted, _ = self.c.fitted_preview(entry.path, entry, "loop", entry.pack)
        self.assertEqual(Path(fitted).name, dest.name)
        self.assertTrue(self.c.play_fitted(fitted, entry.path).message_ja.startswith("合わせた音、ハーフ、60 BPM。"))
        # 順送りはハーフ → Even → ダブル → ハーフ
        self.assertIn("Even", self.c.cycle_tempo_scale().message_ja)
        self.assertIn("テンポはダブル、120 BPM の倍の 240 BPM に合わせます", self.c.cycle_tempo_scale().message_ja)
        self.assertEqual(self.c.cycle_tempo_scale() and self.c.tempo_scale, 0.5)
        self.assertFalse(self.c.set_tempo_scale(3.0))
        # 保存しない（アプリを開き直すと Even に戻る）
        self.assertEqual(C.AppController(settings_path=self.c.settings_path).tempo_scale, 1.0)


if __name__ == "__main__":
    unittest.main()
