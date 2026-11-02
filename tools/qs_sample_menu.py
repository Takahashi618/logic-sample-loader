#!/usr/bin/env python3.14
"""qs_sample_menu.py - Quick Sampler の届かない部品を押す補助（読み込み・Flex）

背景（2026-09-24〜25、Logic Pro 12.3 で実測）:
  Quick Sampler のエディタ表示にある部品の多くは、AX に存在するのに
  ウィンドウからたどれない孤立要素で、VoiceOver の移動では届かない。
  **座標で問い合わせたときだけ見つかる。** 届かないものの中に、次の 2 つがある。

  - Sample name の欄（`AXPopUpButton`）。押すと標準のメニュー（Load Audio File… など）が開く。
    その先の「開く」ダイアログは標準の部品なので、VoiceOver で操作できる
  - Flex のボタン（**名前の無い** `AXCheckBox`）。入れると、音程を変えても速さが変わらない。
    隣の Follow Tempo（プロジェクトのテンポに合わせる）と Speed（速さの倍率）は、
    Flex を入れたときだけ使えるようになる

この道具は、届かない部品を押すところだけを肩代わりする。**Keyboard Maestro のマクロと、
Logicサンプルローダー（このプロジェクトのアプリ）の両方がこのファイルを使う。**
Quick Sampler の操作はここ 1 か所に置き、二重に書かない。

    python3.14 qs_sample_menu.py              # 欄を押してメニューを開く（コントロール+シフト+L）
    python3.14 qs_sample_menu.py --flex       # Flex を切り替える（コントロール+シフト+T）
    python3.14 qs_sample_menu.py --follow     # Follow Tempo を切り替える（コントロール+シフト+P）
    python3.14 qs_sample_menu.py --state      # いまの状態を 1 行で言う（押さない）
    python3.14 qs_sample_menu.py --load ファイル [--optimized] [--flex on|off] [--follow on|off]
    python3.14 qs_sample_menu.py --dry-run    # 欄を探すだけで押さない
    python3.14 qs_sample_menu.py --check      # 動く状態か（権限・Logic・窓）を調べるだけ

**標準出力には「読み上げる 1 行」だけを書く。** メニューを開いて成功したときは何も書かない
（開いたメニューは VoiceOver が読む）。Keyboard Maestro のマクロがこの 1 行を
VoiceOver に渡す（LM と同じ `Application("VoiceOver").output`）。
詳しい経過は標準エラーに書く（端末から試すとき用）。

終了コードは EXIT_* を見る（0 が成功）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

VERSION = "1.7.0"

# --------------------------------------------------------------------------
# 表示の言語（日本語・英語）。**core/i18n.py の写し**（この補助は 1 つのファイルで Keyboard Maestro から動くので、
# core を読み込めない）。片方を変えたら両方を変える（tests/test_i18n.py が食い違いに気づく）
# --------------------------------------------------------------------------

LANG_ENV_NAME = "LOGICLOADER_LANG"


def preferred_languages() -> List[str]:
    """Mac の「優先する言語」（例: ["ja-JP", "en-JP"]）。読めなければ環境変数 LANG から 1 つ。"""
    try:
        from Foundation import NSLocale

        langs = [str(x) for x in (NSLocale.preferredLanguages() or [])]
        if langs:
            return langs
    except Exception:
        pass
    import os
    env = os.environ.get("LANG", "")
    return [env] if env else []


def pick_language(langs: Sequence[str]) -> str:
    """並びの上から、日本語か英語の先に出てくるほう。どちらも無ければ英語。"""
    for lang in langs:
        head = lang.strip().lower().replace("_", "-").split("-")[0]
        if head in ("ja", "en"):
            return head
    return "en"


def detect_language() -> str:
    import os
    forced = os.environ.get(LANG_ENV_NAME, "").strip().lower()
    if forced in ("ja", "en"):
        return forced
    return pick_language(preferred_languages())


LANG = detect_language()


def tr(ja: str, en: str) -> str:
    """いまの言語の文言。"""
    return en if LANG == "en" else ja


ERROR = tr("[エラー]", "[Error]")
WARNING = tr("[警告]", "[Warning]")
PERIOD = tr("。", ".")
SEP = tr("。", ". ")
COMMA = tr("、", ", ")


def sentence(text: str) -> str:
    """文を閉じる（句点が無ければ付ける）。空ならそのまま。"""
    text = text.rstrip()
    if not text:
        return text
    if LANG == "en":
        return text if text.endswith((".", "!", "?")) else text + "."
    return text if text.endswith("。") else text + "。"


def sentences(parts) -> str:
    """文を並べて 1 つの読み上げにする（空の文は飛ばす。最後も閉じる）。"""
    cleaned = [p.rstrip().rstrip("。.").rstrip() for p in parts if p and p.strip()]
    return sentence(SEP.join(cleaned)) if cleaned else ""

# --------------------------------------------------------------------------
# Logic の部品の名前（2026-11-02 から。Logic の表示が英語でも日本語でも動く）
# --------------------------------------------------------------------------
#   部品の名前（AXDescription・AXTitle・AXHelp・メニューの項目）は **Logic の表示の言語**で変わる
#   （この補助の言語とは別。Mac は日本語のまま Logic だけ英語にしている人もいる）。名前は (英語, 日本語) の組で持ち、
#   **どちらでも当たるようにする**（Logic の言語を先に調べない。調べ違えると何も見つからなくなる）。
#   日本語は Logic Pro 12.3 を日本語の表示にして実測した。言語で変わらない部品（Quick Sampler の名前の表示、
#   「開く」ダイアログの AXIdentifier、再生位置の bar・beat・tick など）は 1 つのまま


def is_named(text: Optional[str], names: Sequence[str]) -> bool:
    """部品の名前が、組のどれかと同じか。"""
    return (text or "") in names


def starts_named(text: Optional[str], names: Sequence[str]) -> bool:
    """部品の名前が、組のどれかで始まるか（AXHelp の頭、末尾に「…」の付くメニューの項目）。"""
    return (text or "").startswith(tuple(names))


MAIN_TITLE_SUFFIXES = ("- Tracks", "- トラック")        # 主画面の題（「名称未設定 - トラック」）
PART_CONTROL_BAR = ("Control Bar", "コントロールバー")
PART_INSPECTOR = ("Inspector", "インスペクタ")
PART_TRACKS = ("Tracks", "トラック")
TRACKS_HEADER = ("Tracks header", "トラックヘッダ")
TRACKS_CONTENTS = ("Tracks contents", "トラックコンテンツ")
# コントロールバーの表示（読むときの名前 → 部品の説明）
LCD_NAMES = {
    "Tempo": ("Tempo", "テンポ"),
    "Key Signature": ("Key Signature", "キー"),
    "Time Signature": ("Time Signature", "拍子"),
    "Sample Rate": ("Sample Rate", "サンプルレート"),
}
DIVISION_HELP = ("Division", "ディビジョン")
PLAYHEAD_NAMES = ("Playhead Position", "再生ヘッドの位置")
PLAYHEAD_BARS_HELP = ("Playhead Position (Bars)", "再生ヘッドの位置（小節）")
# 再生位置の 4 つの欄（読むときの名前 → 部品の説明）。日本語の表示でも division だけが訳される
PLAYHEAD_PART_NAMES = {"bar": ("bar",), "beat": ("beat",), "division": ("division", "ディビジョン"), "tick": ("tick",)}
PLAY_NAMES = ("Play", "再生")
STRIP_HELP = {
    "Left": ("Left inspector channel strip", "インスペクタの左チャンネルストリップ"),
    "Right": ("Right inspector channel strip", "インスペクタの右チャンネルストリップ"),
}
SLOT_PARTS = (["bypass", "open", "list"], ["バイパス", "開く", "リスト"])   # 入っている音源スロットの 3 つの部品
INSTRUMENT_SLOT_HELP = ("Instrument slot", "音源スロット")
INPUT_SLOT_HELP = ("Input slot", "入力スロット")
RECORD_NAMES = ("record", "録音")
CHANNEL_MODE_NAMES = ("channel mode", "チャンネルモード")
REGION_LABELS = ("Region:", "リージョン:")       # インスペクタのリージョンの欄の見出し
CLOSE_NAMES = ("close", "閉じる")
VIEW_BUTTON_NAMES = ("view", "表示")            # プラグイン画面の「表示」のボタン
VIEW_CONTROLS = ("Controls", "コントロール")
VIEW_EDITOR = ("Editor", "エディタ")
STEREO_ITEMS = ("Stereo", "ステレオ")

# --------------------------------------------------------------------------
# 画面の寸法（2026-09-24、Logic Pro 12.3、表示サイズ 100% で実測）
# --------------------------------------------------------------------------

PLUGIN_LABEL = "Quick Sampler"     # プラグイン画面の中の名前表示
EDITOR_WIDTH_100 = 1028            # エディタ表示の窓の幅
CONTENT_OFFSET = (2, 91)           # 窓の左上から、Quick Sampler の描画域の左上まで（倍率に依らない）
POPUP_CENTER_100 = (561, 21)       # 描画域の左上から欄の中心まで（倍率で伸び縮みする）
POPUP_WIDTH_100 = 294              # 欄の幅
NO_FILE_TEXTS = ("No audio file loaded",)
AUDIO_EXTENSIONS = (".wav", ".aif", ".aiff", ".caf", ".mp3", ".m4a", ".aac", ".flac", ".ogg")

# Flex の群（2026-09-25 に実測）。波形表示の右下に 3 つ並ぶ:
#   Flex（名前なし）→ Follow Tempo → Speed（⅛〜8）
# 窓 (596, 258) 1028x796 のとき Flex (1337, 651) 28x21、Follow Tempo (1381, 651) 130x22、
# Speed (1579, 650) 35x22。**名前があるのは Follow Tempo だけ**なので、それを目印に探し、
# 同じ群の中の名前の無いチェックボックスを Flex とみなす
FOLLOW_TEMPO_LABEL = "Follow Tempo"
FOLLOW_CENTER_100 = (848, 313)     # 描画域の左上から Follow Tempo の中心まで
FLEX_AREA_100 = (700, 290, 1022, 336)   # 粗く走査する範囲（描画域の左上から x0, y0, x1, y1）

LOAD_ITEM_PREFIX = "Load Audio File"   # メニューの項目（末尾は「…」）
OPEN_PANEL_ID = "open-panel"            # 「開く」ダイアログの AXIdentifier
GOTO_SHEET_ID = "GoToWindow"            # 「フォルダへ移動」の AXIdentifier
OK_BUTTON_ID = "OKButton"
CANCEL_BUTTON_ID = "CancelButton"
WHERE_TITLES = ("Where:", "場所:")       # 「開く」ダイアログの場所の欄（AXIdentifier は「where popup」）
WHERE_POPUP_ID = "where popup"
COLUMN_WORDS = ("Column", "カラム")      # 表示の切り替えメニューの列表示の項目
MODE_ORIGINAL, MODE_OPTIMIZED = "Original", "Optimized"   # 「開く」ダイアログ下部の選択肢の頭の語

KEY_G, KEY_RETURN, KEY_ESCAPE = 5, 36, 53

ERR_CANNOT_COMPLETE = -25204       # メニューが開いている間は押下の返事がこれになる（開いてはいる）

(EXIT_OK, EXIT_NO_WINDOW, EXIT_NO_LOGIC, EXIT_NO_POPUP, EXIT_NOT_OPENED, EXIT_NO_PERMISSION,
 EXIT_NO_FLEX, EXIT_FOLLOW_NEEDS_FLEX, EXIT_NO_FILE, EXIT_NOT_FRONT, EXIT_NO_LOAD_ITEM,
 EXIT_NO_PANEL, EXIT_GOTO_FAILED, EXIT_OPEN_FAILED, EXIT_NOT_LOADED, EXIT_PRESS_FAILED,
 EXIT_NO_DMD, EXIT_NO_PAD, EXIT_NO_INSPECTOR, EXIT_OTHER_INSTRUMENT, EXIT_INSERT_FAILED,
 EXIT_PAD_WINDOW, EXIT_NO_MAIN, EXIT_NO_TRACK, EXIT_NOT_AUDIO_TRACK, EXIT_NOT_PLACED,
 EXIT_LOGIC_SLOW) = range(27)

MESSAGES = {
    EXIT_NO_WINDOW: tr("Quick Sampler の画面が開いていません。ハイフンで開いてから、もう一度押してください",
                       "The Quick Sampler window is not open. Open it with the Hyphen key, then press again"),
    EXIT_NO_LOGIC: tr("Logic Pro が起動していません", "Logic Pro is not running"),
    EXIT_NO_POPUP: tr("Sample name の欄が見つかりません", "The Sample name field was not found"),
    EXIT_NOT_OPENED: tr("Sample name の欄を押しましたが、メニューが開きませんでした",
                        "Pressed the Sample name field, but the menu did not open"),
    EXIT_NO_PERMISSION: tr("アクセシビリティの権限がありません", "Accessibility permission is missing"),
    EXIT_NO_FLEX: tr("Flex のボタンが見つかりません", "The Flex button was not found"),
    EXIT_FOLLOW_NEEDS_FLEX: tr("Follow Tempo は Flex を入れたときだけ使えます。先に Flex を入れてください",
                               "Follow Tempo works only when Flex is on. Turn Flex on first"),
    EXIT_NO_FILE: tr("読み込むファイルが見つかりません", "The file to load was not found"),
    EXIT_NOT_FRONT: tr("Logic Pro を前に出せませんでした", "Could not bring Logic Pro to the front"),
    EXIT_NO_LOAD_ITEM: tr("メニューに Load Audio File が見つかりません", "Load Audio File was not found in the menu"),
    EXIT_NO_PANEL: tr("ファイルを開くダイアログが出ませんでした", "The Open dialog did not appear"),
    EXIT_GOTO_FAILED: tr("開くダイアログで、ファイルの場所へ移動できませんでした",
                         "Could not go to the file's folder in the Open dialog"),
    EXIT_OPEN_FAILED: tr("開くダイアログで、Open を押しても閉じませんでした",
                         "The Open dialog did not close after pressing Open"),
    EXIT_NOT_LOADED: tr("読み込んだはずのファイルが、Quick Sampler に出ていません",
                        "The loaded file does not appear in Quick Sampler"),
    EXIT_PRESS_FAILED: tr("ボタンを押しましたが、切り替わりませんでした", "Pressed the button, but it did not switch"),
    EXIT_NO_DMD: tr("Drum Machine Designer の画面が開いていません。DMD のトラックでハイフンを押して開いてください",
                    "The Drum Machine Designer window is not open. Open it with the Hyphen key on the DMD track"),
    EXIT_NO_PAD: tr("そのパッドが見つかりません", "That pad was not found"),
    EXIT_NO_INSPECTOR: tr("インスペクタが表示されていません。パッドのチャンネルを操作するので、インスペクタを表示してください",
                          "The inspector is not shown. Show the inspector, because the pad's channel is used"),
    EXIT_OTHER_INSTRUMENT: tr("このパッドには Quick Sampler 以外の音源が入っているので、読み込みませんでした",
                              "This pad has an instrument other than Quick Sampler, so nothing was loaded"),
    EXIT_INSERT_FAILED: tr("空のパッドに Quick Sampler を入れられませんでした", "Could not add Quick Sampler to the empty pad"),
    EXIT_PAD_WINDOW: tr("パッドの Quick Sampler の画面を開けませんでした", "Could not open the pad's Quick Sampler window"),
    EXIT_NO_MAIN: tr("Logic のプロジェクトの画面（トラックの並ぶ主画面）が見つかりません",
                     "Logic's main window (with the tracks) was not found"),
    EXIT_NO_TRACK: tr("トラックが選ばれていません。Logic で貼り付け先のオーディオトラックを選んでください",
                      "No track is selected. In Logic, select the audio track to paste on"),
    EXIT_NOT_AUDIO_TRACK: tr("選んでいるトラックはオーディオトラックではありません。Logic でオーディオトラックを選んでから、"
                             "もう一度貼ってください。新しく作るなら、Logic でオプション+コマンド+A",
                             "The selected track is not an audio track. Select an audio track in Logic, then paste again. "
                             "To create one, press Option-Command-A in Logic"),
    EXIT_NOT_PLACED: tr("貼ったはずのリージョンが、トラックに見つかりません", "The pasted region was not found on the track"),
    EXIT_LOGIC_SLOW: tr("Logic がトラックの選択に応えません。少し待ってから、もう一度押してください",
                        "Logic does not respond to the track selection. Wait a moment, then press again"),
}
SWITCHED_MESSAGE = tr("エディタ表示に切り替えました", "Switched to the editor view")

# Drum Machine Designer（2026-09-25、Logic Pro 12.3、表示サイズ 100% で実測）
#   窓は 1028x732。上半分に 4 列 × 4 行のパッド（左下がパッド 1、右へ 2・3・4、1 段上が 5〜8）。
#   48 パッドが 16 個ずつ 3 ページ。ページは下の 3 つの点（AX のボタン、押せる）で切り替える。
#   **パッドの位置に AX の部品は無い**（座標で問い合わせると窓が返る）。LM もパッドを座標のクリックで選ぶ
#   （LM の Pad1 は窓の左上から (133, 299)、Pad9 は (133, 161)）。ここも同じ値を使う
DMD_LABEL = "Drum Machine Designer"
DMD_WIDTH_100 = 1028
PAD_CENTER_100 = (133, 299)        # パッド 1 の中心（窓の左上から）
PAD_STEP_100 = (255, 69)           # 右隣・上隣までの距離
PADS_PER_PAGE, PAD_PAGES, PAD_COLUMNS = 16, 3, 4
PAD_COUNT = PADS_PER_PAGE * PAD_PAGES
# パッドの見出し（「C1 - KICK 1」「G♯1 - PEDAL HIHAT」）。音名は ♯・# と ♭・b のどちらもありうる
PAD_LABEL_RE = re.compile(r"^([A-G](?:♯|#|♭|b)?-?\d)\s+-\s+(.+)$")
EMPTY_PAD_TEXT = "Empty Pad"


# --------------------------------------------------------------------------
# 座標の計算（AX を使わない。テストしやすいように分けてある）
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    x: float
    y: float
    w: float
    h: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


def scale_of(window: Frame) -> float:
    """表示サイズの倍率。エディタ表示の窓の幅から求める。"""
    return max(0.25, window.w / EDITOR_WIDTH_100)


def _origin(window: Frame) -> Tuple[float, float]:
    return window.x + CONTENT_OFFSET[0], window.y + CONTENT_OFFSET[1]


def probe_points(window: Frame) -> List[Tuple[float, float]]:
    """欄を探す座標の候補。まず見込みの中心、次にその周り、最後に上部を粗く走査する。"""
    s = scale_of(window)
    ox, oy = _origin(window)
    cx, cy = ox + POPUP_CENTER_100[0] * s, oy + POPUP_CENTER_100[1] * s
    points = [(cx, cy)]
    for dx in (-60, 60, -110, 110):
        points.append((cx + dx * s, cy))
    for dy in (-8, 8):
        points.append((cx, cy + dy * s))
    # 窓の見出しを隠している場合などに備えて、上部を粗く走査する
    y = window.y + 24
    while y < window.y + min(window.h, 260 * s):
        x = window.x + window.w * 0.30
        while x < window.x + window.w * 0.75:
            points.append((x, y))
            x += 40 * s
        y += 12
    return points


def follow_probe_points(window: Frame) -> List[Tuple[float, float]]:
    """Follow Tempo を探す座標の候補。見込みの中心とその周り、次に波形表示の右下を粗く走査する。"""
    s = scale_of(window)
    ox, oy = _origin(window)
    cx, cy = ox + FOLLOW_CENTER_100[0] * s, oy + FOLLOW_CENTER_100[1] * s
    points = [(cx, cy)]
    for dx in (-40, 40, -20, 20):
        points.append((cx + dx * s, cy))
    for dy in (-6, 6):
        points.append((cx, cy + dy * s))
    x0, y0, x1, y1 = FLEX_AREA_100
    y = oy + y0 * s
    while y <= oy + y1 * s:
        x = ox + x0 * s
        while x <= ox + x1 * s:
            if window.contains(x, y):
                points.append((x, y))
            x += 16 * s
        y += 8 * s
    return points


def looks_like_sample_popup(role: Optional[str], value: Optional[str], width: float, scale: float) -> bool:
    """座標で見つけた要素が Sample name の欄かどうか。"""
    if role != "AXPopUpButton":
        return False
    text = (value or "").strip()
    if text in NO_FILE_TEXTS:
        return True
    if text.lower().endswith(AUDIO_EXTENSIONS):
        return True
    # 表示言語が違う・拡張子の無い名前（録音したもの等）のときは、幅で見分ける。
    # 近くにある Snap の欄は幅が半分以下なので紛れない
    return width >= POPUP_WIDTH_100 * scale * 0.8


def pick_window(candidates: Sequence[object], focused: Optional[object],
                same: Callable[[object, object], bool]) -> Optional[object]:
    """Quick Sampler の画面が複数あるとき、焦点のある窓を優先する。無ければ最前面（先頭）。"""
    if not candidates:
        return None
    if focused is not None:
        for c in candidates:
            if same(c, focused):
                return c
    return candidates[0]


def pick_flex_siblings(entries: Sequence[Tuple[Optional[str], str, object]], follow: object,
                       same: Callable[[object, object], bool]) -> Tuple[Optional[object], Optional[object]]:
    """Follow Tempo と同じ群の要素から、Flex（名前の無いチェックボックス）と Speed（ポップアップ）を選ぶ。

    entries は (役割, 説明, 要素)。**名前の付いたチェックボックスは Flex とみなさない**
    （Logic の更新で群に別のボタンが増えても、取り違えないようにする）。
    """
    flex = next((obj for role, desc, obj in entries
                 if role == "AXCheckBox" and not desc.strip() and not same(obj, follow)), None)
    speed = next((obj for role, _desc, obj in entries if role == "AXPopUpButton"), None)
    return flex, speed


def norm_name(text: Optional[str]) -> str:
    """名前の比較用。**Unicode の正規化をそろえる**（macOS のファイル名は分解形で返ることがある）。"""
    return unicodedata.normalize("NFC", text or "").strip()


def entry_candidates(name: str, display: Optional[str] = None) -> Tuple[str, ...]:
    """「開く」ダイアログの行と照らし合わせる名前の候補（優先順）。

    実名、表示名（「Music」が「ミュージック」になるなど、Logic の表示言語による）、
    拡張子を隠す設定のときの名前。
    """
    found = [norm_name(name)]
    if display:
        found.append(norm_name(display))
    stem = Path(name).stem
    if stem and stem != name:
        found.append(norm_name(stem))
    return tuple(dict.fromkeys(found))


def match_entry(texts: Sequence[Optional[str]], candidates: Sequence[str]) -> Optional[int]:
    """行の文字の並びから、候補に一致する行の位置を返す。**候補の順に探す**（実名が先）。"""
    normalized = [norm_name(t) for t in texts]
    for cand in candidates:
        for i, text in enumerate(normalized):
            if text and text == cand:
                return i
    return None


def pad_position(index: int) -> Tuple[int, int, int]:
    """パッド番号（1〜48）を (ページ 0〜2, 段 0〜3（0 が下）, 列 0〜3) にする。"""
    if not 1 <= index <= PAD_COUNT:
        raise ValueError(f"パッドは 1〜{PAD_COUNT}: {index}")
    i = index - 1
    within = i % PADS_PER_PAGE
    return i // PADS_PER_PAGE, within // PAD_COLUMNS, within % PAD_COLUMNS


def pad_point(window: Frame, index: int) -> Tuple[float, float]:
    """パッドの中心の画面座標。**そのパッドのページが表示されている前提**（ページは先に切り替える）。"""
    _, row, col = pad_position(index)
    s = max(0.25, window.w / DMD_WIDTH_100)
    return (window.x + (PAD_CENTER_100[0] + PAD_STEP_100[0] * col) * s,
            window.y + (PAD_CENTER_100[1] - PAD_STEP_100[1] * row) * s)


def parse_pad_label(text: Optional[str]) -> Optional[Tuple[str, str]]:
    """パッドの見出し「C1 - KICK 1」を (音名, 名前) にする。見出しでなければ None。"""
    m = PAD_LABEL_RE.match((text or "").strip())
    return (m.group(1), m.group(2).strip()) if m else None


PAD_NOTE_RE = re.compile(r"^[A-G](?:♯|#|♭|b)?-?\d$")


def pad_label_spots(texts: Sequence[Tuple[float, float, str]]) -> List[Tuple[float, float, str, str, int]]:
    """DMD の窓の文字（x, y, 文字）から、パッドの見出しを (x, y, 音名, 名前, texts での位置) で返す。

    **チャンネルだけあって音源の無いパッドを選ぶと、見出しの代わりに「M S Input: D1 Output: D1」が出る**
    （2026-11-02 実測。空のパッドを選んで Quick Sampler を入れる前に止まると、この形で残る）。見出しの位置
    （ほかの見出しと同じ列と段）にある音名だけの文字を、名前の分からない見出し（名前は空）として扱う。
    """
    found = []
    for i, (x, y, text) in enumerate(texts):
        parsed = parse_pad_label(text)
        if parsed:
            found.append((x, y, parsed[0], parsed[1], i))
    xs, ys = {round(x) for x, *_ in found}, {round(y) for _, y, *_ in found}
    taken = {(round(x), round(y)) for x, y, *_ in found}
    for i, (x, y, text) in enumerate(texts):
        spot = (round(x), round(y))
        if PAD_NOTE_RE.match(text.strip()) and spot[0] in xs and spot[1] in ys and spot not in taken:
            found.append((x, y, text.strip(), "", i))
            taken.add(spot)
    return found


def pad_indexes(labels: Sequence[Tuple[float, float, str]]) -> List[Optional[int]]:
    """パッドの見出し（x, y, 文字）それぞれのパッド番号を、並びの順に返す（見出しでなければ None）。

    3 ページは横に並んでいて、見出しの x は 12 列、y は 4 段になる（スクロールしても並びは同じ）。
    **位置の順位で決めるので、スクロールの量に依らない。** 読めない見出しがあっても、読めた範囲で決める。
    """
    keyed = [(round(x), round(y)) if parse_pad_label(t) else None for x, y, t in labels]
    xs = sorted({k[0] for k in keyed if k})
    ys = sorted({k[1] for k in keyed if k}, reverse=True)      # 下の段が先
    out: List[Optional[int]] = []
    for k in keyed:
        if k is None:
            out.append(None)
            continue
        page, col = divmod(xs.index(k[0]), PAD_COLUMNS)
        index = page * PADS_PER_PAGE + ys.index(k[1]) * PAD_COLUMNS + col + 1
        out.append(index if 1 <= index <= PAD_COUNT else None)
    return out


def assign_pads(labels: Sequence[Tuple[float, float, str]]) -> Dict[int, Tuple[str, str]]:
    """パッドの見出しを、パッド番号 → (音名, 名前) にする。"""
    pads: Dict[int, Tuple[str, str]] = {}
    for (x, y, text), index in zip(labels, pad_indexes(labels)):
        if index is not None:
            pads[index] = parse_pad_label(text)
    return pads


def loaded_name_matches(value: Optional[str], filename: str) -> bool:
    """Sample name の欄の値が、読み込んだファイルのものか。

    サンプルレートが違うと Logic は変換した複製（`名前-44k.wav`）を読み込むので、
    元の名前で始まっていれば同じとみなす。
    """
    text = (value or "").strip()
    if not text or text in NO_FILE_TEXTS:
        return False
    if text == filename:
        return True
    stem = Path(filename).stem
    return text.startswith(stem + "-") and text.lower().endswith(AUDIO_EXTENSIONS)


@dataclass
class FlexState:
    flex: bool
    follow: bool
    follow_enabled: bool
    speed: str = ""


def flex_message(state: FlexState) -> str:
    """Flex を切り替えたあとに読み上げる 1 行。"""
    if not state.flex:
        return tr("Flex を切りました。音程と一緒に速さも変わります", "Flex is off. The speed changes with the pitch")
    if state.follow:
        return tr("Flex を入れました。音程を変えても速さは変わりません。"
                  "Follow Tempo も入っているので、プロジェクトのテンポに合わせて鳴ります",
                  "Flex is on. Changing the pitch does not change the speed. "
                  "Follow Tempo is also on, so it plays at the project tempo")
    return tr("Flex を入れました。音程を変えても速さは変わりません", "Flex is on. Changing the pitch does not change the speed")


def follow_message(state: FlexState) -> str:
    """Follow Tempo を切り替えたあとに読み上げる 1 行。"""
    if state.follow:
        return tr("Follow Tempo を入れました。プロジェクトのテンポに合わせて鳴ります",
                  "Follow Tempo is on. It plays at the project tempo")
    return tr("Follow Tempo を切りました。素材の元の速さで鳴ります", "Follow Tempo is off. It plays at the sample's own speed")


def state_phrase(state: Optional[FlexState]) -> str:
    """状態の読み上げ用の言い回し（「Flex は入り、Follow Tempo は切り」など）。"""
    if state is None:
        return tr("Flex の状態は分かりません", "Flex state unknown")
    if not state.flex:
        return tr("Flex は切り", "Flex off")
    return tr(f"Flex は入り、Follow Tempo は{'入り' if state.follow else '切り'}",
              f"Flex on, Follow Tempo {'on' if state.follow else 'off'}")


# --------------------------------------------------------------------------
# オーディオトラックに貼る: 表示と位置の読み方（AX を使わない。2026-09-25、Logic Pro 12.3 で実測）
# --------------------------------------------------------------------------
#   主画面のコントロールバーの表示: Tempo「120.0」、Key Signature「C Major」、Time Signature「4/4」、
#   Sample Rate「44.1 kHz」、Division「/16」。再生位置は bar・beat・division・tick の 4 つの欄（どれも 1 から）。
#   トラックの見出しの説明は「Track 2 “Audio 1”」。貼ったリージョンの AXHelp は
#   「Region starts at 1 bar  and ends at 3 bars , Audio region. …」、長さ（AXSizeDescription）は「2 bars 」
#   「1 bar 3 beats 3 divisions 176 ticks 」のように出る

# 日本語の表示では「トラック2の“Audio 1”」
TRACK_DESC_RES = (re.compile(r"^Track (\d+) “(.*)”$"), re.compile(r"^トラック(\d+)の“(.*)”$"))
TICKS_PER_WHOLE = 3840        # 全音符のティック数（Logic は 4 分音符が 960）
SNAP_MODES = ("off", "bar")      # off = Logic の決まり（再生位置のある小節の頭）、bar = いちばん近い小節の頭
# 貼ったあとの再生位置。end = 貼ったリージョンの終わり（次に貼る位置）、start = 貼ったリージョンの頭、stay = 動かさない
PLAYHEAD_MODES = ("end", "start", "stay")
Position = Tuple[int, int, int, int]    # 小節・拍・分割・ティック（どれも 1 から）
# 日本語の表示では、2 以上が「3 小節」「2拍」「3ディビジョン」「176ティック」になり、1 は英語のまま（「1 bar」）。
# リージョンの説明は「リージョンの開始位置は1 bar 、終了位置は3 小節 です, …」（2026-11-02 実測）
_WORD_RE = re.compile(r"(-?\d+)\s*(bar|beat|division|tick|小節|拍|ディビジョン|ティック)s?", re.IGNORECASE)
_UNIT_WORDS = {"小節": "bar", "拍": "beat", "ディビジョン": "division", "ティック": "tick"}
_REGION_SPAN_RES = (re.compile(r"starts at (.*?) and ends at (.*?)(?:,|$)"),
                    re.compile(r"開始位置は(.*?)、終了位置は(.*?)です"))


def _unit_counts(text: Optional[str]) -> Dict[str, int]:
    """「1 bar 3 beats」「2 小節 3拍」→ {"bar": …, "beat": …}（単位は英語の名前にそろえる）。"""
    return {_UNIT_WORDS.get(unit, unit.lower()): int(n) for n, unit in _WORD_RE.findall(text or "")}


def parse_track_desc(text: Optional[str]) -> Optional[Tuple[int, str]]:
    """「Track 2 “Audio 1”」（日本語の表示では「トラック2の“Audio 1”」）→ (2, "Audio 1")。"""
    for pattern in TRACK_DESC_RES:
        m = pattern.match((text or "").strip())
        if m:
            return int(m.group(1)), m.group(2)
    return None


def parse_number(text) -> Optional[float]:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def parse_meter(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """「4/4」→ (4, 4)。"""
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", text or "")
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        return None
    return int(m.group(1)), int(m.group(2))


def parse_rate(text: Optional[str]) -> Optional[int]:
    """「44.1 kHz」→ 44100。"""
    m = re.match(r"^\s*([\d.]+)\s*kHz\s*$", text or "", re.IGNORECASE)
    return int(round(float(m.group(1)) * 1000)) if m else None


def parse_division(text: Optional[str]) -> Optional[int]:
    """「/16」→ 16。"""
    m = re.match(r"^\s*/\s*(\d+)\s*$", text or "")
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None


def _grid(meter: Tuple[int, int], division: int) -> Tuple[int, int, int]:
    """(1 拍のティック, 1 小節の拍数, 1 分割のティック)。分割が拍より粗いときは拍にそろえる。"""
    beat = TICKS_PER_WHOLE // meter[1]
    return beat, meter[0], min(TICKS_PER_WHOLE // division, beat)


def position_to_ticks(pos: Position, meter: Tuple[int, int], division: int) -> int:
    beat, beats, div = _grid(meter, division)
    bar, b, d, t = pos
    return (bar - 1) * beat * beats + (b - 1) * beat + (d - 1) * div + (t - 1)


def ticks_to_position(ticks: int, meter: Tuple[int, int], division: int) -> Position:
    beat, beats, div = _grid(meter, division)
    bar, rest = divmod(ticks, beat * beats)
    b, rest = divmod(rest, beat)
    d, t = divmod(rest, div)
    return bar + 1, b + 1, d + 1, t + 1


def snap_position(pos: Position, meter: Tuple[int, int], division: int, mode: str) -> Position:
    """いちばん近い拍（mode="beat"）か小節（"bar"）の頭。"off" ならそのまま。ちょうど半分なら先へ。"""
    if mode not in ("beat", "bar"):
        return pos
    beat, beats, _ = _grid(meter, division)
    unit = beat * (beats if mode == "bar" else 1)
    ticks = position_to_ticks(pos, meter, division)
    return ticks_to_position(((ticks + unit // 2) // unit) * unit, meter, division)


def parse_position_words(text: Optional[str]) -> Optional[Position]:
    """「3 bars 2 beats」→ (3, 2, 1, 1)。書かれていない単位は 1（その単位の頭）。"""
    found = _unit_counts(text)
    if "bar" not in found:
        return None
    return found["bar"], found.get("beat", 1), found.get("division", 1), found.get("tick", 1)


def parse_length_words(text: Optional[str]) -> Tuple[int, int, int, int]:
    """「1 bar 3 beats 3 divisions 176 ticks」→ (1, 3, 3, 176)。書かれていない単位は 0。"""
    found = _unit_counts(text)
    return found.get("bar", 0), found.get("beat", 0), found.get("division", 0), found.get("tick", 0)


def parse_region_help(text: Optional[str]) -> Optional[Tuple[Position, Position]]:
    """リージョンの AXHelp から (始まり, 終わり)。"""
    m = next((m for m in (pattern.search(text or "") for pattern in _REGION_SPAN_RES) if m), None)
    if not m:
        return None
    start, end = parse_position_words(m.group(1)), parse_position_words(m.group(2))
    return (start, end) if start and end else None


def position_phrase(pos: Optional[Position]) -> str:
    """(3, 1, 1, 1) →「3 小節目の頭」、(3, 2, 1, 1) →「3 小節目の 2 拍目」、半端なら「…の途中」。"""
    if pos is None:
        return tr("分からない位置", "an unknown position")
    bar, beat, div, tick = pos
    if (beat, div, tick) == (1, 1, 1):
        return tr(f"{bar} 小節目の頭", f"the start of bar {bar}")
    if (div, tick) == (1, 1):
        return tr(f"{bar} 小節目の {beat} 拍目", f"beat {beat} of bar {bar}")
    return tr(f"{bar} 小節目の {beat} 拍目の途中", f"partway through beat {beat} of bar {bar}")


def length_phrase(length: Tuple[int, int, int, int]) -> str:
    """(2, 0, 0, 0) →「2 小節」、(1, 3, 3, 176) →「1 小節と 3 拍と少し」、拍より短ければ「1 拍に満たない」。"""
    bars, beats, divs, ticks = length
    if LANG == "en":
        parts = [f"{bars} bar{'s' if bars != 1 else ''}"] if bars else []
        if beats:
            parts.append(f"{beats} beat{'s' if beats != 1 else ''}")
        text = " and ".join(parts)
        if divs or ticks:
            text = f"a little over {text}" if text else "less than 1 beat"
        return text or "0"
    parts = [f"{bars} 小節"] if bars else []
    if beats:
        parts.append(f"{beats} 拍")
    text = "と ".join(parts)
    if divs or ticks:
        text = f"{text}と少し" if text else "1 拍に満たない"
    return text or "0"


KEY_RE = re.compile(r"^\s*([A-G][#♯b♭]?)\s*(major|minor|メジャー|マイナー)\s*$", re.IGNORECASE)


def key_phrase(text: Optional[str]) -> str:
    """Logic の Key Signature の表示を読み上げの言い方にする（「C Major」→「C メジャー」。英語の画面では「C major」）。

    日本語の表示の Logic は「Cメジャー」と出す（間を空けない）。どちらの表示でも同じ言い方にそろえる。
    """
    m = KEY_RE.match(text or "")
    if not m:
        return (text or "").strip()
    minor = m.group(2).lower() in ("minor", "マイナー")
    return f"{m.group(1)} " + (tr("マイナー", "minor") if minor else tr("メジャー", "major"))


# チャンネルの「Mono」「Stereo」（日本語の表示では「モノラル」「ステレオ」）
CHANNEL_WORDS = {"Mono": tr("モノラル", "mono"), "Stereo": tr("ステレオ", "stereo"),
                 "モノラル": tr("モノラル", "mono"), "ステレオ": tr("ステレオ", "stereo")}


def paste_position(pos: Optional[Position], meter: Optional[Tuple[int, int]], division: Optional[int],
                   snap: str = "off") -> Optional[Position]:
    """貼ると置かれる位置。**Logic は再生位置のある小節の頭に置く**（snap="bar" なら先にいちばん近い小節へ動かす）。"""
    if pos is None:
        return None
    if snap == "bar" and meter and division:
        return snap_position(pos, meter, division, "bar")
    return pos[0], 1, 1, 1


def advance_goal(end: Optional[Position]) -> Optional[Position]:
    """貼ったあとに再生位置を進める先。リージョンの終わりが小節の頭ならそこ、途中なら次の小節の頭
    （Logic は再生位置のある小節の頭に置くので、途中に進めると次に貼ったものが前のものに重なる）。"""
    if end is None:
        return None
    return end if end[1:] == (1, 1, 1) else (end[0] + 1, 1, 1, 1)


def playhead_goal(mode: str, start: Optional[Position], end: Optional[Position]) -> Optional[Position]:
    """貼ったあとに再生位置を置く先（mode は PLAYHEAD_MODES）。動かさないとき・分からないときは None。

    end は次に貼る位置（advance_goal）。start は貼ったリージョンの頭（すぐ再生して聴ける）。
    """
    if mode == "end":
        return advance_goal(end)
    if mode == "start":
        return start
    return None


def occupancy_phrase(regions: Sequence[Tuple[str, str]], at: Optional[Position],
                     meter: Optional[Tuple[int, int]], division: Optional[int]) -> str:
    """貼る位置に何があるか（「5 小節目は空いています」「5 小節目には X があります（3 小節目の頭から 7 小節目の頭まで）」）。

    regions は (名前, AXHelp) の並び。**貼る前に重なりに気づけるようにする**（画面を見ずには分からない）。
    """
    if at is None or not meter or not division:
        return ""
    here = position_to_ticks(at, meter, division)
    for name, help_ in regions:
        span = parse_region_help(help_)
        if span is None:
            continue
        start, end = span
        if position_to_ticks(start, meter, division) <= here < position_to_ticks(end, meter, division):
            return tr(f"{at[0]} 小節目には {name} があります（{position_phrase(start)}から {position_phrase(end)}まで）",
                      f"Bar {at[0]} has {name} (from {position_phrase(start)} to {position_phrase(end)})")
    return tr(f"{at[0]} 小節目は空いています", f"Bar {at[0]} is empty")


# --------------------------------------------------------------------------
# AX（ここから下は実機でしか動かない）
# --------------------------------------------------------------------------

def _ax():
    import ApplicationServices as AS  # 遅延読み込み（テストでは読まない）
    return AS


def attr(el, name):
    if el is None:
        return None
    AS = _ax()
    err, value = AS.AXUIElementCopyAttributeValue(el, name, None)
    return value if err == 0 else None


def frame_of(el) -> Optional[Frame]:
    AS = _ax()
    pos, size = attr(el, "AXPosition"), attr(el, "AXSize")
    if pos is None or size is None:
        return None
    ok1, p = AS.AXValueGetValue(pos, AS.kAXValueCGPointType, None)
    ok2, z = AS.AXValueGetValue(size, AS.kAXValueCGSizeType, None)
    if not (ok1 and ok2):
        return None
    return Frame(p.x, p.y, z.width, z.height)


def same_element(a, b) -> bool:
    from CoreFoundation import CFEqual
    return a is not None and b is not None and bool(CFEqual(a, b))


def belongs_to(el, win) -> bool:
    """要素がその窓のものか。孤立要素は AXWindow を持たないことがあるので、親もたどる。"""
    if same_element(attr(el, "AXWindow"), win):
        return True
    cur = el
    for _ in range(8):
        cur = attr(cur, "AXParent")
        if cur is None:
            return False
        if same_element(cur, win):
            return True
    return False


def logic_pid() -> Optional[int]:
    out = subprocess.run(["pgrep", "-x", "Logic Pro"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def is_qs_window(win) -> bool:
    for child in attr(win, "AXChildren") or []:
        if attr(child, "AXRole") == "AXStaticText" and (attr(child, "AXValue") or "") == PLUGIN_LABEL:
            return True
    return False


def view_button(win):
    for child in attr(win, "AXChildren") or []:
        if attr(child, "AXRole") == "AXMenuButton" and is_named(attr(child, "AXDescription"), VIEW_BUTTON_NAMES):
            return child
    return None


def is_controls_view(win) -> bool:
    btn = view_button(win)
    return btn is not None and is_named(attr(btn, "AXTitle"), VIEW_CONTROLS)


def press_async(el, timeout: float = 0.5) -> int:
    """押す。メニューを開く押下は返事が遅れるので、待ち時間を短くして返す。"""
    AS = _ax()
    AS.AXUIElementSetMessagingTimeout(el, timeout)
    return AS.AXUIElementPerformAction(el, "AXPress")


def wait_for(predicate: Callable[[], object], timeout: float, interval: float = 0.05):
    """predicate が真になるまで待つ。真になった値を返す（時間切れなら None）。"""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() >= deadline:
            return None
        time.sleep(interval)


def open_menu_items(el, wait: float = 1.5):
    """要素の下に開いたメニューの項目を返す。開かなければ空。"""
    def items():
        for child in attr(el, "AXChildren") or []:
            if attr(child, "AXRole") == "AXMenu":
                return attr(child, "AXChildren") or []
        return None
    return wait_for(items, wait) or []


def close_menu(items) -> None:
    """開いたメニューを何も選ばずに閉じる。"""
    for it in items:
        _ax().AXUIElementPerformAction(it, "AXCancel")
        return


def ensure_editor_view(win, log) -> bool:
    """コントロール表示ならエディタ表示に切り替える。切り替えたら True。"""
    btn = view_button(win)
    if btn is None:
        log("表示切り替えのボタンが見つからない（そのまま探す）")
        return False
    if not is_named(attr(btn, "AXTitle"), VIEW_CONTROLS):
        return False
    log("コントロール表示なので、エディタ表示に切り替える")
    threading.Thread(target=press_async, args=(btn, 0.3), daemon=True).start()
    items = open_menu_items(btn)
    target = next((it for it in items if is_named(attr(it, "AXTitle"), VIEW_EDITOR)), None)
    if target is None:
        close_menu(items)
        log("表示メニューに Editor が無い")
        return False
    _ax().AXUIElementPerformAction(target, "AXPress")
    # 窓の大きさが変わり、欄が描かれるまで待つ
    if wait_for(lambda: not is_named(attr(btn, "AXTitle"), VIEW_CONTROLS), 2.0):
        time.sleep(0.3)
    return True


def hit_root(win):
    """座標の問い合わせ先。**その窓のアプリに限って問い合わせる。**

    画面全体（system-wide）に問い合わせると、手前にある別のアプリの窓
    （Logicサンプルローダー自身や Keyboard Maestro の編集画面）に当たって、
    欄が「無い」ことになる。アプリの要素に問い合わせれば、そのアプリの窓だけで
    判定される（2026-09-25 実測。Keyboard Maestro の窓が重なっていても欄に当たった）。
    """
    AS = _ax()
    err, pid = AS.AXUIElementGetPid(win, None)
    if err != 0:
        return AS.AXUIElementCreateSystemWide()
    return AS.AXUIElementCreateApplication(pid)


def find_popup(win, log):
    """座標で問い合わせて、Sample name の欄を探す。"""
    AS = _ax()
    frame = frame_of(win)
    if frame is None:
        return None
    s = scale_of(frame)
    root = hit_root(win)
    seen = 0
    for x, y in probe_points(frame):
        err, el = AS.AXUIElementCopyElementAtPosition(root, x, y, None)
        seen += 1
        if err != 0 or el is None:
            continue
        if not belongs_to(el, win):
            continue            # 別の窓（手前の窓など）に当たった
        if same_element(attr(el, "AXParent"), win):
            continue            # 窓の直接の子は Logic 側の見出し（プリセット欄など）。欄は孤立グループの子
        f = frame_of(el)
        if looks_like_sample_popup(attr(el, "AXRole"), attr(el, "AXValue"), f.w if f else 0, s):
            log(f"欄を見つけた（{seen} 点目、値 {attr(el, 'AXValue')!r}）")
            return el
    log(f"{seen} 点を調べたが欄が無い")
    return None


@dataclass
class FlexControls:
    flex: object
    follow: object
    speed: Optional[object] = None


def find_flex_controls(win, log) -> Optional[FlexControls]:
    """座標で問い合わせて、Flex・Follow Tempo・Speed の群を探す。"""
    AS = _ax()
    frame = frame_of(win)
    if frame is None:
        return None
    root = hit_root(win)
    seen = 0
    for x, y in follow_probe_points(frame):
        err, el = AS.AXUIElementCopyElementAtPosition(root, x, y, None)
        seen += 1
        if err != 0 or el is None or not belongs_to(el, win):
            continue
        if attr(el, "AXRole") != "AXCheckBox" or (attr(el, "AXDescription") or "").strip() != FOLLOW_TEMPO_LABEL:
            continue
        kids = attr(attr(el, "AXParent"), "AXChildren") or []
        entries = [(attr(k, "AXRole"), attr(k, "AXDescription") or "", k) for k in kids]
        flex, speed = pick_flex_siblings(entries, el, same_element)
        if flex is None:
            continue
        log(f"Flex の群を見つけた（{seen} 点目）")
        return FlexControls(flex, el, speed)
    log(f"{seen} 点を調べたが Flex の群が無い")
    return None


def read_flex(ctrls: FlexControls) -> FlexState:
    return FlexState(
        flex=bool(attr(ctrls.flex, "AXValue")),
        follow=bool(attr(ctrls.follow, "AXValue")),
        follow_enabled=bool(attr(ctrls.follow, "AXEnabled")),
        speed=str(attr(ctrls.speed, "AXValue") or "") if ctrls.speed is not None else "",
    )


def set_checkbox(el, want: bool, wait: float = 1.5) -> bool:
    """チェックボックスを want にする。**すでにそうなら押さない。** 切り替わったら True。"""
    if bool(attr(el, "AXValue")) == want:
        return True
    _ax().AXUIElementPerformAction(el, "AXPress")
    return bool(wait_for(lambda: bool(attr(el, "AXValue")) == want, wait))


def frontmost_pid() -> Optional[int]:
    """最前面のアプリの pid。

    **まず AX で読む。** NSWorkspace は、実行ループの無いスクリプトでは起動したときの値の
    ままになる（前に出したあとの変化を拾えない）。ただし AX は、最前面のアプリが
    問い合わせに答えないと -25204 になる（Keyboard Maestro の編集画面が前にあると
    こうなった。2026-09-25 実測）。そのときだけ NSWorkspace に頼る。
    """
    AS = _ax()
    app = attr(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication")
    if app is not None:
        err, pid = AS.AXUIElementGetPid(app, None)
        if err == 0:
            return int(pid)
    try:
        from AppKit import NSWorkspace
        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        return int(front.processIdentifier()) if front is not None else None
    except Exception:
        return None


def bring_to_front(pid: int, wait: float = 2.0) -> bool:
    """アプリを最前面に出す。出せたら True。"""
    if frontmost_pid() == pid:
        return True
    AS = _ax()
    AS.AXUIElementSetAttributeValue(AS.AXUIElementCreateApplication(pid), "AXFrontmost", True)
    if wait_for(lambda: frontmost_pid() == pid, wait):
        return True
    try:  # AX で出せなかったときの予備
        from AppKit import NSApplicationActivateAllWindows, NSRunningApplication
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if running is not None:
            running.activateWithOptions_(NSApplicationActivateAllWindows)
    except Exception:
        pass
    return bool(wait_for(lambda: frontmost_pid() == pid, wait))


def post_key(code: int, flags: int, pid: int) -> bool:
    """キーを 1 回送る。**その pid のアプリが最前面のときだけ送る**（別のアプリに打ち込まない）。

    Logic へ直接送る方式（CGEventPostToPid）では「開く」ダイアログが反応しなかった
    （2026-09-25 実測）ので、通常の入力経路（HID）へ流す。
    """
    if frontmost_pid() != pid:
        return False
    import Quartz
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, down)
        Quartz.CGEventSetFlags(ev, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.02)
    if flags:
        release_modifiers(pid)    # 修飾キーの「離した」を送っていないので、押したままと思われないように
    return True


# コマンド・シフト・オプション・コントロール（左）のキーコード
MODIFIER_KEYS = (55, 56, 58, 59)


def release_modifiers(pid: int) -> None:
    """Logic に「修飾キーを全部離した」と伝える（その pid だけに送る。ほかのアプリと実際のキーには触らない）。

    **Logic は修飾キーの状態を、自分に届いたイベントで覚えている。** コマンドを押したまま Logic から離れると
    （コマンド+タブで移るなど）、押したままと思い込む。その間は、トラックの見出しの `AXSelectedChildren` への
    書き込みが**コマンド+クリック（選ぶ・外すの切り替え）**になり、前のトラックが選ばれたまま増える・
    同じトラックを 2 回目に選ぶと外れる、を繰り返す（2026-10-27 実測。コマンドを押した・離したを Logic にだけ
    送ると、その通りに切り替わった。Caps Lock は関係なかった）。
    """
    import Quartz
    for code in MODIFIER_KEYS:
        ev = Quartz.CGEventCreateKeyboardEvent(None, code, False)
        Quartz.CGEventSetType(ev, Quartz.kCGEventFlagsChanged)
        Quartz.CGEventSetFlags(ev, 0)
        Quartz.CGEventPostToPid(pid, ev)
    time.sleep(0.05)


def _cmd_shift() -> int:
    import Quartz
    return Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskShift


@dataclass
class Target:
    """操作の相手（Logic と Quick Sampler の窓）。"""
    code: int
    pid: Optional[int] = None
    app: object = None
    win: object = None
    title: str = ""
    count: int = 0


def find_target(title: Optional[str] = None) -> Target:
    """Logic の窓から Quick Sampler のプラグイン画面を探す（焦点のあるものを優先）。

    title を渡すと、その題名の画面だけを相手にする（DMD のパッドの画面を、ほかの Quick Sampler と取り違えない）。
    """
    AS = _ax()
    if not AS.AXIsProcessTrusted():
        return Target(EXIT_NO_PERMISSION)
    pid = logic_pid()
    if pid is None:
        return Target(EXIT_NO_LOGIC)
    app = AS.AXUIElementCreateApplication(pid)
    windows = [w for w in (attr(app, "AXWindows") or []) if is_qs_window(w)]
    if title is not None:
        windows = [w for w in windows if (attr(w, "AXTitle") or "") == title]
    win = pick_window(windows, attr(app, "AXFocusedWindow"), same_element)
    if win is None:
        return Target(EXIT_NO_WINDOW, pid, app)
    return Target(EXIT_OK, pid, app, win, attr(win, "AXTitle") or "", len(windows))


@dataclass
class Result:
    code: int
    message: str = ""
    switched: bool = False
    controls_view: bool = False


def run(dry_run: bool = False, log: Callable[[str], None] = lambda m: None) -> Result:
    """Sample name の欄を押して、サンプル読み込みのメニューを開く。"""
    t = find_target()
    if t.code != EXIT_OK:
        return Result(t.code, MESSAGES[t.code])
    AS = _ax()
    win = t.win
    log(f"Quick Sampler の画面: {t.title!r}（候補 {t.count} 枚）")

    if dry_run:
        if is_controls_view(win):
            log("コントロール表示（欄はエディタ表示にしか無い。押すときに切り替える）")
            return Result(EXIT_OK, "", False, controls_view=True)
        switched = False
    else:
        # 別の窓が重なっていると座標の問い合わせがそちらに当たるので、先に最前面へ上げる
        AS.AXUIElementPerformAction(win, "AXRaise")
        switched = ensure_editor_view(win, log)
    popup = find_popup(win, log)
    if popup is None:
        return Result(EXIT_NO_POPUP, MESSAGES[EXIT_NO_POPUP], switched)
    if dry_run:
        return Result(EXIT_OK, "", switched)

    err = press_async(popup)
    log(f"押した（返事 {err}）")
    if err not in (0, ERR_CANNOT_COMPLETE):
        return Result(EXIT_NOT_OPENED, MESSAGES[EXIT_NOT_OPENED], switched)
    items = open_menu_items(popup)
    if not items:
        return Result(EXIT_NOT_OPENED, MESSAGES[EXIT_NOT_OPENED], switched)
    log("メニューの項目: " + " / ".join(attr(i, "AXTitle") or "（区切り）" for i in items))
    return Result(EXIT_OK, SWITCHED_MESSAGE if switched else "", switched)


# --------------------------------------------------------------------------
# Flex と Follow Tempo
# --------------------------------------------------------------------------

@dataclass
class FlexResult:
    code: int
    message: str = ""
    state: Optional[FlexState] = None
    switched: bool = False


def _with_switch_note(message: str, switched: bool) -> str:
    return f"{SWITCHED_MESSAGE}{SEP}{message}" if switched and message else message


def change_flex(which: str, want: Optional[bool], log: Callable[[str], None] = lambda m: None) -> FlexResult:
    """Flex（which="flex"）か Follow Tempo（which="follow"）を切り替える。want が None なら反転する。

    **押す前に Logic を最前面に出す。** 背面のアプリの部品は、押しても -25204 で
    断られることがある（実測）。
    """
    t = find_target()
    if t.code != EXIT_OK:
        return FlexResult(t.code, MESSAGES[t.code])
    if not bring_to_front(t.pid):
        return FlexResult(EXIT_NOT_FRONT, MESSAGES[EXIT_NOT_FRONT])
    _ax().AXUIElementPerformAction(t.win, "AXRaise")
    switched = ensure_editor_view(t.win, log)
    ctrls = find_flex_controls(t.win, log)
    if ctrls is None:
        return FlexResult(EXIT_NO_FLEX, _with_switch_note(MESSAGES[EXIT_NO_FLEX], switched), None, switched)
    before = read_flex(ctrls)
    if which == "follow":
        if not before.flex:
            return FlexResult(EXIT_FOLLOW_NEEDS_FLEX,
                              _with_switch_note(MESSAGES[EXIT_FOLLOW_NEEDS_FLEX], switched), before, switched)
        target, element = (not before.follow if want is None else want), ctrls.follow
    else:
        target, element = (not before.flex if want is None else want), ctrls.flex
    if not set_checkbox(element, target):
        return FlexResult(EXIT_PRESS_FAILED, _with_switch_note(MESSAGES[EXIT_PRESS_FAILED], switched),
                          read_flex(ctrls), switched)
    after = read_flex(ctrls)
    log(f"切り替えた: {before} → {after}")
    text = follow_message(after) if which == "follow" else flex_message(after)
    return FlexResult(EXIT_OK, _with_switch_note(text, switched), after, switched)


@dataclass
class TargetState:
    code: int
    message: str = ""
    title: str = ""
    sample: str = ""
    state: Optional[FlexState] = None
    controls_view: bool = False


def describe_target(log: Callable[[str], None] = lambda m: None) -> TargetState:
    """読み込み先の状態を読む。**押さない・切り替えない。**"""
    t = find_target()
    if t.code != EXIT_OK:
        return TargetState(t.code, MESSAGES[t.code])
    if is_controls_view(t.win):
        return TargetState(EXIT_OK, tr(f"{t.title} の Quick Sampler。コントロール表示なので、中身は読めません",
                                       f"Quick Sampler on {t.title}. It is in Controls view, so its contents cannot be read"),
                           t.title, controls_view=True)
    popup = find_popup(t.win, log)
    if popup is None:
        # 別の Logic の窓（DMD の画面など）が重なっていると欄に当たらない。「空」と言い切らない
        return TargetState(EXIT_OK, tr(f"{t.title} の Quick Sampler。Sample name の欄が読めませんでした。"
                                       "別の画面が重なっているかもしれません",
                                       f"Quick Sampler on {t.title}. Could not read the Sample name field. "
                                       "Another window may be covering it"), t.title)
    sample = (attr(popup, "AXValue") or "").strip()
    ctrls = find_flex_controls(t.win, log)
    state = read_flex(ctrls) if ctrls is not None else None
    if not sample or sample in NO_FILE_TEXTS:
        body = tr("素材はまだ読み込まれていません", "No sample loaded yet")
        sample = ""
    else:
        body = tr(f"素材は {sample}。{state_phrase(state)}", f"Sample: {sample}. {state_phrase(state)}")
    return TargetState(EXIT_OK, tr(f"{t.title} の Quick Sampler。{body}", f"Quick Sampler on {t.title}. {body}"),
                       t.title, sample, state)


# --------------------------------------------------------------------------
# ファイルを指定して読み込む
# --------------------------------------------------------------------------

@dataclass
class LoadResult:
    code: int
    message: str = ""
    title: str = ""
    loaded: str = ""
    state: Optional[FlexState] = None
    switched: bool = False
    flex_note: str = ""


def find_open_panel(app):
    for w in attr(app, "AXWindows") or []:
        if attr(w, "AXIdentifier") == OPEN_PANEL_ID:
            return w
    return None


def child_with(win, key: str, value: str):
    for c in attr(win, "AXChildren") or []:
        if attr(c, key) == value:
            return c
    return None


def goto_field(app):
    """「フォルダへ移動」の入力欄。焦点がそこにあるときだけ返す。"""
    fe = attr(app, "AXFocusedUIElement")
    if attr(fe, "AXRole") not in ("AXTextField", "AXComboBox"):
        return None
    parent = attr(fe, "AXParent")
    if attr(parent, "AXRole") == "AXSheet" or attr(parent, "AXIdentifier") == GOTO_SHEET_ID:
        return fe
    return None


def mode_radios(panel) -> dict:
    """「開く」ダイアログ下部の Original / Optimized の選択肢。"""
    found = {}
    for c in attr(panel, "AXChildren") or []:
        if attr(c, "AXRole") == "AXRadioButton":
            head = (attr(c, "AXTitle") or "").split(" ", 1)[0]
            found[head] = c
    return found


def _descendant(el, role: str, depth: int = 0, max_depth: int = 7):
    """子孫から、その役割の最初の要素を探す（幅は狭いので深さ優先で足りる）。"""
    for c in attr(el, "AXChildren") or []:
        if attr(c, "AXRole") == role:
            return c
        if depth < max_depth:
            found = _descendant(c, role, depth + 1, max_depth)
            if found is not None:
                return found
    return None


def _row_text(row) -> str:
    """列表示の行の文字（行 → セル → 文字の順に探す）。"""
    for c in [row] + list(attr(row, "AXChildren") or []):
        for k in [c] + list(attr(c, "AXChildren") or []):
            v = attr(k, "AXValue") or attr(k, "AXTitle")
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _display_name(path: Path) -> Optional[str]:
    """Finder と同じ表示名（ローカライズされたフォルダ名など）。取れなければ None。"""
    try:
        from Foundation import NSFileManager
        return str(NSFileManager.defaultManager().displayNameAtPath_(str(path)))
    except Exception:
        return None


def _boot_volume_name() -> str:
    try:
        from Foundation import NSURL, NSURLVolumeLocalizedNameKey
        values, _ = NSURL.fileURLWithPath_("/").resourceValuesForKeys_error_([NSURLVolumeLocalizedNameKey], None)
        return str(values[NSURLVolumeLocalizedNameKey])
    except Exception:
        return "Macintosh HD"


def _columns(browser) -> list:
    return list(attr(browser, "AXColumns") or [])


def _column_rows(column) -> Tuple[object, list]:
    lst = _descendant(column, "AXList")
    return lst, list(attr(lst, "AXChildren") or []) if lst is not None else []


def _ensure_column_view(panel, log):
    """列表示でなければ、表示の切り替えメニューで列表示にする。列表示の部品（AXBrowser）を返す。

    **出たばかりのダイアログは、Open のボタンができても列表示の部品がまだ無いことがある**ので、
    列表示の部品か表示の切り替えのボタンが出るまで待つ（2026-09-26、アプリから貼ったときに一度だけ
    「ファイルの場所へ移動できませんでした」になった。同じ手順を端末から通すと通った）。
    """
    def parts():
        found = _descendant(panel, "AXBrowser")
        if found is not None:
            return ("browser", found)
        view = next((c for c in attr(panel, "AXChildren") or [] if attr(c, "AXIdentifier") == "View Options"), None)
        return ("button", view) if view is not None else None

    got = wait_for(parts, 2.0)
    if got is None:
        log("「開く」ダイアログに列表示の部品も表示の切り替えも出ない")
        return None
    kind, button = got
    if kind == "browser":
        return button
    threading.Thread(target=press_async, args=(button, 0.3), daemon=True).start()
    items = open_menu_items(button, 1.5)
    target = next((i for i in items if any(w in (attr(i, "AXTitle") or "") for w in COLUMN_WORDS)), None)
    if target is None:
        close_menu(items)
        return None
    _ax().AXUIElementPerformAction(target, "AXPress")
    log("「開く」ダイアログを列表示に切り替えた")
    return wait_for(lambda: _descendant(panel, "AXBrowser"), 2.0)


def browse_panel_to(panel, path: Path, log) -> bool:
    """「開く」ダイアログの列表示を、**キーを使わずに** AX でたどってファイルを選ぶ。選べたら True。

    列（`AXBrowser` の `AXColumns`）の中の一覧（`AXList`）の `AXSelectedChildren` に行を書くと、
    そのフォルダが開いて次の列が出る（2026-09-25 実測。行そのものの `AXSelected` は書けない）。
    **キーを送らないので、LM の横取りを受けない**（DMD の画面が開いていると、LM が Return を横取りし、
    Logic はコマンド+シフト+G でダイアログを閉じた）。

    列は起動ディスクの一番上（/）から並ぶ。ほかのディスクや「最近使った項目」から始まっていたら、
    サイドバーで起動ディスクを選び直す。

    **列表示の部品は毎回探し直す。** サイドバーで場所を選び直すと部品が作り直され、前に掴んだものは
    古い列を返し続ける（2026-09-25、トラックへの読み込みで「書類」から始まったときに踏んだ）。
    """
    AS = _ax()
    if _ensure_column_view(panel, log) is None:
        return False
    parts = path.parts[1:]
    if not parts:
        return False

    def browser():
        return _descendant(panel, "AXBrowser")

    def first_column_has_top() -> bool:
        cols = _columns(browser())
        if not cols:
            return False
        _, rows = _column_rows(cols[0])
        # 一番上の列の名前は Logic の表示の言語による（日本語では「Users」が「ユーザ」）。実名と表示名の両方で探す
        top = entry_candidates(parts[0], _display_name(Path("/") / parts[0]))
        return match_entry([_row_text(r) for r in rows], top) is not None

    if not wait_for(first_column_has_top, 1.0):
        sidebar = _descendant(panel, "AXOutline")
        boot = _boot_volume_name()
        row = next((r for r in attr(sidebar, "AXRows") or []
                    if norm_name(_row_text(r)) == norm_name(boot)), None)
        if row is None:
            log(f"サイドバーに起動ディスク（{boot}）が無い")
            return False
        AS.AXUIElementSetAttributeValue(row, "AXSelected", True)
        if not wait_for(first_column_has_top, 3.0):
            log("起動ディスクを選んでも、一番上の列が出ない")
            return False

    here = Path("/")
    for depth, name in enumerate(parts):
        here = here / name
        cands = entry_candidates(name, _display_name(here))

        def found():
            cols = _columns(browser())
            if depth >= len(cols):
                return None
            lst, rows = _column_rows(cols[depth])
            i = match_entry([_row_text(r) for r in rows], cands)
            return (lst, rows[i]) if i is not None else None

        got = wait_for(found, 3.0)
        if not got:
            log(f"列 {depth} に {name!r} が無い")
            return False
        lst, row = got
        if not any(same_element(row, x) for x in attr(lst, "AXSelectedChildren") or []):
            # ファイルの行を書くと -25205 が返るが、選ばれてはいる（2026-09-25 実測）。返事では判断しない
            AS.AXUIElementSetAttributeValue(lst, "AXSelectedChildren", [row])
            if not wait_for(lambda: any(same_element(row, x) for x in attr(lst, "AXSelectedChildren") or []),
                            1.5):
                log(f"{name!r} を選べない")
                return False
    return True


def _goto_with_keys(app, pid: int, path: Path, log) -> int:
    """コマンド+シフト+G の「フォルダへ移動」で場所へ移る（列表示でたどれないときの予備）。

    **DMD の画面が開いているときは使わない**（LM が Return を横取りし、Logic はコマンド+シフト+G で
    ダイアログを閉じた。2026-09-25）。
    """
    AS = _ax()
    if not post_key(KEY_G, _cmd_shift(), pid):
        return EXIT_NOT_FRONT
    field = wait_for(lambda: goto_field(app), 3.0)
    if field is None:
        log("フォルダへ移動が開かない")
        return EXIT_GOTO_FAILED
    # **文字は打たない。** Logic では LM が 1 文字キーを横取りする（2026-09-24 に「uckalerS」になった）。
    # AX で値を書き、焦点を欄に戻してから Return を送る（焦点が小窓に移っていると Return が効かない）
    AS.AXUIElementSetAttributeValue(field, "AXValue", str(path))
    AS.AXUIElementSetAttributeValue(field, "AXFocused", True)
    if not wait_for(lambda: attr(field, "AXValue") == str(path), 1.0):
        log("フォルダへ移動の欄に書き込めない")
        return EXIT_GOTO_FAILED
    time.sleep(0.15)
    if not post_key(KEY_RETURN, 0, pid):
        return EXIT_NOT_FRONT
    if not wait_for(lambda: goto_field(app) is None, 3.0):
        log("フォルダへ移動が閉じない")
        return EXIT_GOTO_FAILED
    return EXIT_OK


def cancel_panel(app, pid: int, log) -> None:
    """読み込みをやめる。「フォルダへ移動」が開いていれば閉じ、「開く」ダイアログを Cancel で閉じる。"""
    if goto_field(app) is not None:
        post_key(KEY_ESCAPE, 0, pid)
        wait_for(lambda: goto_field(app) is None, 1.5)
    panel = find_open_panel(app)
    if panel is None:
        return
    cancel = child_with(panel, "AXIdentifier", CANCEL_BUTTON_ID)
    if cancel is not None:
        press_async(cancel, 1.0)
        wait_for(lambda: find_open_panel(app) is None, 3.0)
    log("開くダイアログを閉じた（読み込みはやめた）")


def ready_open_panel(app):
    """「開く」ダイアログ。**Open のボタンまでできあがっているときだけ**返す（出たての窓は中身が空のことがある）。"""
    panel = find_open_panel(app)
    return panel if child_with(panel, "AXIdentifier", OK_BUTTON_ID) is not None else None


def _fill_open_panel(app, pid: int, panel, path: Path, optimized: bool, log) -> int:
    """Quick Sampler の「開く」ダイアログで、読み込み方を選び、ファイルを選んで Open を押す。成功なら EXIT_OK。"""
    AS = _ax()
    radios = mode_radios(panel)
    want = MODE_OPTIMIZED if optimized else MODE_ORIGINAL
    radio = radios.get(want)
    if radio is None:
        log(f"選択肢 {want} が見つからない（そのまま進める）")
    elif attr(radio, "AXValue") != 1:
        AS.AXUIElementPerformAction(radio, "AXPress")
        wait_for(lambda: attr(radio, "AXValue") == 1, 1.0)
    log(f"読み込み方: {want}")
    return _open_file_in_panel(app, pid, panel, path, log)


def _open_file_in_panel(app, pid: int, panel, path: Path, log) -> int:
    """「開く」ダイアログでファイルを選んで Open を押す。成功なら EXIT_OK。Quick Sampler とトラックの両方が使う。"""
    # **まずキーを使わずに列表示をたどる。** たどれないときだけ、キーの「フォルダへ移動」に頼る
    if browse_panel_to(panel, path, log):
        log("列表示をたどってファイルを選んだ（キーは送っていない）")
    else:
        log("列表示でたどれないので、フォルダへ移動を使う")
        code = _goto_with_keys(app, pid, path, log)
        if code != EXIT_OK:
            return code

    # 目的のフォルダに移って、ファイルが選ばれたか（Open が押せるようになったか）
    where = child_with(panel, "AXIdentifier", WHERE_POPUP_ID) or next(
        (c for c in attr(panel, "AXChildren") or [] if is_named(attr(c, "AXTitle"), WHERE_TITLES)), None)
    ok = child_with(panel, "AXIdentifier", OK_BUTTON_ID)
    if ok is None or not wait_for(lambda: attr(ok, "AXEnabled") is True, 3.0):
        log("Open が押せるようにならない（ファイルが選ばれていない）")
        return EXIT_GOTO_FAILED
    where_now = (attr(where, "AXValue") or "") if where is not None else ""
    if where is not None and match_entry([where_now], entry_candidates(path.parent.name,
                                                                       _display_name(path.parent))) is None:
        log(f"移った先が違う（{where_now!r}、期待は {path.parent.name!r}）")
        return EXIT_GOTO_FAILED
    press_async(ok, 1.0)
    if not wait_for(lambda: find_open_panel(app) is None, 6.0):
        return EXIT_OPEN_FAILED
    return EXIT_OK


def load_file(path: str, optimized: bool = False, flex: Optional[bool] = None,
              follow: Optional[bool] = None, log: Callable[[str], None] = lambda m: None,
              window_title: Optional[str] = None) -> LoadResult:
    """ファイルを Quick Sampler に読み込む。

    欄を押す → Load Audio File… → 「開く」ダイアログで Original / Optimized を選ぶ →
    列表示を AX でたどってファイルを選ぶ（キーは送らない。たどれないときだけコマンド+シフト+G）→ Open。
    読み込めたら、欄の値で確かめる。
    flex / follow を渡すと、読み込んだあとに Flex と Follow Tempo をそのとおりにする
    （None なら触らない。Follow Tempo は Flex を入れたときだけ変えられる）。

    **途中で失敗したら「開く」ダイアログを閉じて、元の状態に戻す。**
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return LoadResult(EXIT_NO_FILE, MESSAGES[EXIT_NO_FILE])
    p = p.resolve()
    t = find_target(window_title)
    if t.code != EXIT_OK:
        return LoadResult(t.code, MESSAGES[t.code])
    AS = _ax()
    log(f"読み込み先: {t.title!r}（候補 {t.count} 枚）")
    if not bring_to_front(t.pid):
        return LoadResult(EXIT_NOT_FRONT, MESSAGES[EXIT_NOT_FRONT], t.title)
    AS.AXUIElementPerformAction(t.win, "AXRaise")
    switched = ensure_editor_view(t.win, log)

    def fail(code: int) -> LoadResult:
        return LoadResult(code, _with_switch_note(MESSAGES[code], switched), t.title, switched=switched)

    popup = find_popup(t.win, log)
    if popup is None:
        return fail(EXIT_NO_POPUP)
    err = press_async(popup)
    if err not in (0, ERR_CANNOT_COMPLETE):
        return fail(EXIT_NOT_OPENED)
    items = open_menu_items(popup)
    if not items:
        return fail(EXIT_NOT_OPENED)
    item = next((i for i in items if (attr(i, "AXTitle") or "").startswith(LOAD_ITEM_PREFIX)), None)
    if item is None:
        close_menu(items)
        return fail(EXIT_NO_LOAD_ITEM)
    press_async(item, 0.5)
    panel = wait_for(lambda: ready_open_panel(t.app), 6.0)
    if panel is None:
        return fail(EXIT_NO_PANEL)
    code = _fill_open_panel(t.app, t.pid, panel, p, optimized, log)
    if code != EXIT_OK:
        cancel_panel(t.app, t.pid, log)
        return fail(code)

    # **窓は探し直す。** 読み込むと Quick Sampler の窓が作り直され、手元の参照は古くなる
    # （2026-09-25 実測。古い参照だと欄が「無い」ことになり、読み込めたのに失敗と言っていた）
    seen: list = []
    fresh: list = []

    def loaded_value() -> Optional[str]:
        now = find_target(window_title)
        if now.code != EXIT_OK:
            return None
        value = _popup_value_if(now.win, p.name, log, seen)
        if value:
            fresh[:] = [now.win]
        return value

    shown = wait_for(loaded_value, 6.0, interval=0.1)
    if not shown:
        return fail(EXIT_NOT_LOADED)
    log(f"読み込めた: {shown!r}")
    win = fresh[0]

    state, note = None, ""
    if flex is not None or follow is not None:
        ctrls = find_flex_controls(win, log)
        if ctrls is None:
            note = MESSAGES[EXIT_NO_FLEX]
        else:
            if flex is not None and not set_checkbox(ctrls.flex, flex):
                note = MESSAGES[EXIT_PRESS_FAILED]
            if follow is not None and read_flex(ctrls).flex and not set_checkbox(ctrls.follow, follow):
                note = MESSAGES[EXIT_PRESS_FAILED]
            state = read_flex(ctrls)
    message = tr(f"{shown} を {t.title} の Quick Sampler に読み込みました", f"Loaded {shown} into Quick Sampler on {t.title}")
    if state is not None:
        message += f"{SEP}{state_phrase(state)}"
    if note:
        message += f"{SEP}{WARNING} {note}"
    return LoadResult(EXIT_OK, _with_switch_note(message, switched), t.title, shown, state, switched, note)


def _popup_value_if(win, filename: str, log, seen: Optional[list] = None) -> Optional[str]:
    """欄の値が読み込んだファイルのものなら、その値を返す。"""
    popup = find_popup(win, lambda m: None)
    value = (attr(popup, "AXValue") or "").strip() if popup is not None else ""
    if seen is not None and (not seen or seen[-1] != (popup is not None, value)):
        seen.append((popup is not None, value))
        log(f"欄の値: {value!r}（欄{'あり' if popup is not None else 'なし'}）")
    return value if loaded_name_matches(value, filename) else None


# --------------------------------------------------------------------------
# Drum Machine Designer のパッド（2026-09-25 から。読み込み方は実機で確かめながら決める）
# --------------------------------------------------------------------------

def is_dmd_window(win) -> bool:
    """Drum Machine Designer のプラグイン画面か（下端に「Drum Machine Designer」の文字がある）。"""
    for child in attr(win, "AXChildren") or []:
        if attr(child, "AXRole") == "AXStaticText" and (attr(child, "AXValue") or "") == DMD_LABEL:
            return True
    return False


def find_dmd_target() -> Target:
    """Logic の窓から DMD のプラグイン画面を探す（焦点のあるものを優先）。"""
    AS = _ax()
    if not AS.AXIsProcessTrusted():
        return Target(EXIT_NO_PERMISSION)
    pid = logic_pid()
    if pid is None:
        return Target(EXIT_NO_LOGIC)
    app = AS.AXUIElementCreateApplication(pid)
    windows = [w for w in (attr(app, "AXWindows") or []) if is_dmd_window(w)]
    win = pick_window(windows, attr(app, "AXFocusedWindow"), same_element)
    if win is None:
        return Target(EXIT_NO_WINDOW, pid, app)
    kit = next((attr(c, "AXValue") for c in attr(win, "AXChildren") or []
                if attr(c, "AXRole") == "AXStaticText" and (attr(c, "AXValue") or "") != DMD_LABEL), "") or ""
    return Target(EXIT_OK, pid, app, win, kit, len(windows))


@dataclass
class PadInfo:
    index: int
    note: str
    name: str
    title: str = ""                   # パッドに出ている素材の名前（「Empty Pad」や空文字のこともある）
    frame: Optional[Frame] = None     # 見出しの枠（クリックする位置の基準）


def read_pads(win) -> Dict[int, PadInfo]:
    """パッドの見出しと、パッドに出ている素材の名前を読む（押さない）。"""
    area = next((c for c in attr(win, "AXChildren") or [] if attr(c, "AXRole") == "AXScrollArea"), None)
    texts = []
    for c in attr(area, "AXChildren") or []:
        f = frame_of(c) if attr(c, "AXRole") == "AXStaticText" else None
        if f is not None:
            texts.append((f, (attr(c, "AXValue") or "").strip()))
    found = pad_label_spots([(f.x, f.y, v) for f, v in texts])
    pads: Dict[int, PadInfo] = {}
    spots = [(x, y, f"{note} - {name or '?'}") for x, y, note, name, _ in found]
    for (_x, _y, note, name, i), index in zip(found, pad_indexes(spots)):
        if index is not None:
            pads[index] = PadInfo(index, note, name, frame=texts[i][0])
    # 素材の名前は、同じ列で見出しのすぐ上（1 段の高さより近い）にある文字
    for f, v in texts:
        if not v or parse_pad_label(v):
            continue
        near = [(pad.frame.y - f.y, pad) for pad in pads.values()
                if pad.frame is not None and round(pad.frame.x) == round(f.x) and 0 < pad.frame.y - f.y < PAD_STEP_100[1]]
        if near:
            min(near, key=lambda n: n[0])[1].title = v
    return pads


def page_buttons(win) -> list:
    """ページを切り替える 3 つの点（小さな AXButton）。左から 1・2・3 ページ。"""
    dots = []
    for c in attr(win, "AXChildren") or []:
        f = frame_of(c)
        if attr(c, "AXRole") == "AXButton" and f is not None and f.w <= 14 and f.h <= 14:
            dots.append((f.x, c))
    return [c for _, c in sorted(dots, key=lambda d: d[0])]


def visible_page(win, pads: Dict[int, PadInfo]) -> Optional[int]:
    """いま表示されているページ（0〜2）。見出しが窓の中にあるパッドのページ。"""
    wf = frame_of(win)
    if wf is None:
        return None
    for index, pad in sorted(pads.items()):
        if pad.frame is not None and wf.x <= pad.frame.x < wf.x + wf.w:
            return pad_position(index)[0]
    return None


def click_at(x: float, y: float, pid: int) -> bool:
    """その位置を 1 回クリックする。**その pid のアプリが最前面のときだけ。** ポインタは元へ戻す。"""
    if frontmost_pid() != pid:
        return False
    import Quartz
    before = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    point = Quartz.CGPointMake(x, y)
    for kind in (Quartz.kCGEventMouseMoved, Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        ev = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.06)
    back = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, before, Quartz.kCGMouseButtonLeft)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, back)
    return True


def main_window(app):
    """プロジェクトの主画面（題が「… - Tracks」、日本語の表示では「… - トラック」）。"""
    for w in attr(app, "AXWindows") or []:
        if (attr(w, "AXTitle") or "").endswith(MAIN_TITLE_SUFFIXES):
            return w
    return None


def main_part(win, names: Sequence[str]):
    """主画面の直下の群（PART_CONTROL_BAR・PART_INSPECTOR・PART_TRACKS）。見つからなければ主画面そのもの。

    **探す範囲をここに絞る。** 主画面全体を幅優先で探すと、ループブラウザ（28,816 行）やライブラリが
    開いているときに、その行をすべて読んでから見つけることになる（2026-09-26 のレビューで指摘）。
    """
    return next((k for k in attr(win, "AXChildren") or [] if is_named(attr(k, "AXDescription"), names)), win)


def inspector_strip(app, side: str):
    """インスペクタのチャンネルストリップ（side は "Left" か "Right"）。表示されていなければ None。

    DMD でパッドを選ぶと、右がそのパッドのチャンネルになる（2026-09-25 実測）。
    **出力のトラック（Stereo Out）を選ぶと、Logic は左右の両方を「右」と名付ける**（左が Stereo Out、右が Master。
    2026-11-02 実測）。左が見つからなければ、並びの先頭のストリップを左とみなす（インスペクタは出ているので、
    「出ていない」と言わない。種類は入力のスロットが無いので audio にならない）。
    """
    main = main_window(app)
    queue = [(main_part(main, PART_INSPECTOR), 0)] if main is not None else []
    strips = []
    while queue:
        el, depth = queue.pop(0)
        help_ = attr(el, "AXHelp")
        if starts_named(help_, STRIP_HELP[side]):
            return el
        if any(starts_named(help_, names) for names in STRIP_HELP.values()):
            strips.append(el)       # 反対側のストリップ（中は探さない）
        elif depth < 8:
            queue.extend((k, depth + 1) for k in attr(el, "AXChildren") or [])
    return strips[0] if side == "Left" and strips else None


def instrument_slot(strip):
    """ストリップの音源スロット。入っていれば ("group", 群)、空なら ("empty", ボタン)、無ければ (None, None)。"""
    for g in attr(strip, "AXChildren") or []:
        if attr(g, "AXRole") == "AXGroup" and \
                [attr(k, "AXDescription") for k in attr(g, "AXChildren") or []] in SLOT_PARTS:
            return "group", g
        if attr(g, "AXRole") == "AXButton" and starts_named(attr(g, "AXHelp"), INSTRUMENT_SLOT_HELP):
            return "empty", g
    return None, None


QS_SLOT_NAMES = ("Q-Sampler", "Quick Sampler")    # 音源スロットの表示名（略称で出る）
QS_MENU_ITEM = "Quick Sampler (Single Sample)"


def insert_quick_sampler(app, button, log) -> bool:
    """空の音源スロットに Quick Sampler（Stereo）を入れる。**検索欄は AX で書く**（LM に文字を奪われない）。"""
    AS = _ax()
    AS.AXUIElementSetMessagingTimeout(button, 0.5)
    AS.AXUIElementPerformAction(button, "AXPress")
    field = wait_for(lambda: (lambda f: f if attr(f, "AXRole") == "AXTextField"
                              and attr(attr(f, "AXParent"), "AXRole") == "AXMenuItem" else None)(
        attr(app, "AXFocusedUIElement")), 2.0)
    if field is None:
        log("音源のメニューの検索欄が出ない")
        return False
    AS.AXUIElementSetAttributeValue(field, "AXValue", "Quick Sampler")
    menu = attr(attr(field, "AXParent"), "AXParent")
    item = wait_for(lambda: next((k for k in attr(menu, "AXChildren") or []
                                  if attr(k, "AXTitle") == QS_MENU_ITEM), None), 2.0)
    if item is None:
        close_menu(attr(menu, "AXChildren") or [])
        log("メニューに Quick Sampler が無い")
        return False
    sub = next((k for k in attr(item, "AXChildren") or [] if attr(k, "AXRole") == "AXMenu"), None)
    stereo = next((k for k in attr(sub, "AXChildren") or [] if is_named(attr(k, "AXTitle"), STEREO_ITEMS)), None) if sub else item
    if stereo is None:
        # 押す先が無いまま押すと例外で止まり、「思わぬ理由で止まりました」としか言えなくなる
        close_menu(attr(menu, "AXChildren") or [])
        log("Quick Sampler の項目に Stereo が無い")
        return False
    AS.AXUIElementPerformAction(stereo, "AXPress")
    log("空のパッドに Quick Sampler を入れた")
    return True


def close_plugin_window(win) -> None:
    close = attr(win, "AXCloseButton") or next((c for c in attr(win, "AXChildren") or []
                                               if is_named(attr(c, "AXDescription"), CLOSE_NAMES)), None)
    if close is not None:
        _ax().AXUIElementPerformAction(close, "AXPress")


def select_pad(t: Target, index: int, log) -> Optional[PadInfo]:
    """パッドを選ぶ（ページを切り替えてから、パッドをクリックする。LM と同じ方法）。"""
    AS = _ax()
    pads = read_pads(t.win)
    page = pad_position(index)[0]
    if visible_page(t.win, pads) != page:
        dots = page_buttons(t.win)
        if len(dots) > page:
            AS.AXUIElementPerformAction(dots[page], "AXPress")
            wait_for(lambda: visible_page(t.win, read_pads(t.win)) == page, 2.0)
            pads = read_pads(t.win)
    pad = pads.get(index)
    if pad is None or pad.frame is None:
        return None
    s = max(0.25, (frame_of(t.win).w if frame_of(t.win) else DMD_WIDTH_100) / DMD_WIDTH_100)
    if not click_at(pad.frame.x + 35 * s, pad.frame.y - 14 * s, t.pid):
        return None
    time.sleep(0.5)
    log(f"パッド {index}（{pad.note} {pad.name}）を選んだ")
    return pad


def rename_pad(t: Target, index: int, name: str, log) -> bool:
    """パッドの名前を変える。名前の上をダブルクリックすると欄が出るので、AX で書いて確定する（キーは送らない）。"""
    AS = _ax()
    pad = read_pads(t.win).get(index)
    if pad is None or pad.frame is None or frontmost_pid() != t.pid:
        return False
    s = max(0.25, (frame_of(t.win).w if frame_of(t.win) else DMD_WIDTH_100) / DMD_WIDTH_100)
    import Quartz
    before = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    point = Quartz.CGPointMake(pad.frame.x + 40 * s, pad.frame.y - 35 * s)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, point, Quartz.kCGMouseButtonLeft))
    time.sleep(0.08)
    for click in (1, 2):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            ev = Quartz.CGEventCreateMouseEvent(None, kind, point, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGMouseEventClickState, click)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.03)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(
        None, Quartz.kCGEventMouseMoved, before, Quartz.kCGMouseButtonLeft))
    app = AS.AXUIElementCreateApplication(t.pid)
    field = wait_for(lambda: (lambda f: f if attr(f, "AXRole") == "AXTextField" else None)(
        attr(app, "AXFocusedUIElement")), 1.5)
    if field is None:
        log("パッドの名前の欄が出ない")
        return False
    AS.AXUIElementSetAttributeValue(field, "AXValue", name)
    AS.AXUIElementPerformAction(field, "AXConfirm")
    done = wait_for(lambda: (read_pads(t.win).get(index) or PadInfo(0, "", "")).title == name, 2.0)
    log(f"パッド {index} の名前を「{name}」にした" if done else "パッドの名前が変わらない")
    return bool(done)


def load_into_pad(path: str, index: int, optimized: bool = False, flex: Optional[bool] = None,
                  follow: Optional[bool] = None, log: Callable[[str], None] = lambda m: None) -> LoadResult:
    """DMD のパッドにファイルを読み込む（2026-09-25 に実機で組み立てた手順）。

    1. パッドをクリックして選ぶ（右のインスペクタがそのパッドのチャンネルになる）
    2. 空なら、右の音源スロットに Quick Sampler を入れる。**Quick Sampler 以外が入っていたらやめる**
    3. その Quick Sampler を**単独の画面で開き、DMD の画面を閉じる**
    4. 単独の画面で、いつもの読み込み（`load_file`。「開く」ダイアログはキーを使わずにたどる）
    5. 単独の画面を閉じ、DMD の画面を開き直して、パッドの名前を素材の名前にする

    **DMD の中に出る Quick Sampler の画面からは読み込まない。** そこで「開く」ダイアログを使うと
    Logic が落ちた（2026-09-25 02:13）。ドラッグ（合成マウス）と貼り付け（Cmd+V）はパッドに受け取られなかった。
    パッドの名前は、読み込んでも「Empty Pad」などのままなので、書き換えないと耳で区別できない。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return LoadResult(EXIT_NO_FILE, MESSAGES[EXIT_NO_FILE])
    p = p.resolve()
    if not 1 <= index <= PAD_COUNT:
        return LoadResult(EXIT_NO_PAD, MESSAGES[EXIT_NO_PAD])
    t = find_dmd_target()
    if t.code != EXIT_OK:
        return LoadResult(EXIT_NO_DMD if t.code == EXIT_NO_WINDOW else t.code,
                          MESSAGES[EXIT_NO_DMD if t.code == EXIT_NO_WINDOW else t.code])
    AS = _ax()
    kit = t.title
    if not bring_to_front(t.pid):
        return LoadResult(EXIT_NOT_FRONT, MESSAGES[EXIT_NOT_FRONT], kit)
    AS.AXUIElementPerformAction(t.win, "AXRaise")
    time.sleep(0.2)
    pad = select_pad(t, index, log)
    if pad is None:
        return LoadResult(EXIT_NO_PAD, MESSAGES[EXIT_NO_PAD], kit)
    # **パッドのチャンネルが出るまで待つ。** チャンネルの無い空のパッドを選ぶと、Logic がチャンネル（「Inst 5」など）を
    # 作るまで、右には出力（Stereo Out。音源スロットが無い）が出ている（2026-11-02、アプリから続けて試したときに
    # 「インスペクタが表示されていません」になった）
    def pad_strip():
        strip = inspector_strip(t.app, "Right")
        return strip if strip is not None and instrument_slot(strip)[0] is not None else None

    right = wait_for(pad_strip, 2.0) or inspector_strip(t.app, "Right")
    if right is None:
        return LoadResult(EXIT_NO_INSPECTOR, MESSAGES[EXIT_NO_INSPECTOR], kit)
    kind, slot = instrument_slot(right)
    if kind == "empty":
        if not insert_quick_sampler(t.app, slot, log):
            return LoadResult(EXIT_INSERT_FAILED, MESSAGES[EXIT_INSERT_FAILED], kit)
        got = wait_for(lambda: (lambda k: k if k[0] == "group" else None)(instrument_slot(inspector_strip(t.app, "Right"))), 3.0)
        if not got:
            return LoadResult(EXIT_INSERT_FAILED, MESSAGES[EXIT_INSERT_FAILED], kit)
        kind, slot = got
    if kind != "group":
        return LoadResult(EXIT_NO_INSPECTOR, MESSAGES[EXIT_NO_INSPECTOR], kit)
    if (attr(slot, "AXDescription") or "") not in QS_SLOT_NAMES:
        return LoadResult(EXIT_OTHER_INSTRUMENT, MESSAGES[EXIT_OTHER_INSTRUMENT], kit)

    # 単独の画面で開く（すでに開いている Quick Sampler の画面は数えておき、新しく開いたものを使う）
    before = [w for w in attr(t.app, "AXWindows") or [] if is_qs_window(w)]
    opener = (attr(slot, "AXChildren") or [None, None])[1]
    AS.AXUIElementSetMessagingTimeout(opener, 1.0)
    AS.AXUIElementPerformAction(opener, "AXPress")
    pad_win = wait_for(lambda: next((w for w in attr(t.app, "AXWindows") or [] if is_qs_window(w)
                                     and not any(same_element(w, b) for b in before)), None), 3.0)
    if pad_win is None:
        focused = attr(t.app, "AXFocusedWindow")
        pad_win = focused if focused is not None and is_qs_window(focused) else None
    if pad_win is None:
        return LoadResult(EXIT_PAD_WINDOW, MESSAGES[EXIT_PAD_WINDOW], kit)
    pad_title = attr(pad_win, "AXTitle") or ""       # パッドのチャンネル名（読み込んでも変わらない）
    close_plugin_window(t.win)                       # DMD の画面を閉じる（読み込む間だけ）
    wait_for(lambda: find_dmd_target().code != EXIT_OK, 2.0)
    AS.AXUIElementPerformAction(pad_win, "AXRaise")
    time.sleep(0.2)
    res = load_file(str(p), optimized, flex, follow, log, window_title=pad_title or None)

    # 片付け: パッドの画面を閉じ、DMD の画面を開き直す（主画面の左のストリップは DMD のトラックのまま）。
    # **閉じるのはパッドの画面だけ**（題名で選ぶ。ほかのトラックの Quick Sampler の画面は閉じない）
    now = find_target(pad_title or None)
    if now.code == EXIT_OK and pad_title:
        close_plugin_window(now.win)
    left = inspector_strip(t.app, "Left")
    kind_l, slot_l = instrument_slot(left) if left is not None else (None, None)
    if kind_l == "group":
        opener_l = (attr(slot_l, "AXChildren") or [None, None])[1]
        AS.AXUIElementSetMessagingTimeout(opener_l, 1.0)
        AS.AXUIElementPerformAction(opener_l, "AXPress")
    back = wait_for(lambda: (lambda d: d if d.code == EXIT_OK else None)(find_dmd_target()), 3.0)
    if res.code != EXIT_OK:
        code, message = pad_load_failure(res.code, res.message)
        return LoadResult(code, message, kit)
    renamed = False
    if back is not None:
        name = Path(res.loaded or p.name).stem
        renamed = rename_pad(back, index, name, log)
    message = tr(f"{res.loaded} を {kit} のパッド {index}（{pad.note}）に読み込みました",
                 f"Loaded {res.loaded} into pad {index} ({pad.note}) of {kit}")
    note = pad_notes(res.flex_note, renamed)
    if note:
        message += f"{SEP}{WARNING} {note}"
    return LoadResult(EXIT_OK, message, kit, res.loaded, res.state, res.switched, note)


def pad_load_failure(code: int, message: str) -> Tuple[int, str]:
    """パッドの画面での読み込みが失敗したときの (終了コード, 文)。

    **パッドの Quick Sampler の画面は、この補助が開いたもの。** 見失ったときに load_file の
    「Quick Sampler の画面が開いていません。ハイフンで開いてから…」を読むと、利用者は開き方を探してしまう
    （2026-10-24 のレビュー）。開けなかった、と言う。
    """
    if code == EXIT_NO_WINDOW:
        return EXIT_PAD_WINDOW, MESSAGES[EXIT_PAD_WINDOW]
    return code, message


def pad_notes(flex_note: str, renamed: bool) -> str:
    """パッドへ読み込んだあとの警告。**全部つなげる**（Flex を設定できなかったことを、パッドの名前の警告で
    上書きしない。それまでは名前の警告だけが残り、Flex の失敗は黙って消えていた。2026-10-24 のレビュー）。"""
    notes = [flex_note, "" if renamed else tr("パッドの名前は変えられませんでした", "The pad name could not be changed")]
    return SEP.join(n for n in notes if n)


# --------------------------------------------------------------------------
# オーディオトラックに貼る（2026-09-25、Logic Pro 12.3 で実測）
# --------------------------------------------------------------------------
#   **File > Import > Audio File…（シフト+コマンド+I）は、オーディオトラックを選んでいるときだけ押せる**
#   （ソフトウェア音源のトラックを選ぶと AXEnabled が偽になり、押してもダイアログは出ない）。押すと
#   「開く」ダイアログ（open-panel、題は Open File）が出て、Open で**選んでいるトラックの再生位置**に置かれる。
#   置いたリージョンは選ばれた状態で出る（説明はファイル名から拡張子を除いたもの）。
#   空のモノラルのトラックにステレオの素材を置くと、トラックがステレオに切り替わった。
#   **置かれるのは、再生位置のある小節の頭**（5 小節目の 3 拍目 → 5 小節目の頭、17 小節目の 4 拍目 → 17 小節目の頭。
#   拍の上に置いても小節の頭へ寄る）。いちばん近い小節にしたいときは、先に再生位置を動かす（snap="bar"）。
#   同じファイルを 2 回目に置くと、リージョンの名前の後ろに「.1」「.2」が付く。
#   リージョンの Smart Tempo は「On」で入るが、ファイルのテンポが無いので、あとでテンポを変えても追従しなかった
#   （119 にすると「1 小節 3 拍 3 分割 176 ティック」になった＝秒の長さのまま）。
#   **LCD の欄（再生位置・テンポ）は、AX で値を書くと 1 目盛りずつしか動かない**（大きい値を書くと +1、
#   小さいと -1。100 を書いたテンポは 119 になった）。再生位置は 1 目盛りずつ書いて動かす（1 回 1 ミリ秒ほど）

# 最後は前方一致（末尾は「…」）。日本語の表示では ファイル > 読み込む > オーディオファイル…
IMPORT_MENU = (("File", "ファイル"), ("Import", "読み込む"), ("Audio File", "オーディオファイル"))


def _find_by(el, pred: Callable[[object], bool], max_depth: int = 8):
    """幅優先で最初に pred を満たす子孫。"""
    queue = [(el, 0)]
    while queue:
        cur, depth = queue.pop(0)
        if cur is not el and pred(cur):
            return cur
        if depth < max_depth:
            queue.extend((k, depth + 1) for k in attr(cur, "AXChildren") or [])
    return None


def find_main() -> Target:
    """Logic の主画面（題が「… - Tracks」か「… - トラック」）。"""
    AS = _ax()
    if not AS.AXIsProcessTrusted():
        return Target(EXIT_NO_PERMISSION)
    pid = logic_pid()
    if pid is None:
        return Target(EXIT_NO_LOGIC)
    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(app, 3.0)
    win = main_window(app)
    if win is None:
        return Target(EXIT_NO_MAIN, pid, app)
    return Target(EXIT_OK, pid, app, win, attr(win, "AXTitle") or "")


def lcd_fields(win) -> Dict[str, object]:
    """コントロールバーの表示（Tempo・Key Signature・Time Signature・Sample Rate・Division・Playhead）。"""
    def has_tempo(e) -> bool:
        return any(is_named(attr(k, "AXDescription"), LCD_NAMES["Tempo"]) for k in attr(e, "AXChildren") or [])

    outer = main_part(win, PART_CONTROL_BAR)
    bar = outer if has_tempo(outer) else _find_by(
        outer, lambda e: attr(e, "AXRole") == "AXGroup" and is_named(attr(e, "AXDescription"), PART_CONTROL_BAR)
        and has_tempo(e), 3)
    found: Dict[str, object] = {}
    for k in attr(bar, "AXChildren") or []:
        desc, help_ = attr(k, "AXDescription") or "", attr(k, "AXHelp") or ""
        key = next((name for name, names in LCD_NAMES.items() if desc in names), None)
        if key is not None:
            found[key] = k
        elif starts_named(help_, DIVISION_HELP):
            found["Division"] = k
        elif is_named(desc, PLAYHEAD_NAMES) and starts_named(help_, PLAYHEAD_BARS_HELP):
            found["Playhead"] = k
    return found


def transport_playing(win) -> bool:
    """Logic が再生中（録音中）か。コントロールバーの Play（AXCheckBox）で見る。

    **再生中は再生位置を書き換えない。** 再生で値が進み続けるので 1 目盛りずつ書いても追いつかず、
    再生位置を飛ばし続けて音が途切れる（2026-09-26 のレビューで指摘）。
    """
    for k in attr(main_part(win, PART_CONTROL_BAR), "AXChildren") or []:
        if attr(k, "AXRole") == "AXCheckBox" and is_named(attr(k, "AXDescription"), PLAY_NAMES):
            value = attr(k, "AXValue")
            return value is True or str(value).strip() in ("1", "True")
    return False


def _playhead_parts(fields: Dict[str, object]) -> Dict[str, object]:
    """再生位置の 4 つの欄（"bar"・"beat"・"division"・"tick" → 部品）。"""
    parts: Dict[str, object] = {}
    for k in attr(fields.get("Playhead"), "AXChildren") or []:
        desc = attr(k, "AXDescription") or ""
        name = next((n for n, names in PLAYHEAD_PART_NAMES.items() if desc in names), None)
        if name is not None:
            parts[name] = k
    return parts


def read_position(fields: Dict[str, object]) -> Optional[Position]:
    parts = _playhead_parts(fields)
    values = [parse_number(attr(parts.get(n), "AXValue")) for n in ("bar", "beat", "division", "tick")]
    if any(v is None for v in values):
        return None
    return tuple(int(v) for v in values)  # type: ignore[return-value]


def track_rows(win) -> List[Tuple[int, str, bool, object]]:
    """トラックの見出し（番号, 名前, 選ばれているか, 要素）。"""
    header = _find_by(main_part(win, PART_TRACKS), lambda e: is_named(attr(e, "AXDescription"), TRACKS_HEADER), 7)
    rows = []
    for k in attr(header, "AXChildren") or []:
        parsed = parse_track_desc(attr(k, "AXDescription"))
        if parsed:
            rows.append((parsed[0], parsed[1], bool(attr(k, "AXSelected")), k))
    return rows


def revalidate_menu(app, names: Sequence[str], log: Callable[[str], None] = lambda m: None) -> None:
    """メニューバーの項目（names のどれか）を開いて閉じる。**Logic はメニューを開いたときに有効・無効を決め直す。**

    トラックを選び直したあと、Logic を前に出しても File > Import > Audio File… が無効のまま残ることがある
    （2026-11-02 実測。DMD の画面に焦点があったあと。主画面を前にしても変わらず、ファイルのメニューを開くと
    有効になった。閉じても有効のまま）。
    """
    top = next((i for i in attr(attr(app, "AXMenuBar"), "AXChildren") or [] if is_named(attr(i, "AXTitle"), names)), None)
    if top is None:
        return
    def is_open() -> bool:
        # 開いているかは、メニューバーの項目の AXSelected で見る（下の AXMenu は閉じていても子にある。2026-11-02 実測）
        return attr(top, "AXSelected") is True

    threading.Thread(target=press_async, args=(top, 0.3), daemon=True).start()
    if not wait_for(is_open, 2.0):
        log("メニューが開かない（決め直させられなかった）")
        return
    time.sleep(0.2)
    menu = next((m for m in attr(top, "AXChildren") or [] if attr(m, "AXRole") == "AXMenu"), None)
    close_menu(attr(menu, "AXChildren") or [])
    if not wait_for(lambda: not is_open(), 1.0):
        # 閉じなければ Esc で閉じる（開いたままだと、次の押下やキーがメニューに吸われる）
        err, pid = _ax().AXUIElementGetPid(app, None)
        if err == 0:
            post_key(KEY_ESCAPE, 0, int(pid))
            wait_for(lambda: not is_open(), 1.0)
    log("メニューの有効・無効を決め直させた（メニューを開いて閉じた）")


def import_item(app):
    """メニューの File > Import > Audio File…。見つからなければ None。"""
    level = attr(app, "AXMenuBar")
    for depth, title in enumerate(IMPORT_MENU):
        items = attr(level, "AXChildren") or []
        if depth > 0:     # メニューバーの項目の下は AXMenu をはさむ
            items = attr(items[0], "AXChildren") or [] if items else []
        last = depth == len(IMPORT_MENU) - 1
        level = next((i for i in items if (starts_named(attr(i, "AXTitle"), title) if last
                                           else is_named(attr(i, "AXTitle"), title))), None)
        if level is None:
            return None
    return level


@dataclass
class RegionInfo:
    name: str
    help: str
    size: str
    selected: bool


def lane_regions(win, index: int, name: str) -> Optional[List[RegionInfo]]:
    """そのトラックの行に並ぶリージョン。行が見つからなければ None（画面の外にあるときなど）。"""
    contents = _find_by(main_part(win, PART_TRACKS), lambda e: is_named(attr(e, "AXDescription"), TRACKS_CONTENTS), 7)
    lane = next((k for k in attr(contents, "AXChildren") or []
                 if parse_track_desc(attr(k, "AXDescription")) == (index, name)), None)
    if lane is None:
        return None
    return [RegionInfo(attr(r, "AXDescription") or "", attr(r, "AXHelp") or "", (attr(r, "AXSizeDescription") or "").strip(),
                       bool(attr(r, "AXSelected")))
            for r in attr(lane, "AXChildren") or [] if attr(r, "AXRole") == "AXLayoutItem"]


def strip_kind(app, name: Optional[str] = None) -> Optional[str]:
    """左のインスペクタのチャンネルの種類。"audio" / "other"。見えない・別のトラックのままなら None。

    **オーディオトラックかどうかは、メニューではなくここで見る。** トラックを AX で選び直した直後は、
    File > Import > Audio File… の有効・無効が前のトラックのまま残ることがある（0.8 秒待っても古かった。
    2026-09-25 実測）。チャンネルの部品は選び直すと約 0.1 秒で切り替わる。
    オーディオのチャンネルには**入力のスロット**（AXHelp が「Input slot」）と録音のボタンがある
    （ソフトウェア音源は音源のスロット、DMD のスタックは DMD、Aux には録音のボタンが無い）。
    """
    strip = inspector_strip(app, "Left")
    if strip is None or (name is not None and attr(strip, "AXDescription") != name):
        return None
    kids = attr(strip, "AXChildren") or []
    has_input = any(starts_named(attr(k, "AXHelp"), INPUT_SLOT_HELP) for k in kids)
    has_record = any(is_named(attr(k, "AXDescription"), RECORD_NAMES) for k in kids)
    return "audio" if has_input and has_record else "other"


def left_channel_mode(app) -> str:
    """左のインスペクタのチャンネルの「Mono」「Stereo」。分からなければ空文字。"""
    strip = inspector_strip(app, "Left")
    for k in attr(strip, "AXChildren") or []:
        if is_named(attr(k, "AXDescription"), CHANNEL_MODE_NAMES):
            return str(attr(k, "AXValue") or "")
    return ""


@dataclass
class ProjectState:
    code: int
    message: str = ""
    tempo: Optional[float] = None
    key: str = ""                           # Logic の表示のまま（「C Major」）
    meter: Optional[Tuple[int, int]] = None
    sample_rate: Optional[int] = None
    division: Optional[int] = None
    position: Optional[Position] = None
    track_index: Optional[int] = None
    track_name: str = ""
    can_import: bool = False                # オーディオトラックを選んでいる（貼れる）
    checked: bool = False                   # can_import をインスペクタのチャンネルで確かめたか（偽ならメニューの値）
    channel_mode: str = ""
    tracks: List[Tuple[int, str, bool]] = field(default_factory=list)   # 見出しの (番号, 名前, 選ばれているか)
    regions: List[Tuple[str, str]] = field(default_factory=list)       # 選んでいるトラックの (名前, AXHelp)
    paste_at: Optional[Position] = None     # 貼ると置かれる位置
    summary: str = ""                       # プロジェクト全体の要約（project_phrase）。message は用事ごとに変わる
    selected_count: int = 0                 # 選ばれているトラックの数（2 つ以上なら、いまのトラックはインスペクタのもの）
    strip_name: str = ""                    # 左のインスペクタに出ているチャンネルの名前（出ていなければ空文字）


def _unchecked_reason(st: ProjectState) -> str:
    """オーディオトラックかどうか確かめられなかった理由。**インスペクタが出ていないのか、追いついていないのかを分けて言う**
    （出ているのに「出ていない」と言うと、利用者は Logic でインスペクタを探してしまう）。"""
    if st.strip_name:
        return tr(f"Logic のインスペクタがまだ {st.strip_name} のままなので、オーディオトラックかどうか確かめられません。"
                  "コマンド+T で確かめ直せます",
                  f"Logic's inspector still shows {st.strip_name}, so it cannot be checked whether this is an audio track. "
                  "Press Command-T to check again")
    return tr("インスペクタが出ていないので、オーディオトラックかどうか確かめられません",
              "The inspector is not shown, so it cannot be checked whether this is an audio track")


def track_phrase(st: ProjectState) -> str:
    """貼り付け先のトラックの読み上げ。**トラックの名前を最初に言う**（「Audio 2、トラック 3、オーディオ、モノラル。
    1 小節目は空いています」）。キーで移ったとき、名前が聞こえないと、どこに移ったか分からない（2026-09-26 の指摘）。"""
    if st.track_index is None:
        return tr("トラックが選ばれていません", "No track is selected")
    head = tr(f"{st.track_name}、トラック {st.track_index}", f"{st.track_name}, track {st.track_index}")
    more = SEP + tr("ほかにも選ばれているトラックがあります", "Other tracks are also selected") if st.selected_count > 1 else ""
    if not st.checked:
        return f"{head}{SEP}{_unchecked_reason(st)}{more}"
    if not st.can_import:
        return f"{head}{SEP}" + tr("オーディオトラックではないので、貼れません", "Not an audio track, so it cannot be pasted on") + more
    mode = CHANNEL_WORDS.get(st.channel_mode, "")
    audio = tr("オーディオ", "audio")
    head += f"{COMMA}{audio}{COMMA}{mode}" if mode else f"{COMMA}{audio}"
    where = occupancy_phrase(st.regions, st.paste_at, st.meter, st.division)
    return (f"{head}{SEP}{where}" if where else head) + more


def project_phrase(st: ProjectState) -> str:
    """読み上げ用（「貼り付け先は Audio 1（トラック 2）。プロジェクトは 120 BPM、C メジャー、4/4…」）。"""
    if st.track_index is None:
        head = tr("トラックが選ばれていません", "No track is selected")
    elif not st.checked:
        head = tr(f"選んでいるトラックは {st.track_name}（トラック {st.track_index}）。{_unchecked_reason(st)}",
                  f"The selected track is {st.track_name} (track {st.track_index}). {_unchecked_reason(st)}")
    elif st.can_import:
        head = tr(f"貼り付け先は {st.track_name}（トラック {st.track_index}）",
                  f"Paste target: {st.track_name} (track {st.track_index})")
    else:
        head = tr(f"選んでいるトラック {st.track_name} はオーディオトラックではないので、貼れません",
                  f"The selected track {st.track_name} is not an audio track, so it cannot be pasted on")
    tempo = f"{st.tempo:g} BPM" if st.tempo else tr("テンポ不明", "tempo unknown")
    meter = f"{st.meter[0]}/{st.meter[1]}" if st.meter else tr("拍子不明", "time signature unknown")
    rate = f"{st.sample_rate / 1000:g} kHz" if st.sample_rate else ""
    parts = [tempo, key_phrase(st.key) or tr("キー不明", "key unknown"), meter] + ([rate] if rate else [])
    text = tr(f"{head}。プロジェクトは {'、'.join(parts)}。再生位置は {position_phrase(st.position)}",
              f"{head}. Project: {', '.join(parts)}. Playhead at {position_phrase(st.position)}")
    where = occupancy_phrase(st.regions, st.paste_at, st.meter, st.division) if st.can_import and st.checked else ""
    return f"{text}{SEP}{where}" if where else text


def current_track(t: Target, rows: Optional[List[Tuple[int, str, bool, object]]] = None,
                  strip_name: Optional[str] = None):
    """Logic の「いまのトラック」（見出しの行）と、選ばれているトラックの数。選ばれていなければ (None, 0)。

    **選ばれているトラックが 2 つ以上あるとき、Logic が貼るのはインスペクタに出ているトラック**
    （Classic Electric Piano と Audio 2 が選ばれ、インスペクタが Audio 2 のとき、Audio 2 に置かれた。2026-09-26 実測）。
    見出しの並びの最初の 1 つを選ぶと取り違える。インスペクタが出ていなければ、最初の 1 つ。
    strip_name（インスペクタのチャンネルの名前）を渡せば、読み直さない。
    """
    rows = track_rows(t.win) if rows is None else rows
    selected = [r for r in rows if r[2]]
    if not selected:
        return None, 0
    if strip_name is None:
        strip_name = attr(inspector_strip(t.app, "Left"), "AXDescription")
    return next((r for r in selected if r[1] == strip_name), selected[0]), len(selected)


def _read_project(t: Target, snap: str = "off") -> ProjectState:
    fields = lcd_fields(t.win)
    rows = track_rows(t.win)
    strip_name = str(attr(inspector_strip(t.app, "Left"), "AXDescription") or "")
    chosen, selected_count = current_track(t, rows, strip_name)
    item = import_item(t.app)
    # 種類はチャンネルの部品で見る（名前がいまのトラックと同じときだけ。違えば別のトラックのチャンネル）。
    # インスペクタが出ていないときだけメニューに頼る（選び直した直後は古いことがある。
    # そのときは checked が偽になり、読み上げでも「確かめられません」と言う）
    kind = strip_kind(t.app, chosen[1]) if chosen else None
    can_import = (kind == "audio") if kind is not None else bool(item is not None and attr(item, "AXEnabled"))
    st = ProjectState(
        EXIT_OK,
        tempo=parse_number(attr(fields.get("Tempo"), "AXValue")),
        key=str(attr(fields.get("Key Signature"), "AXValue") or ""),
        meter=parse_meter(attr(fields.get("Time Signature"), "AXValue")),
        sample_rate=parse_rate(attr(fields.get("Sample Rate"), "AXValue")),
        division=parse_division(attr(fields.get("Division"), "AXValue")),
        position=read_position(fields),
        track_index=chosen[0] if chosen else None,
        track_name=chosen[1] if chosen else "",
        can_import=can_import,
        checked=kind is not None,
        channel_mode=left_channel_mode(t.app) if chosen and kind == "audio" else "",
        tracks=[(i, n, sel) for i, n, sel, _ in rows],
        selected_count=selected_count,
        strip_name=strip_name,
    )
    if chosen:
        st.regions = [(r.name, r.help) for r in lane_regions(t.win, chosen[0], chosen[1]) or []]
    st.paste_at = paste_position(st.position, st.meter, st.division, snap)
    st.message = st.summary = project_phrase(st)
    return st


def read_project(log: Callable[[str], None] = lambda m: None, snap: str = "off") -> ProjectState:
    """プロジェクトのテンポ・キー・拍子・サンプルレート・再生位置と、選んでいるトラックを読む。**押さない。**"""
    t = find_main()
    if t.code != EXIT_OK:
        return ProjectState(t.code, MESSAGES[t.code])
    st = _read_project(t, snap)
    log(st.message)
    return st


# select_track の結果。**「選べたか」と「インスペクタが追いついたか」を分ける**（2026-09-26）。
# Logic が遅れているときに、前のトラックのチャンネルを見て種類を決めると取り違える
SELECT_OK = "ok"              # 選べた。インスペクタもそのトラックになった（インスペクタが出ていないときも、これ）
SELECT_LAGGING = "lagging"    # 選べたが、インスペクタが前のトラックのまま。種類を確かめられない
SELECT_STUCK = "stuck"        # Logic が選択に応えない（見出しが選ばれない）
SELECT_MISSING = "missing"    # そのトラックが見つからない
SELECT_WAIT = 2.0             # 見出しが選ばれるまで待つ秒数（ふだんは 0.01 秒）
INSPECTOR_WAIT = 1.5          # インスペクタがそのトラックになるまで待つ秒数（ふだんは 0.1 秒）


def select_track(t: Target, index: int, log: Callable[[str], None] = lambda m: None) -> str:
    """トラックの見出しを選ぶ（トラックの見出しの群の `AXSelectedChildren` に書く。1 つだけになる）。結果は SELECT_*。

    **Logic が追いつくまで待つ。** 選んでから約 0.1 秒で、メニュー（Import が押せるか）とインスペクタが切り替わる
    （2026-09-25 実測）。すぐ読むと前のトラックの値が返るので、インスペクタの名前がそのトラックになるまで待つ。
    **Logic が重いときは、もっとかかるか、追いつかない**（2026-09-26 の朝、5 分ほど続いた）。そのときは
    SELECT_LAGGING か SELECT_STUCK を返す。呼ぶ側は、インスペクタに残っている前のトラックの種類を使わないこと。
    Logic を前に出さない（このアプリにいたまま選べる）。
    """
    AS = _ax()
    header = _find_by(main_part(t.win, PART_TRACKS), lambda e: is_named(attr(e, "AXDescription"), TRACKS_HEADER), 7)
    rows = track_rows(t.win)
    row = next((r for r in rows if r[0] == index), None)
    if header is None or row is None:
        log(f"トラック {index} が見つからない")
        return SELECT_MISSING

    # 書き込みは「そのトラックをクリックした」扱い（2026-09-26 実測）。選ばれていなければ、それだけが選ばれる。
    # **すでに選ばれていれば選択は変わらず、そのトラックが「いまのトラック」になる**（インスペクタがそれに変わる）。
    # Logic が貼るのは、いまのトラック。並びの 2 つ目からは選ぶ・外すの切り替えになるので、1 つだけ書く。
    # **Logic がコマンドを押したままと思い込んでいると、1 つ目も切り替えになる**ので、先に離したと伝える
    # （2026-10-27。前は「Logic が遅れている」と読み違えていた。release_modifiers）
    release_modifiers(t.pid)
    AS.AXUIElementSetAttributeValue(header, "AXSelectedChildren", [row[3]])
    if not wait_for(lambda: any(i == index and sel for i, _, sel, _ in track_rows(t.win)), SELECT_WAIT, 0.02):
        log(f"トラック {index} を選べない（Logic が応えない）")
        return SELECT_STUCK
    if inspector_strip(t.app, "Left") is None:
        time.sleep(0.5)           # インスペクタが出ていないと目印が無い。メニューが追いつく時間だけ待つ
        return SELECT_OK
    if not wait_for(lambda: strip_kind(t.app, row[1]) is not None, INSPECTOR_WAIT, 0.02):
        log(f"インスペクタがトラック {index}（{row[1]}）にならない")
        return SELECT_LAGGING
    return SELECT_OK


def _stuck_state(t: Target, index: int, name: str, snap: str) -> ProjectState:
    """Logic がトラックの選択に応えなかったときの結果。**選ぼうとしたトラックの名前を先に言う。**"""
    st = _read_project(t, snap)
    st.code = EXIT_LOGIC_SLOW
    st.message = tr(f"{name}、トラック {index} を選ぼうとしましたが、Logic が応えません。"
                    "少し待ってから、もう一度押してください",
                    f"Tried to select {name}, track {index}, but Logic does not respond. "
                    "Wait a moment, then press again")
    return st


def choose_track(index: int, snap: str = "off", log: Callable[[str], None] = lambda m: None) -> ProjectState:
    """番号のトラックを貼り付け先にする（選ぶ）。オーディオトラックでなくても選ぶが、そう言う。"""
    t = find_main()
    if t.code != EXIT_OK:
        return ProjectState(t.code, MESSAGES[t.code])
    name = next((n for i, n, _, _ in track_rows(t.win) if i == index), "")
    result = select_track(t, index, log)
    if result == SELECT_MISSING:
        return ProjectState(EXIT_NO_TRACK, tr(f"トラック {index} が見つかりません", f"Track {index} not found"))
    if result == SELECT_STUCK:
        return _stuck_state(t, index, name, snap)
    st = _read_project(t, snap)      # 追いついていなければ、読み上げで「インスペクタがまだ前のトラック」と言う
    st.message = track_phrase(st)
    return st


def step_audio_track(delta: int, snap: str = "off", log: Callable[[str], None] = lambda m: None) -> ProjectState:
    """次（delta=1）・前（-1）の**オーディオトラック**を選ぶ。ソフトウェア音源などは飛ばす。端まで行けば反対の端へ回る。

    種類は見出しからは分からない（部品が同じ）ので、選んではインスペクタのチャンネルで見る。
    **インスペクタが出ていないと種類が分からない**（メニューの有効・無効は選び直した直後は古い）ので、選び始めずに止める。
    飛ばす候補はチャンネルだけを見て、見つかったトラックだけプロジェクトを読み直す。
    **Logic が追いつかなければ、そこで止めて、そう言う**（先へ進めても同じで、待つ時間だけ延びる。
    インスペクタに残った前のトラックの種類で決めると、ソフトウェア音源のトラックを「オーディオ」と取り違える）。
    見つからなければ元のトラックに戻す（初めに何も選ばれていなければ、選んだものを外さずに残す）。
    """
    t = find_main()
    if t.code != EXIT_OK:
        return ProjectState(t.code, MESSAGES[t.code])
    rows = track_rows(t.win)
    if not rows:
        return ProjectState(EXIT_NO_TRACK, MESSAGES[EXIT_NO_TRACK])
    here, _ = current_track(t, rows)
    if inspector_strip(t.app, "Left") is None:
        where = tr(f"いまは {here[1]}、トラック {here[0]}。", f"Now on {here[1]}, track {here[0]}. ") if here else ""
        return ProjectState(EXIT_NO_INSPECTOR, where + tr("インスペクタが出ていないので、どれがオーディオトラックか"
                                                          "確かめられません。Logic でインスペクタを表示してから（I のキー）、もう一度押してください",
                                                          "The inspector is not shown, so audio tracks cannot be told apart. "
                                                          "Show the inspector in Logic (the I key), then press again"))
    order = [r[0] for r in rows]
    names = {r[0]: r[1] for r in rows}
    current = here[0] if here else None
    start = order.index(current) if current in order else (-1 if delta > 0 else 0)
    step = 1 if delta > 0 else -1
    for k in range(1, len(order) + 1):
        candidate = order[(start + k * step) % len(order)]
        if candidate == current:
            break
        result = select_track(t, candidate, log)
        if result == SELECT_MISSING:
            continue
        if result == SELECT_STUCK:
            return _stuck_state(t, candidate, names.get(candidate, ""), snap)
        if result == SELECT_LAGGING:
            st = _read_project(t, snap)      # 名前を先に、確かめられなかったことをそのあとに言う
            st.message = track_phrase(st)
            return st
        if strip_kind(t.app, names.get(candidate)) == "audio":
            st = _read_project(t, snap)
            st.message = track_phrase(st)
            return st
        log(f"トラック {candidate} はオーディオトラックではない（飛ばす）")
    if current is not None:
        select_track(t, current, log)
    st = _read_project(t, snap)
    st.message = track_phrase(st) + SEP + tr("ほかにオーディオトラックがありません", "There is no other audio track")
    return st


def step_playhead(win, goal: Position, log: Callable[[str], None] = lambda m: None) -> bool:
    """再生位置を goal にする。**欄ごとに 1 目盛りずつ書く**（AX で書くと 1 目盛りしか動かないため）。

    **再生中は書かない**（偽を返す）。再生で値が進み続けて追いつかず、再生位置を飛ばし続けることになる。
    """
    AS = _ax()
    if transport_playing(win):
        log("再生中なので、再生位置は動かさない")
        return False
    names = ("bar", "beat", "division", "tick")
    for _ in range(2):
        parts = _playhead_parts(lcd_fields(win))
        for i, name in enumerate(names):
            el = parts.get(name)
            cur = parse_number(attr(el, "AXValue"))
            for _step in range(4000):
                if cur is None or int(cur) == goal[i]:
                    break
                AS.AXUIElementSetAttributeValue(el, "AXValue", float(int(cur) + (1 if goal[i] > cur else -1)))
                new = parse_number(attr(el, "AXValue"))
                if new is None or int(new) == int(cur):
                    log(f"再生位置の {name} が動かない")
                    break
                cur = new
        now = read_position(lcd_fields(win))
        if now == goal:
            return True
        log(f"再生位置が {now}（{goal} のはず）。やり直す")
    return False


# 素材にテンポ情報やマーカーがあると、貼ったあと Logic が「追加するオーディオファイルにはテンポ情報が含まれています。
# テンポ情報をこのプロジェクトに読み込みますか?」と尋ね、続けてマーカーについても同じように尋ねる（2026-11-02 実測。
# プロジェクトと違うサンプルレートの素材を Logic が変換した複製「…-48k」で出た。言語に関係なく出る）。
# **答えるまでメニューが使えず、次に貼るときに「開く」ダイアログが出ない。** このアプリは素材をプロジェクトに
# 合わせて貼る（プロジェクトを素材に合わせない）ので、どちらも「読み込まない」で答える
IMPORT_ALERTS = {
    "tempo": ("contains tempo information", "テンポ情報が含まれています"),
    "markers": ("contains markers", "マーカーが含まれています"),
}
DONT_IMPORT_TITLES = ("Don’t Import", "Don't Import", "読み込まない")
IMPORT_ALERT_WAIT = 1.0        # 貼ったあと、確認が出るまで待つ秒数（1 つ答えたら、次が出るまでも同じだけ待つ）


def import_alert(app) -> Optional[Tuple[str, object]]:
    """素材のテンポ情報・マーカーを読み込むかの確認の (種類, 「読み込まない」のボタン)。出ていなければ None。"""
    for w in attr(app, "AXWindows") or []:
        kids = attr(w, "AXChildren") or []
        text = " ".join(str(attr(k, "AXValue") or "") for k in kids if attr(k, "AXRole") == "AXStaticText")
        kind = next((kind for kind, texts in IMPORT_ALERTS.items() if any(t in text for t in texts)), None)
        if kind is None:
            continue
        button = next((k for k in kids if attr(k, "AXRole") == "AXButton"
                       and is_named(attr(k, "AXTitle"), DONT_IMPORT_TITLES)), None)
        if button is not None:
            return kind, button
    return None


def dismiss_import_alerts(app, log: Callable[[str], None], wait: float = 0.0) -> List[str]:
    """確認が出ていれば「読み込まない」で閉じる（プロジェクトのテンポとマーカーを変えない）。答えた種類の並びを返す。"""
    answered: List[str] = []
    for _ in range(len(IMPORT_ALERTS) + 1):
        got = wait_for(lambda: import_alert(app), wait)
        if got is None:
            break
        kind, button = got
        press_async(button, 1.0)
        wait_for(lambda: (lambda a: a is None or not same_element(a[1], button))(import_alert(app)), 2.0)
        log(f"素材の{'テンポ情報' if kind == 'tempo' else 'マーカー'}を読み込むかの確認に「読み込まない」で答えた")
        answered.append(kind)
    return answered


def import_alert_remark(kinds: Sequence[str]) -> str:
    """答えた確認の知らせ（「Logic がファイルのテンポ情報とマーカーを読み込むか尋ねたので、…」）。無ければ空文字。"""
    if not kinds:
        return ""
    ja = "と".join(dict.fromkeys({"tempo": "テンポ情報", "markers": "マーカー"}[k] for k in kinds))
    en = " and ".join(dict.fromkeys({"tempo": "tempo information", "markers": "markers"}[k] for k in kinds))
    return tr(f"Logic がファイルの{ja}を読み込むか尋ねたので、「読み込まない」と答えました。プロジェクトは変えていません",
              f"Logic asked whether to import the file's {en}. Answered Don't Import, so the project is unchanged")


def _import_enabled(t: Target) -> bool:
    return bool(attr(import_item(t.app), "AXEnabled"))


def _raise_main(t: Target) -> None:
    AS = _ax()
    AS.AXUIElementPerformAction(t.win, "AXRaise")
    AS.AXUIElementSetAttributeValue(t.win, "AXMain", True)


def _make_import_ready(t: Target, track_index: int, log: Callable[[str], None]) -> List[str]:
    """File > Import > Audio File… が無効に見えるとき、押せるようにする。知らせることの並びを返す。

    オーディオトラックを選んでいても無効に見えることがある（2026-09-25・2026-11-02 実測）:
      1. 選び直した直後の古い値 → 少し待つ。待っても古ければ、メニューを開いて決め直させる（revalidate_menu）
      2. **プラグインの画面（Quick Sampler など）に焦点がある** → 主画面を前に上げると押せる
      3. **DMD の画面が開いている** → 主画面を上げても焦点が DMD に残り、押せない。DMD の画面を閉じると押せる。
         閉じると Logic は選択を DMD のトラックに戻すので、貼り付け先を選び直す。閉じたことは言う
    """
    if wait_for(lambda: _import_enabled(t), 1.0):
        return []
    _raise_main(t)
    if wait_for(lambda: _import_enabled(t), 0.5):
        return []
    revalidate_menu(t.app, IMPORT_MENU[0], log)
    if _import_enabled(t) or find_dmd_target().code != EXIT_OK:
        return []
    for _ in range(3):
        dmd = find_dmd_target()
        if dmd.code != EXIT_OK:
            break
        close_plugin_window(dmd.win)
        wait_for(lambda: find_dmd_target().code != EXIT_OK, 1.0)
    log("DMD の画面が開いていて読み込めないので、閉じた")
    select_track(t, track_index, log)
    _raise_main(t)
    if not wait_for(lambda: _import_enabled(t), 1.0):
        revalidate_menu(t.app, IMPORT_MENU[0], log)
    return [tr("Drum Machine Designer の画面が開いていると Logic が貼らせないので、DMD の画面を閉じました",
               "Logic does not allow pasting while the Drum Machine Designer window is open, so the DMD window was closed")]


@dataclass
class PasteResult:
    code: int
    message: str = ""
    track: str = ""
    region: str = ""
    start: Optional[Position] = None
    end: Optional[Position] = None
    length: str = ""                         # Logic の表示のまま（「2 bars」）
    moved_from: Optional[Position] = None    # 再生位置を寄せたときの、寄せる前の位置
    tempo_before: Optional[float] = None
    tempo_after: Optional[float] = None
    note: str = ""
    playhead_to: Optional[Position] = None   # 貼ったあと再生位置を置いた先（動かさなかったら None）


def _selected_region_name(win) -> str:
    """インスペクタのリージョン欄に出ている名前（選んでいるリージョン）。"""
    insp = next((k for k in attr(win, "AXChildren") or [] if is_named(attr(k, "AXDescription"), PART_INSPECTOR)), None)
    for group in attr(_find_by(insp, lambda e: attr(e, "AXRole") == "AXList", 2), "AXChildren") or []:
        texts = [attr(k, "AXValue") for k in attr(group, "AXChildren") or []
                 if attr(k, "AXRole") in ("AXStaticText", "AXTextField")]
        if any(label in texts for label in REGION_LABELS):
            return next((str(v) for v in texts if v and v not in REGION_LABELS), "")
    return ""


def paste_audio(path: str, snap: str = "off", log: Callable[[str], None] = lambda m: None,
                playhead: str = "stay") -> PasteResult:
    """ファイルを、Logic で選んでいるオーディオトラックの再生位置に置く（File > Import > Audio File…）。

    **Logic は、再生位置のある小節の頭に置く**（拍の途中でも、拍の上でも）。snap="bar" なら、先に再生位置を
    いちばん近い小節の頭へ動かしてから貼る（小節の終わり近くで止めたとき、次の小節に貼られる）。
    画面を見ずに再生位置をちょうど小節の頭に置くのは難しいので、どこに置いたかを必ず言う。
    **「開く」ダイアログはキーを使わずにたどる。** 置けたかどうかは、トラックの行のリージョンで確かめる。
    playhead は貼ったあとの再生位置（PLAYHEAD_MODES）。"end" はリージョンの終わりへ進める（ループを続けて並べるとき。
    終わりが小節の途中なら次の小節の頭へ）。"start" はリージョンの頭に置く（すぐ再生して聴ける。再生位置が
    小節の途中だったときは、頭へ戻す）。"stay" は動かさない。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return PasteResult(EXIT_NO_FILE, MESSAGES[EXIT_NO_FILE])
    p = p.resolve()
    t = find_main()
    if t.code != EXIT_OK:
        return PasteResult(t.code, MESSAGES[t.code])
    st = _read_project(t)
    log(st.message)
    if st.track_index is None:
        return PasteResult(EXIT_NO_TRACK, MESSAGES[EXIT_NO_TRACK])
    if not st.can_import:
        return PasteResult(EXIT_NOT_AUDIO_TRACK, MESSAGES[EXIT_NOT_AUDIO_TRACK], st.track_name)
    if not bring_to_front(t.pid):
        return PasteResult(EXIT_NOT_FRONT, MESSAGES[EXIT_NOT_FRONT], st.track_name)

    # 前に貼ったときの確認が残っていた（このときはメニューが使えない）。**答えたら読み直す。** 確認が出ていた間に
    # 動かした再生位置は Logic に効いていないことがある（2026-11-02、表示は 3 小節目なのに 1 小節目に置かれた）
    answered = dismiss_import_alerts(t.app, log)
    if answered:
        st = _read_project(t)
        log(st.message)
        if st.track_index is None:
            return PasteResult(EXIT_NO_TRACK, SEP.join([MESSAGES[EXIT_NO_TRACK], import_alert_remark(answered)]))
        if not st.can_import:
            return PasteResult(EXIT_NOT_AUDIO_TRACK, SEP.join([MESSAGES[EXIT_NOT_AUDIO_TRACK],
                                                               import_alert_remark(answered)]), st.track_name)

    moved_from = None
    notes: List[str] = []      # 警告（[警告] のあとに、全部つなげて読む。上書きしない）
    remarks: List[str] = []    # 知らせるだけのこと（失敗ではない）

    def failed(code: int, why: str) -> PasteResult:
        """失敗を返す。**それまでにしたこと（DMD の画面を閉じた・確認に答えた）も言う**（黙って変えたことにしない）。"""
        done = remarks + ([import_alert_remark(answered)] if answered else [])
        return PasteResult(code, SEP.join([why] + done), st.track_name)

    playing = transport_playing(t.win)
    if snap == "bar" and st.position and st.meter and st.division:
        goal = snap_position(st.position, st.meter, st.division, "bar")
        if goal != st.position and playing:
            remarks.append(tr("再生中なので、再生位置は寄せていません", "Playing, so the playhead was not moved to the bar"))
        elif goal != st.position:
            if step_playhead(t.win, goal, log):
                moved_from = st.position
                log(f"再生位置を {st.position} から {goal} へ寄せた")
            else:
                notes.append(tr("再生位置をいちばん近い小節の頭へ寄せられなかったので、再生位置のある小節の頭に貼りました",
                                "Could not move the playhead to the nearest bar start, so it was pasted at the start of the playhead's bar"))

    def named(regions: Optional[List[RegionInfo]]) -> List[RegionInfo]:
        return [r for r in regions or [] if r.name.startswith(p.stem)]

    before = len(named(lane_regions(t.win, st.track_index, st.track_name)))
    # ほかのトラックの行にも同じ名前がいくつあるか（Logic が別のトラックに置いたときに気づけるように）
    before_all = {(i, n): len(named(lane_regions(t.win, i, n))) for i, n, _, _ in track_rows(t.win)}
    # 行が見えないときの確かめに使う（前に貼った同じ素材が選ばれたままでも、取り違えない）
    selected_before = _selected_region_name(t.win)
    item = import_item(t.app)
    if item is None:
        return failed(EXIT_NOT_AUDIO_TRACK, MESSAGES[EXIT_NOT_AUDIO_TRACK])
    if not attr(item, "AXEnabled"):
        remarks += _make_import_ready(t, st.track_index, log)
    for attempt in range(2):
        press_async(import_item(t.app) or item, 0.5)
        panel = wait_for(lambda: ready_open_panel(t.app), 6.0)
        if panel is None:
            why = MESSAGES[EXIT_NO_PANEL]
            if not st.checked:
                why += SEP + tr("オーディオトラックを選んでいるか確かめてください（インスペクタが出ていると、アプリが確かめられます）",
                                "Check that an audio track is selected (the app can check when the inspector is shown)")
            return failed(EXIT_NO_PANEL, why)
        code = _open_file_in_panel(t.app, t.pid, panel, p, log)
        if code == EXIT_OK:
            break
        cancel_panel(t.app, t.pid, log)
        if code != EXIT_GOTO_FAILED or attempt == 1:
            return failed(code, MESSAGES[code])
        # ファイルの場所へたどれなかった: 出たばかりで部品がそろっていなかったことがある。1 回だけ開き直す
        log("「開く」ダイアログでたどれなかったので、開き直す")
        wait_for(lambda: find_open_panel(t.app) is None, 3.0)

    def placed() -> Optional[RegionInfo]:
        regions = lane_regions(t.win, st.track_index, st.track_name)
        if regions is None:
            # 行が見つからない: インスペクタに出ている選んだリージョンの名前で確かめる。**貼る前と同じ名前なら
            # まだ置けていない**（同じファイルの 2 回目は「.1」が付くので、置ければ名前が変わる）
            name = _selected_region_name(t.win)
            return RegionInfo(name, "", "", True) if name.startswith(p.stem) and name != selected_before else None
        mine = named(regions)
        if len(mine) <= before:
            return None
        return next((r for r in mine if r.selected), mine[-1])

    def placed_or_answer() -> Optional[RegionInfo]:
        # **置く前に尋ねられることもある**（答えるまで置かれず、確認も残る）。待つ間も答える
        answered.extend(dismiss_import_alerts(t.app, log))
        return placed()

    region = wait_for(placed_or_answer, 6.0, interval=0.1)
    placed_on = st.track_name
    if region is None:
        # いまのトラックに見つからない: ほかのトラックに置かれていないか探す（選ばれているトラックが 2 つ以上のときなど）
        for (i, n), count in before_all.items():
            mine = named(lane_regions(t.win, i, n))
            if len(mine) > count:
                region, placed_on = next((r for r in mine if r.selected), mine[-1]), n
                log(f"トラック {i}（{n}）に置かれていた")
                break
    if region is None:
        return failed(EXIT_NOT_PLACED, MESSAGES[EXIT_NOT_PLACED])
    answered.extend(dismiss_import_alerts(t.app, log, IMPORT_ALERT_WAIT))
    span = parse_region_help(region.help)
    start, end = span if span else (None, None)
    tempo_after = parse_number(attr(lcd_fields(t.win).get("Tempo"), "AXValue"))
    message = tr(f"{region.name} を {placed_on} の {position_phrase(start)}に貼りました",
                 f"Pasted {region.name} on {placed_on} at {position_phrase(start)}")
    if placed_on != st.track_name:
        message += tr(f"（{st.track_name} ではなく、{placed_on} に置かれました）",
                      f" (it went on {placed_on}, not {st.track_name})")
    if region.size:
        message += SEP + tr(f"長さは {length_phrase(parse_length_words(region.size))}",
                            f"Length {length_phrase(parse_length_words(region.size))}")
    mode_after = left_channel_mode(t.app)
    if st.channel_mode and mode_after and mode_after != st.channel_mode:
        # 空のモノラルのトラックにステレオを置くと、Logic がトラックをステレオにする（2026-09-25 実測）
        was, now = CHANNEL_WORDS.get(st.channel_mode, st.channel_mode), CHANNEL_WORDS.get(mode_after, mode_after)
        message += SEP + tr(f"Logic がトラックを{was}から{now}に切り替えました", f"Logic switched the track from {was} to {now}")
    if moved_from is not None:
        message += SEP + tr(f"再生位置が {position_phrase(moved_from)}だったので、いちばん近い小節の頭に寄せてから貼りました",
                            f"The playhead was at {position_phrase(moved_from)}, so it was moved to the nearest bar start first")
    elif st.position and st.position[1:] != (1, 1, 1):
        message += SEP + tr(f"再生位置は {position_phrase(st.position)}。Logic は再生位置のある小節の頭に置きます",
                            f"The playhead was at {position_phrase(st.position)}. "
                            "Logic places it at the start of the playhead's bar")
    playhead_to = None
    if playhead in ("end", "start"):
        answered.extend(dismiss_import_alerts(t.app, log))   # 待つ時間より遅れて出た確認（この下で動かすと効かない）
        goal = playhead_goal(playhead, start, end)
        if goal is None:
            notes.append(tr("リージョンの位置が分からないので、再生位置は動かしませんでした",
                            "The region position is unknown, so the playhead was not moved"))
        elif playing or transport_playing(t.win):
            remarks.append(tr("再生中なので、再生位置は動かしていません", "Playing, so the playhead was not moved"))
        elif playhead == "start" and read_position(lcd_fields(t.win)) == goal:
            playhead_to = goal
            message += SEP + tr(f"再生位置は、貼ったリージョンの頭（{position_phrase(goal)}）のままです",
                                f"The playhead stays at the start of the pasted region ({position_phrase(goal)})")
        elif step_playhead(t.win, goal, log):
            playhead_to = goal
            message += SEP + (tr(f"再生位置を {position_phrase(goal)}に進めました",
                                 f"Moved the playhead to {position_phrase(goal)}") if playhead == "end"
                              else tr(f"再生位置を、貼ったリージョンの頭（{position_phrase(goal)}）に戻しました",
                                      f"Moved the playhead back to the start of the pasted region ({position_phrase(goal)})"))
        else:
            notes.append(tr("再生位置を動かせませんでした", "Could not move the playhead"))
    if answered:
        remarks.append(import_alert_remark(answered))
    if remarks:
        message += SEP + SEP.join(remarks)
    if st.tempo and tempo_after and abs(st.tempo - tempo_after) > 1e-3:
        notes.append(tr(f"貼ったあと、Logic がプロジェクトのテンポを {st.tempo:g} から {tempo_after:g} に変えました。"
                        "テンポのモードが ADAPT か AUTO のときに起きます",
                        f"After pasting, Logic changed the project tempo from {st.tempo:g} to {tempo_after:g}. "
                        "This happens when the tempo mode is ADAPT or AUTO"))
    note = SEP.join(notes)
    if note:
        message += f"{SEP}{WARNING} {note}"
    log(message)
    return PasteResult(EXIT_OK, message, placed_on, region.name, start, end, region.size, moved_from,
                       st.tempo, tempo_after, note, playhead_to)


# --------------------------------------------------------------------------
# 起動確認と入口
# --------------------------------------------------------------------------

def check(log: Callable[[str], None]) -> int:
    """動く状態かを調べる（押さない）。起動確認に使う。"""
    AS = _ax()
    trusted = bool(AS.AXIsProcessTrusted())
    print(f"qs_sample_menu {VERSION}")
    print(f"アクセシビリティの権限: {'あり' if trusted else 'なし'}")
    pid = logic_pid()
    print(f"Logic Pro: {'起動中' if pid else '起動していない'}")
    if not (trusted and pid):
        return EXIT_NO_PERMISSION if not trusted else EXIT_NO_LOGIC
    res = run(dry_run=True, log=log)
    if res.controls_view:
        print("Quick Sampler の画面: コントロール表示（押したときにエディタ表示へ切り替える）")
        return EXIT_OK
    print({EXIT_OK: "Quick Sampler の欄: 見つかった（押してはいない）",
           EXIT_NO_WINDOW: "Quick Sampler の画面: 開いていない（開けば使える）",
           EXIT_NO_POPUP: "Quick Sampler の欄: 見つからない"}
          .get(res.code, res.message))
    if res.code == EXIT_OK:
        t = find_target()
        ctrls = find_flex_controls(t.win, log) if t.code == EXIT_OK else None
        print("Flex の群: " + (f"見つかった（{state_phrase(read_flex(ctrls))}。押してはいない）"
                              if ctrls is not None else "見つからない"))
    return EXIT_OK


def _on_off(text: Optional[str]) -> Optional[bool]:
    if text in (None, "toggle"):
        return None
    return text == "on"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Quick Sampler の届かない部品を押す（読み込み・Flex）")
    ap.add_argument("--dry-run", action="store_true", help="欄を探すだけで押さない")
    ap.add_argument("--check", action="store_true", help="動く状態かを調べるだけ（押さない）")
    ap.add_argument("--quiet", action="store_true", help="経過を標準エラーに書かない")
    ap.add_argument("--state", action="store_true", help="いまの状態を 1 行で言う（押さない）")
    ap.add_argument("--flex", nargs="?", const="toggle", choices=("on", "off", "toggle"),
                    help="Flex を切り替える（--load と一緒なら、読み込んだあとに on / off にする）")
    ap.add_argument("--follow", nargs="?", const="toggle", choices=("on", "off", "toggle"),
                    help="Follow Tempo を切り替える（--load と一緒なら、読み込んだあとに on / off にする）")
    ap.add_argument("--load", metavar="ファイル", help="ファイルを読み込む")
    ap.add_argument("--optimized", action="store_true", help="--load のとき Optimized で読み込む（既定は Original）")
    ap.add_argument("--pad", type=int, metavar="番号", help="--load と一緒に: DMD のパッド（1〜48）に読み込む")
    ap.add_argument("--pads", action="store_true", help="DMD のパッドの一覧を読み上げ用に出す（押さない）")
    ap.add_argument("--project", action="store_true",
                    help="プロジェクトのテンポ・キー・再生位置と、選んでいるトラックを言う（押さない）")
    ap.add_argument("--paste", metavar="ファイル", help="選んでいるオーディオトラックの再生位置にファイルを貼る")
    ap.add_argument("--playhead", choices=PLAYHEAD_MODES, default="stay",
                    help="--paste のとき、貼ったあとの再生位置。end はリージョンの終わり（次に貼る位置）、"
                         "start はリージョンの頭、stay は動かさない（既定）")
    ap.add_argument("--advance", action="store_true", help="--playhead end と同じ（1.4 までの書き方）")
    ap.add_argument("--track", type=int, metavar="番号", help="そのトラックを貼り付け先にする（選ぶ）")
    ap.add_argument("--next-track", action="store_true", help="次のオーディオトラックを選ぶ")
    ap.add_argument("--prev-track", action="store_true", help="前のオーディオトラックを選ぶ")
    ap.add_argument("--snap", choices=SNAP_MODES, default="off",
                    help="--paste のとき bar なら、先に再生位置をいちばん近い小節の頭へ動かす"
                         "（既定の off は Logic の決まりどおり、再生位置のある小節の頭）")
    args = ap.parse_args(argv)
    log = (lambda m: None) if args.quiet else (lambda m: print(f"[qs] {m}", file=sys.stderr))
    if args.check:
        return check(log)
    if args.state:
        st = describe_target(log)
        print(st.message)
        return st.code
    if args.project:
        st = read_project(log, args.snap)
        print(st.message)
        return st.code
    if args.track or args.next_track or args.prev_track:
        st = (choose_track(args.track, args.snap, log) if args.track
              else step_audio_track(1 if args.next_track else -1, args.snap, log))
        print(st.message)
        return st.code
    if args.paste:
        res = paste_audio(args.paste, args.snap, log, playhead="end" if args.advance else args.playhead)
        print(res.message)
        return res.code
    if args.pads:
        t = find_dmd_target()
        if t.code != EXIT_OK:
            print(MESSAGES[EXIT_NO_DMD if t.code == EXIT_NO_WINDOW else t.code])
            return t.code
        for index, pad in sorted(read_pads(t.win).items()):
            print(f"{index} {pad.note} {pad.name}" + (f" 「{pad.title}」" if pad.title else ""))
        return EXIT_OK
    if args.load:
        if "toggle" in (args.flex, args.follow):
            ap.error("--load と一緒に使うときは --flex on / off のように値を付ける")
        if args.pad is not None:      # 0 も「パッドを指定した」（番号の誤りとして断る。Quick Sampler に読み込まない）
            res = load_into_pad(args.load, args.pad, args.optimized, _on_off(args.flex), _on_off(args.follow), log)
            print(res.message)
            return res.code
        res = load_file(args.load, args.optimized, _on_off(args.flex), _on_off(args.follow), log)
        print(res.message)
        return res.code
    if args.flex or args.follow:
        which = "follow" if args.follow else "flex"
        res = change_flex(which, _on_off(args.follow if args.follow else args.flex), log)
        print(res.message)
        return res.code
    res = run(dry_run=args.dry_run, log=log)
    if res.message:
        print(res.message)
    return res.code


if __name__ == "__main__":
    raise SystemExit(main())
