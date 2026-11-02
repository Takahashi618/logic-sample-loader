"""画面と中身の橋渡し。**wx に依存しない。**

画面の部品を組み立てるのは `main_frame.py` の仕事で、ここは「押されたら何をするか」
だけを持つ。分けてあるのは、**判断の部分を画面なしでテストできるようにするため**
（`tests/test_controller.py`）。

依存は差し替えられるようにしてある。

| 依存 | 既定 | テストでは |
| --- | --- | --- |
| Splice の索引 | `ptcore/library.py` | スタブ |
| Quick Sampler・DMD・トラックの操作 | `tools/qs_sample_menu.py` | スタブ |
| 試聴 | `speech_and_preview/audio/player.py` | スタブ |
| 音を合わせる | `integration/render.py`（ffmpeg） | スタブ |
| 権限の確認 | `AXIsProcessTrusted` | 常に真 |

**素材が 1 件も無い、Logic が動いていない、といった状態でも落ちない。**
理由を言葉で返して（Mac の言語に合わせて日本語か英語。core/i18n.py）、利用者が次に何をすればよいか分かるようにする。
"""

from __future__ import annotations

import re
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from core import fit
from core.i18n import ERROR, SEP, WARNING, is_english, sentence, sentences, tr
from core.settings import (INDEX_PATH, SETTINGS_PATH, LoadSettings, flex_for, follow_for, load_settings,
                           resolve_splice_root, save_settings)
from core.sibling import FROZEN, load_ptcore
from core.staging import (PREVIEW_ROOT, SPLICE_PACKS, STAGE_ROOT, clear_previews, copy_into_place, fitted_path,
                          stage)
from integration import quick_sampler
from integration import render as render_module
from ui import borrowed

__all__ = [
    "Outcome",
    "SearchForm",
    "AppController",
    "KIND_CHOICES",
    "SPLICE_ROOT",
    "PAD_COUNT",
    "pad_phrase",
    "KEY_CHOICES",
    "TRANSPOSE_CHOICES",
    "TEMPO_CHOICES",
]

# 主画面の「キー」と「移調」の欄（オーディオトラックに貼るとき）。**保存しない**（core/settings.py の説明）
KEY_CHOICES: list[tuple[str, str]] = fit.key_choices()
TRANSPOSE_CHOICES: list[tuple[str, int]] = fit.transpose_choices()
# 「テンポ」の欄（ハーフ・Even・ダブル）。これも**保存しない**（前の曲の合わせ方のまま次の曲に貼らないように）
TEMPO_CHOICES: list[tuple[str, float]] = list(fit.TEMPO_CHOICES)

# 試聴のたびにプロジェクトを読み直すが、これより新しければ読み直さない（秒）。読み直す間は Logic を操作する
# 排他を握るので、続けて試聴すると、その間に押した Enter が「操作しています」で断られる
PROJECT_FRESH_SECONDS = 1.5


def _engine_log(message: str) -> None:
    """補助（Logic を操作する部品）の経過を、アプリのログ（~/Library/Logs/LogicSampleLoader.log）に残す。

    **うまくいかなかったとき、どこで止まったかを後から調べられるようにする**（2026-09-26、アプリから貼って
    「ファイルの場所へ移動できませんでした」になったとき、理由が残っていなかった）。ランチャが標準エラーをログに流す。
    """
    try:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [補助] {message}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _pasteable(st: Any) -> bool:
    """貼れるトラックを選んでいると**確かめられた**か（インスペクタが出ていないと確かめられない）。"""
    return bool(getattr(st, "can_import", False)) and bool(getattr(st, "checked", True))

PAD_COUNT = 48
# パッドの名前として出るが、素材が入っていないことを表すもの
_EMPTY_TITLES = ("", "Empty Pad")
# 空のパッドを押すとできるチャンネルの既定の名前（「Inst 4」など）。素材の名前ではないので「空き」と言う
_BLANK_CHANNEL = re.compile(r"^Inst \d+$")


def pad_phrase(index: int, pad: Any | None) -> str:
    """パッドの読み上げ用の言い方。**中身を必ず言う**（「パッド 3、D1 SNARE 1。中身は 91V_HT_snare_candy」
    「パッド 6、F1 PERC 1。空き」）。パッドを移ったとき、何が入っているかが分からないと選べない（2026-09-25 の指摘）。"""
    if pad is None:
        return tr(f"パッド {index}", f"Pad {index}")
    title = (getattr(pad, "title", "") or "").strip()
    if title in _EMPTY_TITLES:
        state = tr("空き", "Empty")
    elif _BLANK_CHANNEL.match(title):
        state = tr(f"空き（{title}）", f"Empty ({title})")
    else:
        state = tr(f"中身は {title}", f"Contains {title}")
    # 名前は空のことがある（選んだパッドの見出しの代わりに音名だけが出ているとき。qs_sample_menu.pad_label_spots）
    label = f"{getattr(pad, 'note', '')} {getattr(pad, 'name', '')}".strip()
    return tr(f"パッド {index}、{label}。{state}", f"Pad {index}, {label}. {state}")

# Splice のパックが並ぶフォルダの既定（いまの場所は AppController.splice_root。設定で選び直せる）
SPLICE_ROOT = SPLICE_PACKS

# 種別の絞り込み。**言葉は借りている kinds.py から取る**
# 英語の画面では借り物（日本語）を使わず、ここの言葉にする
_KIND_LABELS_EN = {"one_shot": "One-shot", "loop": "Loop", "fx": "FX", "": "Unknown"}
_KIND_LABELS = _KIND_LABELS_EN if is_english() else dict(getattr(borrowed.kinds, "KIND_LABELS", {}) or {
    "one_shot": "ワンショット", "loop": "ループ", "fx": "効果音", "": "不明"})
KIND_CHOICES: list[tuple[str, str]] = [
    (tr("すべての種別", "All types"), ""),
    (_KIND_LABELS.get("one_shot", "ワンショット"), "one_shot"),
    (_KIND_LABELS.get("loop", "ループ"), "loop"),
    (_KIND_LABELS.get("fx", "効果音"), "fx"),
]

_PACK_ORDER_OPTIONS = list(getattr(borrowed.kinds, "PACK_ORDER_OPTIONS", []) or [("count", "中身の多い順")])
_PACK_ORDER_EN = {"count": "most samples first", "name": "by name", "mtime": "recently updated first",
                  "added": "order added, oldest first"}
if is_english():
    _PACK_ORDER_OPTIONS = [(value, _PACK_ORDER_EN.get(value, value)) for value, _ in _PACK_ORDER_OPTIONS]

# 試聴の状態（借り物の player は日本語で返す）を英語にする言い換え
_PLAYER_EN = (
    (re.compile(r"^ファイルが読み込まれていません$"), "No file loaded"),
    (re.compile(r"^(.*)、停止中$"), r"\1, stopped"),
    (re.compile(r"^(.*)、再生中、"), r"\1, playing, "),
    (re.compile(r"^(.*)、一時停止中、"), r"\1, paused, "),
    (re.compile(r"(\d+(?:\.\d+)?)秒目"), r"at \1 s"),
    (re.compile(r"位置不明"), "position unknown"),
    (re.compile(r"、全長(\d+(?:\.\d+)?)秒$"), r", length \1 s"),
)


def player_state(player: Any) -> str:
    """試聴の状態の読み上げ（「91V_kick.wav、再生中、0.0秒目」/「91V_kick.wav, playing, at 0.0 s」）。"""
    text = player.describe_state_ja()
    if not is_english():
        return text
    for pattern, repl in _PLAYER_EN:
        text = pattern.sub(repl, text)
    return text


def summary_en(entry: Any) -> str:
    """一覧の 1 行の英語（索引の summary は日本語なので組み立て直す。並びは同じ）。"""
    parts = [getattr(entry, "description", "") or getattr(entry, "filename", "") or str(entry)]
    if getattr(entry, "bpm", None):
        parts.append(f"{entry.bpm} BPM")
    if getattr(entry, "key", None):
        quality = {"minor": "m", "major": "maj"}.get(getattr(entry, "key_quality", "") or "", "")
        parts.append(fit.key_label(f"{entry.key}{quality}"))
    parts.append(_KIND_LABELS.get(getattr(entry, "kind", "") or "", _KIND_LABELS[""]))
    duration = getattr(entry, "duration", None)
    if isinstance(duration, (int, float)):
        parts.append(f"{duration:.1f} s")
    return ", ".join(parts)

# 読み込みに失敗したときに足す「次にすること」。**アプリから使うときの言い方**にする
# （補助のほうの文言は Keyboard Maestro のキーを押す前提で書いてある）
_APP_MESSAGES = {
    "no_window": tr("Quick Sampler の画面が開いていません。Logic で読み込み先のトラックを選び、"
                    "ハイフンで Quick Sampler の画面を開いてから、もう一度読み込んでください",
                    "The Quick Sampler window is not open. In Logic, select the track to load into, "
                    "open Quick Sampler with the Hyphen key, then load again"),
    "no_logic": tr("Logic Pro が起動していません。Logic でプロジェクトを開いてから、もう一度読み込んでください",
                   "Logic Pro is not running. Open a project in Logic, then load again"),
    "no_permission": tr("アクセシビリティの許可がありません。システム設定の「プライバシーとセキュリティ」→"
                        "「アクセシビリティ」で、Logicサンプルローダーを入れてください。"
                        "操作メニューの「アクセシビリティの設定を開く」で、その画面を開けます",
                        "Accessibility permission is missing. In System Settings, Privacy & Security, "
                        "Accessibility, turn on Logic Sample Loader. "
                        "Open Accessibility Settings in the Actions menu takes you there"),
}


@dataclass(frozen=True)
class Outcome:
    """操作の結果。**成否を持たせる**（音は文言ではなく成否から選ぶ）。"""

    ok: bool
    message_ja: str

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class SearchForm:
    """検索の条件。空文字は「指定しない」。"""

    text: str = ""
    key: str = ""
    relative: bool = False
    bpm: str = ""
    tolerance: int = 4
    kind: str = ""
    pack: str = ""

    def describe_ja(self) -> str:
        """条件の読み上げ（名前は昔のまま。英語の画面では英語で返す）。"""
        parts = []
        if self.text:
            parts.append(tr(f"語「{self.text}」", f"word \"{self.text}\""))
        if self.key:
            parts.append(tr(f"キー {self.key}" + ("、平行調も" if self.relative else ""),
                            f"key {self.key}" + (" and relative" if self.relative else "")))
        if self.bpm:
            parts.append(tr(f"BPM {self.bpm}、前後 {self.tolerance}", f"BPM {self.bpm} plus or minus {self.tolerance}"))
        if self.kind:
            parts.append(_KIND_LABELS.get(self.kind, self.kind))
        if self.pack:
            parts.append(tr(f"パック {self.pack}", f"pack {self.pack}"))
        return tr("、", ", ").join(parts) if parts else tr("すべての素材", "all samples")


def _make_library(root: Path):
    """Splice の索引。**配布版は索引を .app の外（INDEX_PATH）に置く**（ptcore の既定は ptcore の隣で、
    配布版では .app の中になる。書くと署名が壊れる）。手元の版は借り先の索引を共有する。"""
    module = load_ptcore("library")
    if module is None:
        return None
    return module.Library(str(root), index_path=str(INDEX_PATH) if FROZEN else None)


class AppController:
    """押されたら何をするか。"""

    def __init__(
        self,
        *,
        library_factory: Callable[[], Any] | None = None,
        engine: Callable[[], Any] | None = None,
        player_factory: Callable[[], Any] | None = None,
        trusted: Callable[[], bool] | None = None,
        renderer: Callable[[Path, Path, Any], Any] | None = None,
        backend: Callable[[], str] | None = None,
        settings_path: Path = SETTINGS_PATH,
        stage_root: Path = STAGE_ROOT,
        preview_root: Path = PREVIEW_ROOT,
        splice_root: Path | None = None,
    ) -> None:
        self._library_factory = library_factory or (lambda: _make_library(self.splice_root))
        self._engine = engine or quick_sampler.engine
        self._player_factory = player_factory or borrowed.make_player
        self._trusted = trusted or quick_sampler.is_trusted
        self._render = renderer or render_module.render
        self._backend = backend or render_module.backend_name
        self.settings_path = settings_path
        self.stage_root = stage_root
        self.preview_root = preview_root
        # テストで決めた場所（None なら設定の「Splice のフォルダ」。splice_root）
        self._fixed_splice_root = splice_root
        self.settings: LoadSettings = load_settings(settings_path)
        self.results: list[Any] = []
        self._library: Any | None = None
        self._library_tried = False
        self._player: Any | None = None
        self._player_tried = False
        # **Logic を操作する処理は同時に 1 つだけ。** 前面のアプリ・Logic の画面・キー入力は
        # 共有の資源なので、読むだけの処理も同じ排他に入れる
        self._busy = threading.Lock()
        self._prompted = False
        # DMD のパッド（読み込み先が DMD のとき）。一覧は Logic から読み直したときだけ新しくなる
        self.pad_index = 1
        self.pads: dict[int, Any] = {}
        self.kit = ""
        # オーディオトラック（読み込み先がトラックのとき）。プロジェクトは Logic から読み直したときだけ新しくなる
        self.project: Any | None = None
        self._project_read_at = 0.0          # いつ読んだか（試聴で読み直すかの判断に使う）
        self.key_choice = fit.KEY_PROJECT
        self.transpose = 0
        self.tempo_scale = fit.TEMPO_EVEN
        # いま鳴っている「合わせた音」の元の素材（スペースとシフト+スペースのどちらで鳴らしたかを見分ける）。
        # 元の音を鳴らしているときは None
        self._preview_source: str | None = None
        # いちばん新しい「合わせた音」のファイル → どう合わせたか（読み上げに使う）。**1 つだけ覚える**
        # （作るのは 1 つずつで、作り終えたらすぐ鳴らすので、前のものは要らない。覚え続けると起動中ずっと増える）
        self._fitted_plans: dict[str, fit.FitPlan] = {}

    # -- 設定 -------------------------------------------------------------

    def update_settings(self, new: LoadSettings) -> None:
        self.settings = new
        try:
            save_settings(new, self.settings_path)
        except OSError:
            pass  # 保存できなくても、この起動のあいだは効く

    def cycle_pack_order(self) -> Outcome:
        values = [value for value, _ in _PACK_ORDER_OPTIONS]
        nxt = borrowed.kinds.next_pack_order(self.settings.pack_order) if borrowed.kinds else values[0]
        self.update_settings(replace(self.settings, pack_order=nxt))
        label = dict(_PACK_ORDER_OPTIONS).get(nxt, nxt)
        return Outcome(True, tr(f"パックの並び順を{label}にしました。", f"Pack order: {label}."))

    @property
    def splice_root(self) -> Path:
        """Splice のパックが並ぶフォルダ（設定の「Splice のフォルダ」。空なら決まった場所を探す）。"""
        return self._fixed_splice_root or resolve_splice_root(self.settings.splice_root)

    def set_splice_root(self, chosen: str) -> Outcome:
        """Splice のフォルダを選び直す（操作メニューの「Splice のフォルダを選ぶ」）。**索引は作り直してから使う。**

        `~/Splice` そのものを選んでも、中の `sounds/packs` を使う（resolve_splice_root）。
        """
        root = resolve_splice_root(chosen)
        if not root.is_dir():
            return Outcome(False, tr(f"[エラー] フォルダが見つかりません: {root}", f"[Error] Folder not found: {root}"))
        self.update_settings(replace(self.settings, splice_root=str(root)))
        self._library, self._library_tried = None, False      # 前のフォルダの索引は使わない
        self.results = []
        return Outcome(True, tr(f"Splice のフォルダを {root} にしました。", f"Splice folder set to {root}."))

    # -- 索引 -------------------------------------------------------------

    @property
    def library(self) -> Any | None:
        """Splice の索引。**一度だけ読み込む。** 読めなければ None（ファイルを直接選ぶ経路は残る）。"""
        if not self._library_tried:
            self._library_tried = True
            lib = self._library_factory()
            self._library = lib if lib is not None and lib.load(quiet=True) else None
        return self._library

    def rescan_library(self) -> Outcome:
        """Splice の索引を作り直す。**素材を買い足しても索引が古いままだと見つからない。**"""
        if not self.splice_root.is_dir():
            # 無いフォルダを数えると「0 件」と言うだけで、何が悪いのか分からない
            return Outcome(False, tr(f"[エラー] Splice のフォルダが見つかりません（{self.splice_root}）。"
                                     "操作メニューの「Splice のフォルダを選ぶ」で、素材の入っているフォルダを選んでください。",
                                     f"[Error] The Splice folder was not found ({self.splice_root}). "
                                     "Use Choose Splice Folder in the Actions menu to pick the folder with your samples."))
        lib = self._library or self._library_factory()
        if lib is None:
            return Outcome(False, tr("[エラー] Splice の索引の部品が見つかりません。", "[Error] The Splice index component was not found."))
        try:
            stats = lib.scan()          # 作り直した索引は scan が保存する（ここで二度書かない）
        except Exception as exc:
            return Outcome(False, tr(f"[エラー] 索引を更新できませんでした: {exc}", f"[Error] Could not update the index: {exc}"))
        self._library = lib
        self._library_tried = True
        message = tr(f"索引を更新しました。{stats.get('total', 0)} 件。うち {stats.get('parsed', 0)} 件を読み直しました。",
                     f"Index updated. {stats.get('total', 0)} samples, {stats.get('parsed', 0)} of them read again.")
        if stats.get("unreadable"):
            message += tr(f"{stats['unreadable']} 件は読めませんでした。", f" {stats['unreadable']} could not be read.")
        return Outcome(True, message)

    def packs(self) -> list[str]:
        lib = self.library
        if lib is None or borrowed.kinds is None:
            return sorted({getattr(e, "pack", "") for e in (lib.entries if lib else []) if getattr(e, "pack", "")})
        try:
            return borrowed.kinds.sort_packs(borrowed.kinds.pack_stats(lib.entries), self.settings.pack_order)
        except Exception:
            return []

    def pack_choices(self) -> list[tuple[str, str]]:
        """パックの欄に並べる (言葉, パック名)。**パックの横に中のサンプルの数を書く**（2026-09-25 の要望）。

        先頭は「すべてのパック」（パック名は空文字）。数は索引に入っている件数。
        """
        lib = self.library
        entries = list(lib.entries) if lib is not None else []
        counts: dict[str, int] = {}
        for e in entries:
            name = (getattr(e, "pack", "") or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        choices = [(tr(f"すべてのパック（{len(entries)} 件）", f"All packs ({len(entries)})"), "")]
        choices += [(tr(f"{name}（{counts.get(name, 0)} 件）", f"{name} ({counts.get(name, 0)})"), name)
                    for name in self.packs()]
        return choices

    def search(self, form: SearchForm) -> Outcome:
        """条件で絞り込む。結果は `self.results` に入る。"""
        lib = self.library
        if lib is None:
            self.results = []
            return Outcome(False, tr("[エラー] Splice の索引が読めません。コマンド+R で索引を作るか、"
                                     "コマンド+O でファイルを選んで読み込んでください。"
                                     "Splice のフォルダが違う場所なら、操作メニューの「Splice のフォルダを選ぶ」で選びます。",
                                     "[Error] Cannot read the Splice index. Press Command-R to build it, "
                                     "or Command-O to choose a file and load it. "
                                     "If your Splice folder is elsewhere, use Choose Splice Folder in the Actions menu."))
        bpm: int | None = None
        if form.bpm.strip():
            try:
                bpm = int(form.bpm.strip())
            except ValueError:
                return Outcome(False, tr(f"[エラー] BPM は数字で入れてください: {form.bpm}",
                                         f"[Error] Enter the BPM as a number: {form.bpm}"))
        try:
            found = list(lib.search(
                text=form.text or None,
                bpm=bpm,
                tolerance=form.tolerance,
                key=form.key or None,
                relative=form.relative,
                # **種別は索引に任せない。** あちらはファイル名だけで判定する。長さを見て自分で絞る
                kind=None,
                pack=form.pack or None,
            ))
        except Exception as exc:
            self.results = []
            return Outcome(False, tr(f"[エラー] 検索に失敗しました: {exc}", f"[Error] Search failed: {exc}"))
        if form.kind:
            found = [e for e in found if self.kind_of(e) == form.kind]
        self.results = found
        if not found:
            return Outcome(False, tr(f"{form.describe_ja()} に合う素材はありませんでした。",
                                     f"No samples match {form.describe_ja()}."))
        return Outcome(True, tr(f"{len(found)} 件見つかりました。{form.describe_ja()}。",
                                f"Found {len(found)}. {form.describe_ja()}."))

    def kind_of(self, entry: Any) -> str:
        """素材の種別。**長さを優先する**（借りている kinds.py）。"""
        name_kind = getattr(entry, "kind", "") or ""
        duration = getattr(entry, "duration", None)
        if borrowed.kinds is None:
            return name_kind
        return borrowed.kinds.resolve_kind(name_kind, duration)

    def result_labels(self) -> list[str]:
        """一覧に並べる文字列。**1 行 1 件、判定し直した種別で。**"""
        labels = []
        for entry in self.results:
            resolved = self.kind_of(entry)
            if (getattr(entry, "kind", "") or "") != resolved:
                try:
                    entry = replace(entry, kind=resolved)
                except TypeError:
                    pass
            if is_english():
                labels.append(summary_en(entry))
                continue
            summary = getattr(entry, "summary", None)
            labels.append(summary() if callable(summary) else str(entry))
        return labels

    def result_entry(self, index: int) -> Any | None:
        return self.results[index] if 0 <= index < len(self.results) else None

    def result_path(self, index: int) -> str | None:
        entry = self.result_entry(index)
        return getattr(entry, "path", None) if entry is not None else None

    # -- 試聴 -------------------------------------------------------------

    def _get_player(self):
        if not self._player_tried:
            self._player_tried = True
            self._player = self._player_factory()
        return self._player

    def toggle_preview(self, path: str | None) -> Outcome:
        """スペース 1 つで再生と停止を切り替える。**元の音のまま鳴らす**
        （読み込み先がオーディオトラックでも。プロジェクトに合わせた音はシフト+スペース。2026-10-13 の要望）。"""
        outcome = self._toggle_player(path)
        if outcome.ok:
            self._preview_source = None
        return outcome

    def _toggle_player(self, path: str | None) -> Outcome:
        player = self._get_player()
        if player is None:
            return Outcome(False, tr("[エラー] 試聴は使えません。just_playback が要ります。",
                                     "[Error] Preview is not available. It needs just_playback."))
        if not path:
            return Outcome(False, tr("一覧から素材を選んでください。", "Choose a sample in the list."))
        try:
            if player.current_path != str(path):
                player.load(path)
                player.play()
            else:
                player.toggle_play_stop()
        except Exception as exc:
            return Outcome(False, tr(f"[エラー] 試聴できませんでした: {exc}", f"[Error] Could not preview: {exc}"))
        return Outcome(True, player_state(player))

    def preview_selected(self, path: str | None) -> Outcome | None:
        """一覧で選び直したとき。**「自動で試聴」が入っているときだけ、元の音を鳴らす。** 何もしなければ None。"""
        if not self.settings.auto_preview or not path:
            return None
        player = self._get_player()
        if player is None:
            return None
        try:
            if player.current_path == str(path) and player.is_active:
                return None
            player.load(path)
            player.play()
        except Exception as exc:
            return Outcome(False, tr(f"[エラー] 試聴できませんでした: {exc}", f"[Error] Could not preview: {exc}"))
        self._preview_source = None
        return Outcome(True, "")

    def stop_preview(self) -> None:
        """鳴らしっぱなしにしない。読み込む前と終了時に呼ぶ。まだ作っていなければ作らない。"""
        if self._player is None:
            return
        try:
            self._player.stop()
        except Exception:
            pass

    # -- 読み込み先とパッド -------------------------------------------------

    @property
    def dmd_mode(self) -> bool:
        return self.settings.target == "dmd"

    @property
    def track_mode(self) -> bool:
        return self.settings.target == "track"

    def set_target(self, target: str) -> Outcome:
        if target not in ("qs", "dmd", "track"):
            return Outcome(False, tr(f"[エラー] 知らない読み込み先です: {target}", f"[Error] Unknown destination: {target}"))
        self.update_settings(replace(self.settings, target=target))
        if target == "dmd":
            pad = pad_phrase(self.pad_index, self.pads.get(self.pad_index))
            return Outcome(True, tr(f"読み込み先を DMD のパッドにしました。いまは{pad}。",
                                    f"Destination: DMD pad. Now on {pad}."))
        if target == "track":
            return Outcome(True, tr("読み込み先を Logic のオーディオトラックにしました。テンポとキーを合わせてから貼ります。",
                                    "Destination: Logic audio track. Samples are matched to the tempo and key, then pasted."))
        return Outcome(True, tr("読み込み先を Quick Sampler の画面にしました。", "Destination: Quick Sampler window."))

    def pad_labels(self) -> list[str]:
        """パッドの欄に並べる 48 個の言葉。"""
        return [pad_phrase(i, self.pads.get(i)) for i in range(1, PAD_COUNT + 1)]

    def set_pad(self, index: int) -> Outcome:
        index = max(1, min(PAD_COUNT, int(index)))
        self.pad_index = index
        return Outcome(True, sentence(pad_phrase(index, self.pads.get(index))))

    def step_pad(self, delta: int) -> Outcome:
        """次・前のパッドへ。**48 の次は 1 に戻る**（端で止まると、止まったことに気づきにくい）。"""
        return self.set_pad((self.pad_index - 1 + delta) % PAD_COUNT + 1)

    def refresh_pads(self) -> Outcome:
        """DMD のパッドの一覧を Logic から読み直す。**押さない・切り替えない。**"""
        def work(qs):
            return self._read_pads(qs)
        return self._exclusive(work)

    def _read_pads(self, qs) -> Outcome:
        t = qs.find_dmd_target()
        if t.code != qs.EXIT_OK:
            code = getattr(qs, "EXIT_NO_DMD", t.code) if t.code == qs.EXIT_NO_WINDOW else t.code
            return Outcome(False, self._fail_message(qs, code, qs.MESSAGES.get(code, "")))
        self.pads = dict(qs.read_pads(t.win))
        self.kit = t.title
        filled = sum(1 for p in self.pads.values()
                     if (getattr(p, "title", "") or "") not in _EMPTY_TITLES
                     and not _BLANK_CHANNEL.match(getattr(p, "title", "") or ""))
        pad = pad_phrase(self.pad_index, self.pads.get(self.pad_index))
        return Outcome(True, tr(f"{t.title} の DMD。パッド {len(self.pads)} 個のうち、名前の付いたものが {filled} 個。"
                                f"いまは{pad}。",
                                f"DMD on {t.title}. {filled} of {len(self.pads)} pads have names. Now on {pad}."))

    # -- オーディオトラック（テンポとキーを合わせて貼る） ---------------------------

    def refresh_project(self, brief: bool = False) -> Outcome:
        """Logic のプロジェクト（テンポ・キー・再生位置・選んでいるトラック）を読み直す。**押さない。**

        brief は短く言う（読み込み先を切り替えたとき。brief_project_phrase）。コマンド+T は全部言う。
        """
        return self._exclusive(lambda qs: self._read_project(qs, brief))

    def _read_project(self, qs, brief: bool = False) -> Outcome:
        st = qs.read_project(log=_engine_log, snap=self.settings.snap)
        if st.code != qs.EXIT_OK:
            return Outcome(False, self._fail_message(qs, st.code, st.message))
        self._remember(st)
        if brief:
            return Outcome(_pasteable(st), sentence(self.brief_project_phrase(qs)))
        return Outcome(_pasteable(st), sentences([st.message, self.fit_phrase()]))

    def brief_project_phrase(self, qs: Any | None = None) -> str:
        """読み込み先をオーディオトラックに切り替えたときの短い読み上げ（「貼り付け先は Audio 1。120 BPM、Cメジャー」）。

        全部（トラックの番号・拍子・サンプルレート・再生位置・空いているか・合わせ方）を言うと約 20 秒かかり、
        切り替えるたびに待たされた（2026-10-13 の要望で縮めた。全部はコマンド+T）。合わせ方は、**既定と違うときだけ**言う
        （ハーフ・ダブル、キーを合わせない・別のキー、移調、テンポを合わせない設定）。
        貼れないとき（トラックが無い・オーディオトラックでない・確かめられない）は、理由まで言う。
        その文は補助の `track_phrase`（コマンド+右ブラケットで移ったときと同じ、**トラックの名前から言う**文）。
        補助の要約の文を「。プロジェクトは」で切って使うと、要約の言い回しが変わったとき黙って長いまま読む。
        """
        st = self.project
        if st is None:
            return tr("プロジェクトをまだ読んでいません", "The project has not been read yet")
        if not _pasteable(st) or getattr(st, "track_index", None) is None:
            phrase = getattr(qs, "track_phrase", None) if qs is not None else None
            if phrase is not None:
                return phrase(st)
            return (getattr(st, "summary", "") or st.message).split("。プロジェクトは")[0]   # 古い補助（track_phrase が無い）
        tempo = getattr(st, "tempo", None)
        key = fit.logic_key_query(getattr(st, "key", "") or "")
        project = tr("、", ", ").join([f"{fit.format_bpm(tempo)} BPM" if tempo else tr("テンポ不明", "tempo unknown"),
                                      fit.key_label(key) if key else tr("キー不明", "key unknown")])
        parts = [tr(f"貼り付け先は {st.track_name}", f"Paste target: {st.track_name}"), project]
        if not self.settings.fit_tempo:
            parts.append(tr("テンポは合わせません", "Tempo is not matched"))
        elif self.tempo_scale != fit.TEMPO_EVEN:
            parts.append(tr(f"テンポは{fit.tempo_word(self.tempo_scale)}", f"Tempo: {fit.tempo_word(self.tempo_scale)}"))
        if self.key_choice != fit.KEY_PROJECT:
            parts.append(tr(f"キーは{self._key_goal_phrase()}", f"Key: {self._key_goal_phrase()}"))
        if self.transpose:
            parts.append(self._transpose_phrase())
        if getattr(st, "selected_count", 0) > 1:
            parts.append(tr("ほかにも選ばれているトラックがあります", "Other tracks are also selected"))
        return SEP.join(parts)

    @property
    def track_pasteable(self) -> bool:
        """最後に読んだプロジェクトで、貼れるトラック（オーディオトラック）を選んでいると確かめられたか。"""
        return self.project is not None and _pasteable(self.project)

    def _remember(self, st) -> None:
        self.project = st
        self._project_read_at = time.monotonic()

    # -- 貼り付け先のトラック（コマンド+右ブラケット・左ブラケット、または欄で選ぶ） --

    def track_labels(self) -> list[str]:
        """「貼り付け先のトラック」の欄に並べる言葉（「トラック 2、Audio 1」）。まだ読んでいなければ空。"""
        tracks = getattr(self.project, "tracks", []) if self.project is not None else []
        return [tr(f"トラック {index}、{name}", f"Track {index}, {name}") for index, name, _ in tracks]

    def selected_track_row(self) -> int | None:
        """欄の中で、いま Logic で選ばれているトラックの位置（0 から）。"""
        tracks = getattr(self.project, "tracks", []) if self.project is not None else []
        return next((row for row, (_, _, selected) in enumerate(tracks) if selected), None)

    def choose_track(self, row: int) -> Outcome:
        """欄の row 番目のトラックを貼り付け先にする（Logic で選ぶ）。オーディオトラックでなければ、そう言う。

        **欄は最後に読んだ一覧のまま**なので、選ぶ前に Logic から読み直し、番号と名前で突き合わせる
        （Logic でトラックを足したり並べ替えたりしていると、同じ番号が別のトラックになっている）。
        名前が 1 つだけ見つかればそちらを選ぶ。見つからなければ選ばずに、読み直した一覧を欄に出す。
        """
        tracks = getattr(self.project, "tracks", []) if self.project is not None else []
        if not 0 <= row < len(tracks):
            return Outcome(False, tr("トラックの一覧がありません。コマンド+T で読み直してください。",
                                     "There is no track list. Press Command-T to read it again."))
        number, name = tracks[row][0], tracks[row][1]

        def work(qs):
            fresh = qs.read_project(log=_engine_log, snap=self.settings.snap)
            if fresh.code != qs.EXIT_OK:
                return Outcome(False, self._fail_message(qs, fresh.code, fresh.message))
            self._remember(fresh)
            now = list(getattr(fresh, "tracks", []) or [])
            if (number, name) not in [(i, n) for i, n, _ in now]:
                same = [i for i, n, _ in now if n == name]
                if len(same) != 1:
                    return Outcome(False, tr(f"トラックの並びが変わっていて、{name} が見つかりません。"
                                             "一覧を読み直したので、もう一度選んでください。",
                                             f"The track order changed and {name} was not found. "
                                             "The list was read again, so choose once more."))
                number_now = same[0]
            else:
                number_now = number
            return self._track_outcome(qs, qs.choose_track(number_now, self.settings.snap, log=_engine_log))
        return self._exclusive(work)

    def step_track(self, delta: int) -> Outcome:
        """次（delta=1）・前（-1）のオーディオトラックを貼り付け先にする。ソフトウェア音源などは飛ばす。"""
        def work(qs):
            return self._track_outcome(qs, qs.step_audio_track(delta, self.settings.snap, log=_engine_log))
        return self._exclusive(work)

    def _track_outcome(self, qs, st) -> Outcome:
        if st.code != qs.EXIT_OK:
            return Outcome(False, self._fail_message(qs, st.code, st.message))
        self._remember(st)
        return Outcome(_pasteable(st), sentence(st.message))

    def set_key_choice(self, value: str) -> Outcome:
        labels = dict((v, k) for k, v in KEY_CHOICES)
        if value not in labels:
            return Outcome(False, tr(f"[エラー] 知らないキーです: {value}", f"[Error] Unknown key: {value}"))
        self.key_choice = value
        return Outcome(True, tr(f"キーは{self._key_goal_phrase()}。", f"Key: {self._key_goal_phrase()}."))

    def toggle_key_match(self) -> Outcome:
        """キーをプロジェクトに合わせるか、合わせないか（コマンド+K）。

        「プロジェクトのキーに合わせる」なら「合わせない」へ、それ以外（合わせない・24 のキーのどれか）なら
        「プロジェクトのキーに合わせる」へ。
        """
        nxt = fit.KEY_OFF if self.key_choice == fit.KEY_PROJECT else fit.KEY_PROJECT
        return self.set_key_choice(nxt)

    def set_tempo_scale(self, scale: float) -> Outcome:
        """ハーフ・Even・ダブル。**基準はプロジェクトの BPM。**"""
        values = [value for _, value in TEMPO_CHOICES]
        if scale not in values:
            return Outcome(False, tr(f"[エラー] 知らないテンポの合わせ方です: {scale}", f"[Error] Unknown tempo mode: {scale}"))
        self.tempo_scale = scale
        return Outcome(True, sentence(self._tempo_phrase()))

    def cycle_tempo_scale(self) -> Outcome:
        """ハーフ → Even → ダブル → ハーフ の順送り（コマンド+シフト+T）。"""
        values = [value for _, value in TEMPO_CHOICES]
        index = values.index(self.tempo_scale) if self.tempo_scale in values else 0
        return self.set_tempo_scale(values[(index + 1) % len(values)])

    def _tempo_phrase(self) -> str:
        """「テンポは Even、120 BPM に合わせます」「テンポはハーフ、120 BPM の半分の 60 BPM に合わせます」。"""
        word = fit.tempo_word(self.tempo_scale)
        if is_english():
            return self._tempo_phrase_en(word)
        head = f"テンポは {word}、" if word.isascii() else f"テンポは{word}、"
        tempo = getattr(self.project, "tempo", None) if self.project is not None else None
        if not self.settings.fit_tempo:
            return f"{head}ただし、読み込みの設定でテンポを合わせない設定です"
        if not tempo:
            how = {fit.TEMPO_HALF: "プロジェクトの BPM の半分", fit.TEMPO_DOUBLE: "プロジェクトの BPM の倍"}
            return f"{head}{how.get(self.tempo_scale, 'プロジェクトの BPM')}に合わせます（まだ Logic から読んでいません）"
        target = fit.format_bpm(tempo * self.tempo_scale)
        if self.tempo_scale == fit.TEMPO_EVEN:
            return f"{head}{target} BPM に合わせます"
        half = "半分" if self.tempo_scale < 1 else "倍"
        return f"{head}{fit.format_bpm(tempo)} BPM の{half}の {target} BPM に合わせます"

    def _tempo_phrase_en(self, word: str) -> str:
        """_tempo_phrase の英語（"Tempo: Even, matched to 120 BPM"）。"""
        head = f"Tempo: {word}, "
        tempo = getattr(self.project, "tempo", None) if self.project is not None else None
        if not self.settings.fit_tempo:
            return f"{head}but tempo matching is off in Load Settings"
        if not tempo:
            how = {fit.TEMPO_HALF: "half the project BPM", fit.TEMPO_DOUBLE: "twice the project BPM"}
            return f"{head}matched to {how.get(self.tempo_scale, 'the project BPM')} (not read from Logic yet)"
        target = fit.format_bpm(tempo * self.tempo_scale)
        if self.tempo_scale == fit.TEMPO_EVEN:
            return f"{head}matched to {target} BPM"
        half = "half" if self.tempo_scale < 1 else "twice"
        return f"{head}matched to {target} BPM, {half} of {fit.format_bpm(tempo)} BPM"

    def set_transpose(self, semitones: int) -> Outcome:
        self.transpose = max(-12, min(12, int(semitones)))
        if not self.transpose:
            return Outcome(True, tr("移調しません。", "No transpose."))
        return Outcome(True, sentence(self._transpose_phrase()))

    def _transpose_phrase(self) -> str:
        """「さらに 2 半音上げます」。移調しないなら空文字。"""
        if not self.transpose:
            return ""
        n = abs(self.transpose)
        return tr(f"さらに {n} 半音{'上げ' if self.transpose > 0 else '下げ'}ます",
                  f"Then {'up' if self.transpose > 0 else 'down'} {n} more semitone{'s' if n != 1 else ''}")

    def _key_goal(self) -> str | None:
        """合わせるキー（ptcore の書き方）。合わせないなら None。"""
        if self.key_choice == fit.KEY_OFF:
            return None
        if self.key_choice == fit.KEY_PROJECT:
            key = fit.logic_key_query(getattr(self.project, "key", "") if self.project is not None else "")
            return key or None
        return self.key_choice

    def _key_goal_phrase(self) -> str:
        if self.key_choice == fit.KEY_OFF:
            return tr("合わせません（素材のまま）", "not matched (the sample's own)")
        goal = self._key_goal()
        if self.key_choice == fit.KEY_PROJECT:
            if goal is None:
                return tr("プロジェクトのキーに合わせます（まだ Logic から読んでいません）",
                          "matched to the project key (not read from Logic yet)")
            return tr(f"プロジェクトのキー、{fit.key_label(goal)}に合わせます",
                      f"matched to the project key, {fit.key_label(goal)}")
        if not goal:
            return tr("合わせません", "not matched")
        return tr(f"{fit.key_label(goal)}に合わせます", f"matched to {fit.key_label(goal)}")

    def fit_phrase(self) -> str:
        """いまの合わせ方の読み上げ（「テンポは 120 BPM に、キーは Cメジャーに合わせます」）。"""
        if not self.settings.fit_tempo:
            head = tr("テンポは合わせません", "Tempo is not matched")
        else:
            head = self._tempo_phrase()
        parts = [head, tr(f"キーは{self._key_goal_phrase()}", f"Key: {self._key_goal_phrase()}")]
        if self.transpose:
            parts.append(self._transpose_phrase())
        return sentences(parts)

    def goal(self) -> fit.Goal:
        st = self.project
        tempo = getattr(st, "tempo", None) if st is not None and self.settings.fit_tempo else None
        return fit.Goal(tempo=tempo, tempo_scale=self.tempo_scale, key=self._key_goal(), transpose=self.transpose,
                        sample_rate=getattr(st, "sample_rate", None) if st is not None else None)

    def source_for(self, path: str | Path, entry: Any | None = None, kind: str | None = None) -> fit.Source:
        """素材の BPM・キー（索引かファイル名）と、音声ヘッダ（長さ・サンプルレート）。"""
        path = Path(path)
        if entry is None:
            splicename = load_ptcore("splicename")
            if splicename is not None:
                try:
                    entry = splicename.parse(path.name)
                except Exception:
                    entry = None
        frames = rate = channels = None
        audiofile = load_ptcore("audiofile")
        if audiofile is not None:
            try:
                info = audiofile.read_audio_info(str(path))
            except Exception:
                info = None
            if info is not None:
                frames, rate, channels = info.frames, info.sample_rate, info.channels
        if not rate:
            # MP3・CAF・FLAC・M4A は ptcore が読めない。ffprobe で調べる（0 のままだと移調が失敗する）
            probed = render_module.probe(path)
            if probed is not None:
                frames, rate, channels = probed.frames, probed.sample_rate, probed.channels
        src = fit.source_from_entry(entry, frames=frames, sample_rate=rate,
                                    kind=kind if kind is not None else self.kind_for_file(str(path)))
        return replace(src, name=path.name, channels=int(channels or src.channels or 0))

    def plan_for(self, path: str | Path, entry: Any | None = None, kind: str | None = None) -> fit.FitPlan:
        return fit.plan_fit(self.source_for(path, entry, kind), self.goal())

    def fitted_file(self, path: str | Path, pack: str, plan: fit.FitPlan, *, for_paste: bool,
                    on_render: Callable[[], None] | None = None) -> tuple[Path | None, str]:
        """plan どおりに合わせた音のファイル。(パス, 失敗の理由)。

        - 合わせる必要が無ければ、作業用フォルダへの複製（Splice の元は Logic に触らせない）
        - 貼るときは作業用フォルダ（プロジェクトが参照するので消さない）。**同じ名前がすでにあれば使い回す**
          （前に貼ったプロジェクトが参照しているファイルを書き換えない）
        - 試聴のときは試聴用の置き場所。貼るときにそちらにあれば複製して使う（聴いた音と同じものを貼る）
        """
        src = Path(path)
        if not plan.needs_render:
            if not for_paste:
                return src, ""
            try:
                return stage(src, pack, self.stage_root, self.splice_root).path, ""
            except FileNotFoundError:
                return None, tr(f"素材が見つかりません: {src.name}", f"Sample not found: {src.name}")
            except OSError as exc:
                return None, tr(f"作業用フォルダへ複製できませんでした: {exc}", f"Could not copy into the work folder: {exc}")
        tag = plan.tag()
        # ffmpeg 以外で作る音は道具の名前のフォルダへ（前に ffmpeg で作った同じ名前のファイルを使い回さない）
        engine = self._backend() or ""
        engine = "" if engine == "ffmpeg" else engine
        final = fitted_path(src, tag, pack, self.stage_root, self.splice_root, engine)
        preview = fitted_path(src, tag, pack, self.preview_root, self.splice_root, engine)
        target = final if for_paste else preview
        if target.is_file():
            return target, ""
        if for_paste and preview.is_file():
            try:
                copy_into_place(preview, final)
                return final, ""
            except OSError:
                pass   # 作り直す
        if on_render is not None:
            on_render()      # 作り始める合図（rubberband だと 20 秒のループで 1 秒ほどかかる）
        result = self._render(src, target, plan)
        if not getattr(result, "ok", False):
            return None, getattr(result, "message", "") or tr("理由は分かりません", "reason unknown")
        return Path(getattr(result, "path", "") or target), ""

    def fitted_preview(self, path: str | None, entry: Any | None = None, kind: str | None = None,
                       pack: str = "", on_render: Callable[[], None] | None = None) -> tuple[str | None, str]:
        """シフト+スペースで試聴する音（テンポとキーをプロジェクトに合わせたもの）。(パス, 添える言葉)。
        **別のスレッドから呼んでよい。** 貼るときと同じ計画で作る（聴いた音と同じものを貼る）。

        Logic を操作中でなければ、その場でプロジェクトを読み直す（テンポを変えたばかりでも、いまの値で聴ける）。
        """
        if not path:
            return None, ""
        note = ""
        fresh = self.project is not None and time.monotonic() - self._project_read_at < PROJECT_FRESH_SECONDS
        if not fresh and self._busy.acquire(blocking=False):
            try:
                if self._trusted():
                    qs = self._engine()
                    st = qs.read_project(snap=self.settings.snap) if qs is not None else None
                    if st is not None and st.code == qs.EXIT_OK:
                        self._remember(st)
            except Exception:
                pass
            finally:
                self._busy.release()
        if self.project is None:
            # 合わせられないものを全部言う（「プロジェクトのキーに合わせる」なら、キーも合わせられない）
            missing = [what for what, wanted in ((tr("テンポ", "tempo"), self.settings.fit_tempo),
                                                 (tr("キー", "key"), self.key_choice == fit.KEY_PROJECT)) if wanted]
            if missing:
                note = tr(f"Logic のプロジェクトが読めないので、{'と'.join(missing)}は合わせずに鳴らします。",
                          f"The Logic project cannot be read, so {' and '.join(missing)} are not matched. ")
        plan = self.plan_for(path, entry, kind)
        made, why = self.fitted_file(path, pack, plan, for_paste=False, on_render=on_render)
        if made is None:
            self._fitted_plans = {}       # 元の音を鳴らす。前の計画で「合わせた音」と言わない
            return path, tr(f"[警告] 合わせた音を作れなかったので、元の音で鳴らします（{why}）。",
                            f"[Warning] Could not make the matched sound, so the original plays ({why}). ")
        self._fitted_plans = {str(made): plan}
        return str(made), note

    def stop_if_previewing(self, source: str | None) -> Outcome | None:
        """いま鳴っているのが、この素材を合わせた音なら止める（シフト+スペースをもう一度押したとき）。止めなければ None。

        **元の音（スペース）が鳴っているときは止めない**（合わせた音に切り替える）。"""
        player = self._player
        if player is None or not source or self._preview_source != str(source):
            return None
        try:
            if not player.is_active:
                return None
            player.stop()
            return Outcome(True, player_state(player))
        except Exception:
            return None

    def play_fitted(self, fitted: str, source: str) -> Outcome:
        """合わせた音を鳴らす。どの素材の音かを覚えておく（止めるときに使う）。

        読み上げの頭に「合わせた音」と付ける（スペースの元の音と聞き分けられるように）。
        """
        outcome = self._toggle_player(fitted)
        if not outcome.ok:
            return outcome
        self._preview_source = str(source)
        plan = self._fitted_plans.get(str(fitted))
        if plan is None:
            # 合わせた音を作れず、元の音を鳴らしている（そう言う文は fitted_preview が先に添える）
            return outcome
        return Outcome(True, tr(f"合わせた音、{self._tempo_short(plan)}。{outcome.message_ja}",
                                f"Matched sound, {self._tempo_short(plan)}. {outcome.message_ja}"))

    @staticmethod
    def _tempo_short(plan: fit.FitPlan) -> str:
        """「Even、120 BPM」「ハーフ、60 BPM」「テンポはそのまま」（試聴の読み上げの頭に付ける短い言い方）。"""
        if not plan.tempo_fitted:
            return tr("テンポはそのまま", "tempo unchanged")
        return tr(f"{fit.tempo_word(plan.goal.tempo_scale)}、{fit.format_bpm(plan.target_tempo)} BPM",
                  f"{fit.tempo_word(plan.goal.tempo_scale)}, {fit.format_bpm(plan.target_tempo)} BPM")

    def clear_previews(self) -> None:
        """試聴用に作った音を消す（アプリを閉じるとき）。"""
        self._fitted_plans = {}
        try:
            clear_previews(self.preview_root)
        except OSError:
            pass

    def _paste_to_track(self, qs, src: Path, pack: str, kind: str, entry: Any | None) -> Outcome:
        """テンポとキーを合わせた音を作り、Logic で選んでいるオーディオトラックの再生位置に貼る。"""
        st = qs.read_project(log=_engine_log, snap=self.settings.snap)
        if st.code != qs.EXIT_OK:
            return Outcome(False, self._fail_message(qs, st.code, st.message))
        self._remember(st)
        if st.track_index is None:
            return Outcome(False, self._fail_message(qs, qs.EXIT_NO_TRACK, qs.MESSAGES[qs.EXIT_NO_TRACK]))
        if not st.can_import:
            return Outcome(False, self._fail_message(qs, qs.EXIT_NOT_AUDIO_TRACK, qs.MESSAGES[qs.EXIT_NOT_AUDIO_TRACK]))
        plan = self.plan_for(src, entry, kind)
        made, why = self.fitted_file(src, pack, plan, for_paste=True)
        if made is None:
            return Outcome(False, tr(f"[エラー] テンポとキーを合わせた音を作れなかったので、貼りませんでした（{why}）。",
                                     f"[Error] Could not make the tempo- and key-matched sound, so nothing was pasted ({why})."))
        _engine_log(f"貼る: {made}")
        res = qs.paste_audio(str(made), self.settings.snap, log=_engine_log, playhead=self.settings.playhead)
        _engine_log(f"結果: {res.code} {res.message}")
        if res.code != qs.EXIT_OK:
            return Outcome(False, self._fail_message(qs, res.code, res.message))
        # 補助の文言の「[警告] …」（テンポが変わった、など）は最後に回す。先に何をしたかを言う
        base, _, warning = res.message.partition(f"{SEP}{WARNING} ")
        parts = [base]
        done = plan.describe_ja()
        if done:
            parts.append(done)
        parts.extend(plan.notes)
        text = sentences(parts)
        if warning:
            text += tr("", " ") + sentence(f"{WARNING} {warning.rstrip('。.')}")
        return Outcome(not warning, text)

    # -- Logic ------------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._busy.locked()

    def _ready(self) -> tuple[Any | None, Outcome | None]:
        """Logic を操作できる状態か。できなければ (None, 理由)。"""
        if not self._trusted():
            if not self._prompted:
                # 一度だけ macOS に知らせて、許可の一覧にこのアプリを載せる（入り切りは利用者がする）
                self._prompted = True
                quick_sampler.request_trust_prompt()
            return None, Outcome(False, f"{ERROR} {_APP_MESSAGES['no_permission']}")
        qs = self._engine()
        if qs is None:
            return None, Outcome(False, tr("[エラー] Quick Sampler を操作する部品（tools/qs_sample_menu.py）が読めません。",
                                           "[Error] Cannot read the Quick Sampler component (tools/qs_sample_menu.py)."))
        return qs, None

    def _fail_message(self, qs: Any, code: int, message: str) -> str:
        key = {getattr(qs, "EXIT_NO_WINDOW", -1): "no_window",
               getattr(qs, "EXIT_NO_LOGIC", -1): "no_logic",
               getattr(qs, "EXIT_NO_PERMISSION", -1): "no_permission"}.get(code)
        text = (_APP_MESSAGES[key] if key else message) or tr("理由は分かりません", "Reason unknown")
        return sentence(f"{ERROR} {text}")

    def _exclusive(self, work: Callable[[Any], Outcome]) -> Outcome:
        if not self._busy.acquire(blocking=False):
            return Outcome(False, tr("いま Logic を操作しています。終わるまでお待ちください。",
                                     "Logic is being operated now. Please wait until it finishes."))
        try:
            qs, why = self._ready()
            if why is not None:
                return why
            return work(qs)
        finally:
            self._busy.release()

    def detect_target(self, brief: bool = False) -> Outcome:
        """読み込み先（Quick Sampler の画面、DMD のパッド、オーディオトラック）を調べる。**押さない・切り替えない。**

        brief は、読み込み先を切り替えたとき（オーディオトラックのときだけ短く言う）。
        """
        if self.dmd_mode:
            return self.refresh_pads()
        if self.track_mode:
            return self.refresh_project(brief)

        def work(qs):
            st = qs.describe_target()
            if st.code != qs.EXIT_OK:
                return Outcome(False, self._fail_message(qs, st.code, st.message))
            return Outcome(True, tr(f"読み込み先は {st.message}。", f"Destination: {sentence(st.message)}"))
        return self._exclusive(work)

    def change_flex(self, which: str) -> Outcome:
        """いまの Quick Sampler の Flex（which="flex"）か Follow Tempo（"follow"）を切り替える。"""
        if self.dmd_mode:
            return Outcome(False, tr("DMD のパッドでは、ここから Flex を切り替えられません。"
                                     "読み込むときに、読み込みの設定どおりにします。",
                                     "Flex cannot be toggled from here for DMD pads. "
                                     "It is set from Load Settings when loading."))
        if self.track_mode:
            return Outcome(False, tr("オーディオトラックに貼るときは Flex を使いません。"
                                     "アプリがテンポとキーを合わせた音を作ってから貼ります。",
                                     "Flex is not used when pasting on an audio track. "
                                     "The app makes a tempo- and key-matched sound, then pastes it."))

        def work(qs):
            res = qs.change_flex(which, None)
            if res.code != qs.EXIT_OK:
                return Outcome(False, self._fail_message(qs, res.code, res.message))
            return Outcome(True, sentence(res.message))
        return self._exclusive(work)

    def kind_for_file(self, path: str) -> str:
        """索引に無いファイルの種別。ファイル名と長さから決める。分からなければ空文字。"""
        name_kind = ""
        splicename = load_ptcore("splicename")
        if splicename is not None:
            try:
                name_kind = splicename.parse(Path(path).name).kind or ""
            except Exception:
                name_kind = ""
        duration = None
        audiofile = load_ptcore("audiofile")
        if audiofile is not None:
            try:
                info = audiofile.read_audio_info(path)
                duration = getattr(info, "duration", None) if info is not None else None
            except Exception:
                duration = None
        if borrowed.kinds is None:
            return name_kind
        return borrowed.kinds.resolve_kind(name_kind, duration)

    def load_entry(self, index: int) -> Outcome:
        entry = self.result_entry(index)
        if entry is None:
            return Outcome(False, tr("一覧から素材を選んでください。", "Choose a sample in the list."))
        return self.load_path(entry.path, getattr(entry, "pack", "") or "", self.kind_of(entry), entry)

    def load_path(self, path: str, pack: str = "", kind: str | None = None, entry: Any | None = None) -> Outcome:
        """素材を作業用フォルダへ複製して、Quick Sampler に読み込む。

        **Splice の元のファイルは読み込ませない**（Logic が書き足すため。core/staging.py）。
        読み込んだら、設定どおりに Flex と Follow Tempo を入れ直す
        （Flex の状態は次の素材にも引き継がれるので、毎回決める）。
        """
        if kind is None:
            kind = self.kind_for_file(path)
        self.stop_preview()

        def work(qs):
            if self.track_mode:
                if not Path(path).is_file():
                    return Outcome(False, tr(f"[エラー] 素材が見つかりません: {Path(path).name}",
                                             f"[Error] Sample not found: {Path(path).name}"))
                return self._paste_to_track(qs, Path(path), pack, kind, entry)
            try:
                staged = stage(path, pack, self.stage_root, self.splice_root)
            except FileNotFoundError:
                return Outcome(False, tr(f"[エラー] 素材が見つかりません: {Path(path).name}",
                                         f"[Error] Sample not found: {Path(path).name}"))
            except OSError as exc:
                return Outcome(False, tr(f"[エラー] 作業用フォルダへ複製できませんでした: {exc}",
                                         f"[Error] Could not copy into the work folder: {exc}"))
            flex = flex_for(self.settings, kind)
            follow = follow_for(self.settings) if flex else None
            optimized = self.settings.mode == "optimized"
            if self.dmd_mode:
                return self._load_into_pad(qs, staged.path, optimized, flex, follow, kind)
            res = qs.load_file(str(staged.path), optimized, flex, follow, log=_engine_log)
            if res.code != qs.EXIT_OK:
                return Outcome(False, self._fail_message(qs, res.code, res.message))
            return self._loaded_outcome(res, kind)
        return self._exclusive(work)

    def _load_into_pad(self, qs, path: Path, optimized: bool, flex: bool, follow, kind: str) -> Outcome:
        """DMD のパッドへ読み込み、パッドの一覧を読み直し、次のパッドへ進む。"""
        index = self.pad_index
        res = qs.load_into_pad(str(path), index, optimized, flex, follow, log=_engine_log)
        if res.code != qs.EXIT_OK:
            code = res.code
            return Outcome(False, self._fail_message(qs, code, res.message or qs.MESSAGES.get(code, "")))
        self._read_pads(qs)
        outcome = self._loaded_outcome(res, kind, base=res.message.split(f"{SEP}{WARNING}")[0], pad=True)
        if not self.settings.pad_advance:
            return outcome
        nxt = index % PAD_COUNT + 1
        self.pad_index = nxt
        pad = pad_phrase(nxt, self.pads.get(nxt))
        return Outcome(outcome.ok, outcome.message_ja + tr(f"次は{pad}。", f" Next: {pad}."))

    def _loaded_outcome(self, res: Any, kind: str, base: str | None = None, pad: bool = False) -> Outcome:
        """読み込めたときの読み上げ。**何を決めたかを必ず言う**（黙って変えると気づけない）。

        pad（DMD のパッド）のときは、Flex を入れた場合だけ言う。ドラムの単発音は鍵盤で音程を変えて
        弾くものではないので、「音程と一緒に速さも変わります」は耳の邪魔になるだけ。
        """
        parts = []
        if res.switched and base is None:
            parts.append(tr("エディタ表示に切り替えました", "Switched to the editor view"))
        parts.append(base or tr(f"{res.loaded} を {res.title} の Quick Sampler に読み込みました",
                                f"Loaded {res.loaded} into Quick Sampler on {res.title}"))
        state = res.state
        auto = self.settings.flex == "auto"
        if state is not None:
            if state.flex:
                reason = tr("ループなので、", "It is a loop, so ") if auto else ""
                parts.append(tr(f"{reason}Flex を入れました。音程を変えても速さは変わりません",
                                f"{reason}Flex is on. Changing the pitch does not change the speed"))
                if state.follow:
                    parts.append(tr("Follow Tempo も入れてあるので、プロジェクトのテンポに合わせて鳴ります",
                                    "Follow Tempo is also on, so it plays at the project tempo"))
            elif not pad:
                reason = ({"one_shot": tr("ワンショットなので、", "It is a one-shot, so "),
                           "fx": tr("効果音なので、", "It is an effect, so ")}.get(kind, "") if auto else "")
                parts.append(tr(f"{reason}Flex は切ってあります。音程と一緒に速さも変わります",
                                f"{reason}Flex is off. The speed changes with the pitch"))
        note = getattr(res, "flex_note", "")
        if note or state is None:
            text = note or tr("Flex の状態を確かめられませんでした", "the Flex state could not be checked")
            return Outcome(False, f"{WARNING} " + sentences(parts) + tr(f"ただし、{text}。", f" However, {sentence(text)}"))
        return Outcome(True, sentences(parts))

    def return_after_load(self) -> bool:
        """設定が「このアプリに戻る」なら、このアプリを最前面に戻す。戻したら True。"""
        if self.settings.after != "back":
            return False
        return quick_sampler.return_to_self()
