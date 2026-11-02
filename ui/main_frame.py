"""主画面。上から順に「探す → 選ぶ（聴く）→ 読み込む」。

### 作者の別のアプリと操作を揃える

状況で使い分けるので、
**同じキーが同じ意味を持つ**ようにしてある。

| キー | 意味 |
| --- | --- |
| 一覧でスペース | 試聴の再生と停止（元の音のまま） |
| 一覧で Enter | 実行（ここでは読み込み先に入れる。Quick Sampler・DMD のパッド・オーディオトラック） |
| 一覧で文字キー | その頭文字の項目へ飛ぶ |
| Cmd+S | 読み込みの設定（あちらはセッションの条件・割り当ての設定） |
| Cmd+R | Splice の索引を更新 |
| Cmd+T | 読み込み先を調べ直す（あちらは配置先・割り当て先） |
| Cmd+1 / 2 / 3 | 読み込み先を Quick Sampler / DMD のパッド / オーディオトラックにする |
| Cmd+C | パスをコピー |
| Cmd+I | 検索結果一覧へ移動 |
| Cmd+Shift+G | パックの並び順を順送り |
| Cmd+Shift+K | キー操作の一覧 |
| Cmd+Q | 終了 |

このアプリだけのもの: **Cmd+Shift+F** で Flex、**Cmd+Shift+P** で Follow Tempo を切り替える
（いま開いている Quick Sampler に対して）。**コマンド+右ブラケット（]）** で次のパッド、
**コマンド+左ブラケット（[）** で前のパッド（読み込み先が DMD のパッドのとき）。読み込み先がオーディオトラックの
ときは、同じキーで次・前の**オーディオトラック**を貼り付け先にする（「次・前の入れ先」という同じ意味にそろえた）。
**スペースの試聴は、どの読み込み先でも元の音のまま**鳴らす。プロジェクトのテンポとキーに合わせた音は
**シフト+スペース**（貼るときと同じ音。2026-10-13 の要望。それまでは、読み込み先がオーディオトラックのときは
スペースで合わせた音が鳴った）。**コマンド+シフト+T** で、テンポの合わせ方をハーフ・Even・ダブルと順送りする
（基準はプロジェクトの BPM）。
**記号のキーは、画面の文言でも必ずカタカナの名前で書く**（VoiceOver が ] と [ を読まなかった。2026-09-25）。

### 守っていること

- 板は `wx.ScrolledWindow`。読み上げ名は `label_control`
- **状態はボタンの名前に入れ、その場で読み上げる**（`_speak`）
- **修飾なしの文字キーを横取りしない。** 機能のキーはメニューのアクセラレータ（Cmd 付き）
- **ボタンは Enter でも押せるようにする**（macOS の wx は既定ボタン以外をスペースでしか押せない）
- **音は読み上げより先に鳴らし、成否から選ぶ**
- **Logic を操作する処理は別スレッドで、同時に 1 つだけ。** UI のスレッドで待つと、
  その間キー入力も読み上げも止まり、「固まった」としか分からない
- **一覧にフォーカスが来たら選択を戻す**（何も選ばれていないとスペースも上下も効かない）
- **起動したら、最初の欄（検索語）にフォーカスを置く。** どの欄にも無いと、窓そのものにフォーカスが残り、
  タブで動けない（2026-09-26 の指摘。実測で、タブを 3 回押しても窓のままだった）
- **読ませた文は、時刻と一緒にアプリのログに残す**（「読まなかった」ときに、何を読ませたかを確かめられる）
- **読み上げは、アプリ自身から VoiceOver へ通知を出して頼む**（`ui/speech.py`）。System Events で
  AXAnnouncement を書く前の方法は、この Mac では断られて何も読まれていなかった（2026-09-26 に判明）
"""

from __future__ import annotations

import os
import plistlib
import sys
import threading
import time
import traceback
from pathlib import Path

import wx

from core.fit import key_label, logic_key_query
from core.i18n import ERROR, PERIOD, SEP, WARNING, is_english, sentence, tr
from core.settings import TARGET_LABELS, describe
from core.sibling import FROZEN
from integration import km_helper, quick_sampler
from ui import speech
from ui.borrowed import label_control, note_names, play_sound, press_button
from ui.controller import (KEY_CHOICES, KIND_CHOICES, TEMPO_CHOICES, TRANSPOSE_CHOICES, AppController, Outcome,
                           SearchForm)
from ui.settings_dialog import SettingsDialog

__all__ = ["MainFrame", "ANNOUNCE_DELAY_MS", "RETURN_DELAY_MS", "PAD_STEP_DELAY_MS", "KEY_HELP", "KEY_HELP_EN", "APP_TITLE"]

APP_TITLE = tr("Logicサンプルローダー", "Logic Sample Loader")

# モーダルを閉じた直後に読ませるまでの間。実機で詰めた値
ANNOUNCE_DELAY_MS = 250
# Logic から戻ってきた直後に読ませるまでの間。窓が切り替わった読み上げが先に入るので長めに待つ
RETURN_DELAY_MS = 700
# メニューのキー（コマンド+右ブラケットなど）でパッドを移ったあと、読ませるまでの間。
# VoiceOver はまずメニューの項目名（「次のパッドへ」）を読むので、それが終わってから中身を読ませる
# （すぐ読ませると項目名だけが聞こえ、中身が聞こえなかった。2026-09-25 の指摘）
PAD_STEP_DELAY_MS = 800
# Logic のトラックを選ぶ用事が長引いたら、開始の音を鳴らすまでの間（すぐ終わるときは鳴らさない）
QUICK_JOB_SOUND_MS = 400
# Logic を操作する排他が一瞬だけ取られているとき（試聴でプロジェクトを読んでいる）、待ち直す回数と間
BUSY_RETRIES, BUSY_RETRY_MS = 5, 100


def _speak(message: str) -> None:
    """読み上げる（ui/speech.py）。**読ませた文と時刻、送った方法をアプリのログ（~/Library/Logs/LogicSampleLoader.log）
    に残す。** 「読まなかった」と言われたときに、アプリが何をいつ読ませたかを確かめられるように（2026-09-26）。"""
    how = speech.announce(message)
    line = message.replace("\n", " ")
    note = {speech.NATIVE: "", speech.FALLBACK: "（予備の方法）", speech.SILENT: "（音を止めている）"}.get(how, "")
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [読み上げ{note}] {line[:300]}", file=sys.stderr, flush=True)


def _focused_window():
    """いまフォーカスのある wx の部品（無ければ None）。"""
    return wx.Window.FindFocus()


def _after_key_delay(pressed_at: float | None) -> int:
    """メニューのキーを押した時刻から、読ませるまでに待つミリ秒。**押してから 800 ミリ秒はあける**
    （VoiceOver がまずメニューの項目名を読むため）。時刻が無ければ待たない。"""
    if pressed_at is None:
        return 0
    return max(0, PAD_STEP_DELAY_MS - int((time.monotonic() - pressed_at) * 1000))


def _guarded(job) -> Outcome:
    """別スレッドの用事を走らせる。**例外でも必ず Outcome を返す**（返さないと「操作中」のまま戻らなくなる）。"""
    try:
        return job()
    except Exception as exc:
        traceback.print_exc()          # ランチャのログ（~/Library/Logs/LogicSampleLoader.log）に残る
        return Outcome(False, tr(f"[エラー] 思わぬ理由で止まりました（{type(exc).__name__}: {exc}）。もう一度試してください。",
                                 f"[Error] Stopped for an unexpected reason ({type(exc).__name__}: {exc}). Please try again."))

KEY_HELP = """\
探す
  タブで欄を移動し、検索語の欄でリターンを押すと検索します。
  パックの欄は、パックの名前のあとに、中のサンプルの数を読みます。
  コマンド+F で、どこからでも検索語の欄へ戻れます。

タブで止まる場所
  タブは欄と一覧と「検索を実行」のボタンに止まります。ほかのボタンには止まりません（キーで同じことができます）。
  パックの欄からタブを 1 回押すと「検索を実行」です。リターンかスペースで検索します。
  読み込み先に入れるのは、一覧でリターン。
  読み込みの設定はコマンド+S、読み込み先を調べるのはコマンド+T、Flex はコマンド+シフト+F。
  ファイルを選んで読み込むのはコマンド+O。

一覧
  上下矢印で項目を移動します。文字キーを打つと、その頭文字の項目へ飛びます。
  スペースで試聴の再生と停止。元の音のまま鳴ります（キーもテンポも変えません）。
  シフト+スペースで、プロジェクトのテンポとキーに合わせた音を再生と停止。貼るときと同じ音です。
  リターンで、読み込み先に入れます（Quick Sampler、DMD のパッド、オーディオトラック）。

読み込み先を切り替える
  コマンド+1 で Quick Sampler の画面、コマンド+2 で DMD のパッド、コマンド+3 で Logic のオーディオトラック。
  コマンド+シフト+L で、この 3 つを順送りします。
  「読み込み先」の欄で選んでも同じです。切り替えると、何に入れるか（貼り付け先のトラックなど）を読みます。

読み込む前に
  Logic で読み込み先のトラックを選び、ハイフンで Quick Sampler の画面を開いておきます。
  素材は作業用フォルダに複製してから読み込みます。Splice の元のファイルは書き換わりません。

DMD のパッドへ読み込む
  「読み込み先」を「Drum Machine Designer のパッド」にします（コマンド+2）。
  Logic で DMD のトラックを選び、ハイフンで DMD の画面を開いておきます。インスペクタも表示しておきます。
  「パッド」の欄で入れる先を選び、一覧でリターン。読み込んだら次のパッドへ進みます（コマンド+S の設定で切れます）。
  空のパッドには Quick Sampler を入れてから読み込み、パッドの名前を素材の名前にします。
  読み込むあいだ、DMD の画面はいったん閉じて、また開きます。
  コマンド+右ブラケット（]）で次のパッド、コマンド+左ブラケット（[）で前のパッド。移ると、そのパッドの中身を読みます。
  日本語配列では、右ブラケットは L の 3 つ右（む のキー）、左ブラケットは P の 2 つ右（半濁点 ゜ のキー）です。
  コマンド+T でパッドの一覧を読み直します。

オーディオトラックに貼る（テンポとキーを合わせる）
  「読み込み先」を「Logic のオーディオトラック」にします（コマンド+3）。
  Logic で貼り付け先のオーディオトラックを選び、再生位置を貼りたい小節に置いておきます。
  一覧でリターンを押すと、素材の BPM とキーをプロジェクトのテンポとキーに合わせた音を作り、
  選んでいるトラックの、再生位置のある小節の頭に貼ります。
  貼ったあとの再生位置は、コマンド+S の設定で 3 つから選べます。
    貼ったオーディオの終わりへ進める（既定）。続けてリターンを押すと、ループが順に並びます。
    貼ったオーディオの頭に置く。すぐ再生して、貼った音を聴けます。
    動かさない。
  スペースの試聴は元の音、シフト+スペースの試聴は合わせた音です。シフト+スペースで聴いた音と同じものを貼ります。
  「テンポの合わせ方」の欄で選べます。基準はプロジェクトの BPM です。
    Even。プロジェクトの BPM に合わせます（既定）。
    ハーフ。プロジェクトの BPM の半分に合わせます。120 BPM のプロジェクトなら 60 BPM。
    ダブル。プロジェクトの BPM の倍に合わせます。120 BPM のプロジェクトなら 240 BPM。
  コマンド+シフト+T でも、ハーフ、Even、ダブルと順送りできます。
  「合わせるキー」の欄で、合わせるキーを選べます。既定はプロジェクトのキー（Logic のコントロールバーの Key Signature）。
  コマンド+K で、プロジェクトのキーに合わせるか、合わせない（素材のまま）かを切り替えられます。
  「移調」の欄で、さらに半音ずつ上げ下げできます。テンポの合わせ方、キー、移調は、アプリを閉じると元に戻ります。
  合わせた音は、作業用フォルダに「元の名前__120bpm_even_+3_Am」のような名前で置きます。
  ハーフなら 120bpm_half、ダブルなら 120bpm_double が付きます。Splice の元のファイルは書き換わりません。
  貼り付け先のトラックは、このアプリから選べます。
  コマンド+右ブラケット（]）で次のオーディオトラック、コマンド+左ブラケット（[）で前のオーディオトラック。
  ソフトウェア音源などのトラックは飛ばします。端まで行くと反対の端に戻ります。
  移ると、まずトラックの名前を読み、続けて、貼る小節が空いているか（何があるか）を読みます。
  Logic が遅れていてインスペクタが切り替わらないときは、そこで止めて、そう言います。コマンド+T で確かめ直せます。
  選ばれているトラックが 2 つ以上あるときは、Logic のインスペクタに出ているトラックに貼ります。
  「貼り付け先のトラック」の欄で、番号と名前から選ぶこともできます。
  コマンド+T で、プロジェクトのテンポ、キー、再生位置と、選んでいるトラック、トラックの一覧を読み直します。
  ソフトウェア音源のトラックには貼れません。新しいオーディオトラックは、Logic でオプション+コマンド+A。

コマンドキー
  コマンド+S で読み込みの設定（Original か Optimized、Flex、Follow Tempo、読み込んだあと、貼る位置、
  貼ったあとの再生位置、自動で試聴するか、次のパッドへ進むか、トラックに貼るときにテンポを合わせるか）。
  コマンド+T で読み込み先を調べ直す。
  コマンド+1、コマンド+2、コマンド+3 で読み込み先を切り替える。
  コマンド+シフト+L で、読み込み先を順送り。
  コマンド+シフト+T で、テンポの合わせ方をハーフ、Even、ダブルと順送り。
  コマンド+K で、キーをプロジェクトに合わせるか、合わせないかを切り替える。
  コマンド+シフト+F で、いまの Quick Sampler の Flex を切り替える。入れると、音程を変えても速さが変わりません。
  コマンド+シフト+P で Follow Tempo を切り替える。Flex を入れたときだけ効きます。
  コマンド+R で Splice の索引を更新。
  コマンド+C で選んでいる素材のパスをコピー。
  コマンド+I で検索結果一覧へ移動。
  コマンド+F で検索語の欄へ移動。
  コマンド+O で、索引に無いファイルを選んで読み込む。
  コマンド+シフト+G でパックの並び順を順送り。
    中身の多い順、名前順、更新の新しい順、追加した順の 4 つです。
  コマンド+シフト+K でこの一覧。
  コマンド+Q で終了。

操作メニューだけにあるもの（キーは割り当てていません）
  Splice のフォルダを選ぶ。Splice の素材が決まった場所に無いときに選び直します。選ぶと索引を作り直します。
  アクセシビリティの設定を開く。
  Keyboard Maestro の補助を入れる（配布版だけ）。Logic の中で使うキーのマクロを、Keyboard Maestro に渡します。

Logic の中で使うキー（Keyboard Maestro の補助）
  コントロール+シフト+L で、Quick Sampler のサンプル読み込みのメニューを開く。
  コントロール+シフト+T で Flex、コントロール+シフト+P で Follow Tempo を切り替える。

ボタン
  リターンでもスペースでも押せます。
  「検索を実行」のほかのボタンはタブでは止まりませんが、VoiceOver の移動で着けば押せます。
"""

# キー操作の一覧の英語（KEY_HELP と同じ並び）。記号のキーは名前で書く（VoiceOver が ] と [ を読まなかった）
KEY_HELP_EN = """\
Search
  Press Tab to move between fields. Press Return in the search field to search.
  The Pack field reads the pack name followed by the number of samples in it.
  Command-F brings you back to the search field from anywhere.

Where Tab stops
  Tab stops at the fields, the list and the Search button. It skips the other buttons (keys do the same things).
  From the Pack field, one Tab reaches Search. Press Return or Space to search.
  To load into the destination, press Return in the list.
  Load settings: Command-S. Check the destination: Command-T. Flex: Command-Shift-F.
  Choose a file and load it: Command-O.

The list
  Up and Down Arrow move between items. Type a letter to jump to items starting with it.
  Space plays and stops a preview of the original sound (key and tempo unchanged).
  Shift-Space plays and stops the sound matched to the project tempo and key. It is the same sound that gets pasted.
  Return loads into the destination (Quick Sampler, a DMD pad, or an audio track).

Switching the destination
  Command-1: the Quick Sampler window. Command-2: a DMD pad. Command-3: a Logic audio track.
  Command-Shift-L cycles through these three.
  Choosing in the Destination field does the same. After switching, the app says where samples will go (such as the paste track).

Before loading
  In Logic, select the track to load into and open Quick Sampler with the Hyphen key.
  Samples are copied into a work folder before loading. The original Splice files are never changed.

Loading into a DMD pad
  Set the Destination to Drum Machine Designer pad (Command-2).
  In Logic, select the DMD track, open DMD with the Hyphen key, and show the inspector.
  Choose the pad in the Pad field, then press Return in the list. After loading, the next pad is selected (you can turn this off with Command-S).
  An empty pad gets a Quick Sampler first; the pad is then named after the sample.
  While loading, the DMD window closes and opens again.
  Command-Right Bracket (]) moves to the next pad, Command-Left Bracket ([) to the previous pad. The app reads what is in the pad.
  Command-T reads the pad list again.

Pasting on an audio track (matched to tempo and key)
  Set the Destination to Logic audio track (Command-3).
  In Logic, select the audio track to paste on and put the playhead in the bar where you want it.
  Press Return in the list: the app makes a version of the sample matched to the project tempo and key,
  and pastes it at the start of the bar the playhead is in, on the selected track.
  Where the playhead goes after pasting can be chosen with Command-S:
    Move to the end of the pasted audio (default). Press Return again to line loops up one after another.
    Move to the start of the pasted audio, so you can play it right away.
    Don't move.
  Space previews the original sound, Shift-Space the matched sound. Shift-Space plays exactly what will be pasted.
  The Tempo mode field chooses how the tempo is matched. The basis is the project BPM.
    Even: the project BPM (default).
    Half: half the project BPM. 60 BPM in a 120 BPM project.
    Double: twice the project BPM. 240 BPM in a 120 BPM project.
  Command-Shift-T also cycles Half, Even and Double.
  The Match key field chooses the key. The default is the project key (the Key Signature in Logic's control bar).
  Command-K switches between matching the project key and not matching (keeping the sample's key).
  The Transpose field moves it further up or down in semitones. Tempo mode, key and transpose reset when the app closes.
  Matched files are stored in the work folder with names like "original name__120bpm_even_+3_Am".
  Half adds 120bpm_half, Double adds 120bpm_double. The original Splice files are never changed.
  You can choose the paste track from this app.
  Command-Right Bracket (]) selects the next audio track, Command-Left Bracket ([) the previous audio track.
  Software instrument tracks and other tracks are skipped. At either end it wraps around to the other end.
  After moving, the app reads the track name first, then whether the bar to paste in is empty (or what is there).
  If Logic lags and the inspector does not switch, the app stops there and says so. Command-T checks again.
  When two or more tracks are selected, the sample goes on the track shown in Logic's inspector.
  You can also choose by number and name in the Paste track field.
  Command-T reads the project tempo, key, playhead, the selected track and the track list again.
  Software instrument tracks cannot be pasted on. Create a new audio track in Logic with Option-Command-A.

Command keys
  Command-S: Load settings (Original or Optimized, Flex, Follow Tempo, after loading, where to paste,
  playhead after pasting, auto preview, moving to the next pad, matching tempo when pasting on a track).
  Command-T: check the destination again.
  Command-1, Command-2, Command-3: switch the destination.
  Command-Shift-L: cycle the destination.
  Command-Shift-T: cycle the tempo mode through Half, Even and Double.
  Command-K: switch between matching the project key and not matching.
  Command-Shift-F: toggle Flex on the current Quick Sampler. When on, changing the pitch does not change the speed.
  Command-Shift-P: toggle Follow Tempo. Works only when Flex is on.
  Command-R: update the Splice index.
  Command-C: copy the path of the selected sample.
  Command-I: move to the search results list.
  Command-F: move to the search field.
  Command-O: choose a file that is not in the index and load it.
  Command-Shift-G: cycle the pack order.
    Most samples first, by name, recently updated first, and order added.
  Command-Shift-K: this list.
  Command-Q: quit.

Only in the Actions menu (no keys assigned)
  Choose Splice Folder. Pick the folder again when your Splice samples are not in the usual place. The index is rebuilt.
  Open Accessibility Settings.
  Install Keyboard Maestro Helper (distribution version only). Sends the macros for the keys used inside Logic to Keyboard Maestro.

Keys inside Logic (Keyboard Maestro helper)
  Control-Shift-L opens the Quick Sampler sample load menu.
  Control-Shift-T toggles Flex, Control-Shift-P toggles Follow Tempo.

Buttons
  Both Return and Space press a button.
  Buttons other than Search are skipped by Tab, but you can press them when VoiceOver navigation lands on them.
"""


def _static(parent, text: str, *, name: str = "") -> wx.StaticText:
    """見出し・欄名の文字。**すべてのウィジェットに名前を付ける**（AX のダンプで調べられるように）。"""
    st = wx.StaticText(parent, label=text)
    st.SetName(name or text)
    return st


class _KeyedButton(wx.Button):
    """**タブで止まらない**ボタン。キーで同じことができるもの（入れる・設定・調べる・Flex・ファイルを選ぶ）。
    検索を実行はここに入れない（種別やパックを選んだあと、タブ 1 つで実行できるように）。

    タブの一周が 17 か所あり、ボタンを通るたびに読まれて、どこにいるか分からなくなった（2026-10-13 の指摘）。
    ボタンは残す（VoiceOver の移動やマウスでは押せる。名前も付けたまま）。
    """

    def AcceptsFocusFromKeyboard(self) -> bool:
        return False


class MainFrame(wx.Frame):
    """本体の窓。distribution は配布版（PyInstaller の .app）か。配布版だけのメニューの項目を出す。"""

    def __init__(self, controller: AppController, *, distribution: bool = FROZEN) -> None:
        super().__init__(None, title=APP_TITLE)
        self.controller = controller
        self._distribution = distribution
        # Logic を操作する処理が走っているか。**二重に押させない。**
        self._job_running = False
        # 窓が生きているか。別スレッドの結果を捨てる判断に使う
        self._alive = True
        self._build_menu()
        self._build_ui()
        self._refresh_pad_choice()     # 読み込み先に合わせて、使う欄だけを有効にする
        self._bind()
        self._fit_to_content()
        self._refresh_settings_button()
        # **読み込み先を調べるのは窓を出したあと。** 表示前に走らせると、その間まるごと無音になる。
        # 起動時は許可を求める画面を出さない（利用者が読み込もうとしたときだけ出す）
        wx.CallAfter(self._start_detect, False)
        # **起動したら、すぐタブで動けるようにする。** 何もしないと窓そのものにフォーカスが残り、タブが効かない
        # （2026-09-26 実測）。窓が前に出たときにも確かめる（Finder から開くと、窓を出したあとで前に出る）
        wx.CallAfter(self._focus_first)

    def _focus_first(self) -> None:
        """どの欄にもフォーカスが無ければ、最初の欄（検索語）に置く。**欄にあるときは動かさない。**"""
        if not self._alive:
            return
        focused = _focused_window()
        if focused is None or focused is self or focused is self._panel:
            self._text.SetFocus()

    def _on_activate(self, evt) -> None:
        evt.Skip()
        if evt.GetActive():
            wx.CallAfter(self._focus_first)

    # -- メニュー ---------------------------------------------------------

    def _build_menu(self) -> None:
        """メニューバー。**ショートカットには必ず修飾キーを付ける**（`Ctrl` は macOS で Cmd になる）。"""
        bar = wx.MenuBar()
        ops = wx.Menu()
        ids = ("focus_search", "settings", "load", "preview", "preview_fit", "tempo_scale", "key_match", "target_cycle", "detect", "flex", "follow", "pick", "copy",
               "rescan", "splice_root", "pack_order", "focus_list", "ax", "km_helper", "keys", "version",
               "next_pad", "prev_pad", "target_qs", "target_dmd", "target_track")
        self._ids = {name: wx.NewIdRef() for name in ids}
        i = self._ids
        ops.Append(i["settings"], tr("読み込みの設定", "Load Settings") + "\tCtrl+S",
                   tr("Original か Optimized、Flex、Follow Tempo", "Original or Optimized, Flex, Follow Tempo"))
        ops.Append(i["load"], tr("読み込み先に入れる", "Load into Destination"),
                   tr("選んでいる素材を、読み込み先に入れる（一覧でリターン）", "Load the selected sample (Return in the list)"))
        ops.Append(i["preview"], tr("試聴 元の音 再生/停止", "Preview Original Sound Play/Stop"),
                   tr("選んでいる素材を元の音のまま聴く（一覧でスペース）", "Hear the selected sample as it is (Space in the list)"))
        # シフト+スペースはアクセラレータにしない（文字の欄でシフト+スペースを打てなくなる）。一覧でだけ効く
        ops.Append(i["preview_fit"], tr("試聴 プロジェクトに合わせた音 再生/停止", "Preview Matched Sound Play/Stop"),
                   tr("テンポとキーをプロジェクトに合わせた音を聴く（一覧でシフト+スペース）",
                      "Hear it matched to the project tempo and key (Shift-Space in the list)"))
        ops.Append(i["tempo_scale"], tr("テンポの合わせ方を順送り", "Cycle Tempo Mode") + "\tCtrl+Shift+T",
                   tr("コマンド+シフト+T。ハーフ、Even、ダブル。基準はプロジェクトの BPM",
                      "Command-Shift-T. Half, Even, Double. Based on the project BPM"))
        ops.Append(i["key_match"], tr("キーをプロジェクトに合わせるかを切り替える", "Toggle Matching the Project Key") + "\tCtrl+K",
                   tr("コマンド+K。プロジェクトのキーに合わせる、合わせない（素材のまま）",
                      "Command-K. Match the project key, or keep the sample's key"))
        ops.AppendSeparator()
        ops.Append(i["detect"], tr("読み込み先を調べ直す", "Check Destination Again") + "\tCtrl+T",
                   tr("Logic で開いている Quick Sampler、DMD のパッド、プロジェクトのテンポとキーを見る",
                      "Look at the open Quick Sampler, DMD pads, and the project tempo and key in Logic"))
        # 読み込み先を切り替える（主画面の「読み込み先」の欄と同じ）。**VoiceOver はキーで項目名を読む**ので、
        # 項目名だけで何になるか分かるように書く
        ops.Append(i["target_qs"], tr("読み込み先を Quick Sampler にする", "Destination: Quick Sampler") + "\tCtrl+1",
                   tr("コマンド+1。Quick Sampler の画面に読み込む", "Command-1. Load into the Quick Sampler window"))
        ops.Append(i["target_dmd"], tr("読み込み先を DMD のパッドにする", "Destination: DMD Pad") + "\tCtrl+2",
                   tr("コマンド+2。Drum Machine Designer のパッドに読み込む", "Command-2. Load into a Drum Machine Designer pad"))
        ops.Append(i["target_track"], tr("読み込み先をオーディオトラックにする", "Destination: Audio Track") + "\tCtrl+3",
                   tr("コマンド+3。テンポとキーを合わせて、Logic のオーディオトラックに貼る",
                      "Command-3. Match tempo and key, then paste on a Logic audio track"))
        ops.Append(i["target_cycle"], tr("読み込み先を順送り", "Cycle Destination") + "\tCtrl+Shift+L",
                   tr("コマンド+シフト+L。Quick Sampler、DMD のパッド、オーディオトラックの順",
                      "Command-Shift-L. Quick Sampler, DMD pad, audio track"))
        ops.Append(i["flex"], tr("Flex を切り替える", "Toggle Flex") + "\tCtrl+Shift+F",
                   tr("入れると音程を変えても速さが変わらない", "When on, changing the pitch does not change the speed"))
        ops.Append(i["follow"], tr("Follow Tempo を切り替える", "Toggle Follow Tempo") + "\tCtrl+Shift+P",
                   tr("プロジェクトのテンポに合わせる", "Follow the project tempo"))
        ops.Append(i["pick"], tr("ファイルを選んで読み込む", "Choose File and Load") + "\tCtrl+O",
                   tr("コマンド+O。索引に無いファイル", "Command-O. A file that is not in the index"))
        # 項目名は読み込み先で変わる（DMD ならパッド、オーディオトラックならトラック。_refresh_step_items）。
        # **VoiceOver はキーを押すと項目名を読む**ので、いまの読み込み先の言葉にしておく
        ops.Append(i["next_pad"], tr("次のパッドへ", "Next Pad") + "\tCtrl+]",
                   tr("コマンド+右ブラケット。読み込み先が DMD のパッドのとき", "Command-Right Bracket. When the destination is a DMD pad"))
        ops.Append(i["prev_pad"], tr("前のパッドへ", "Previous Pad") + "\tCtrl+[",
                   tr("コマンド+左ブラケット。読み込み先が DMD のパッドのとき", "Command-Left Bracket. When the destination is a DMD pad"))
        ops.AppendSeparator()
        ops.Append(i["copy"], tr("パスをコピー", "Copy Path") + "\tCtrl+C", tr("選んでいる素材のパスを写す", "Copy the path of the selected sample"))
        ops.Append(i["rescan"], tr("Splice の索引を更新", "Update Splice Index") + "\tCtrl+R",
                   tr("買い足した素材を登録する", "Add newly downloaded samples"))
        ops.Append(i["splice_root"], tr("Splice のフォルダを選ぶ…", "Choose Splice Folder…"),
                   tr("Splice の素材が入っているフォルダを選び直す", "Choose the folder that holds your Splice samples"))
        ops.Append(i["pack_order"], tr("パックの並び順を順送り", "Cycle Pack Order") + "\tCtrl+Shift+G",
                   tr("中身の多い順、名前順、更新の新しい順、追加した順",
                      "Most samples first, by name, recently updated first, order added"))
        # **`Cmd+L` は使わない。** macOS と競合する（実測）
        ops.Append(i["focus_list"], tr("検索結果一覧へ移動", "Go to Search Results") + "\tCtrl+I",
                   tr("一覧にフォーカスを移す", "Move focus to the list"))
        ops.Append(i["focus_search"], tr("検索語の欄へ移動", "Go to Search Field") + "\tCtrl+F",
                   tr("コマンド+F。検索語の欄にフォーカスを移し、入っている語を選ぶ",
                      "Command-F. Move focus to the search field and select its text"))
        ops.Append(i["ax"], tr("アクセシビリティの設定を開く", "Open Accessibility Settings"),
                   tr("許可はご自身で入れてください", "Please turn the permission on yourself"))
        if self._distribution:
            # 手元の版は tools/install_qs_helper.py --import で入れる（python3.14 で動かす別のグループ）
            ops.Append(i["km_helper"], tr("Keyboard Maestro の補助を入れる…", "Install Keyboard Maestro Helper…"),
                       tr("Logic の中で使うキー（コントロール+シフト+L・T・P）のマクロを Keyboard Maestro に渡す",
                          "Send the macros for the keys used inside Logic (Control-Shift-L, T, P) to Keyboard Maestro"))
        ops.AppendSeparator()
        ops.Append(wx.ID_EXIT, tr("終了", "Quit") + "\tCtrl+Q")
        bar.Append(ops, tr("操作", "Actions"))

        help_menu = wx.Menu()
        help_menu.Append(i["keys"], tr("キー操作の一覧", "Keyboard Commands") + "\tCtrl+Shift+K")
        # **`wx.ID_ABOUT` は使わない。** macOS では自動でアプリメニューへ移り、ヘルプから辿れなくなる
        help_menu.Append(i["version"], tr("バージョン情報", "Version Information"),
                         tr("ビルドの日時とコミット", "Build date and commit"))
        bar.Append(help_menu, tr("ヘルプ", "Help"))
        self.SetMenuBar(bar)

    # -- 画面 -------------------------------------------------------------

    def _build_ui(self) -> None:
        # **素の Panel にしない。** 入りきらない欄が高さ 0 に潰れる
        panel = wx.ScrolledWindow(self, style=wx.TAB_TRAVERSAL | wx.VSCROLL)
        panel.SetScrollRate(0, 20)
        self._panel = panel
        outer = wx.BoxSizer(wx.VERTICAL)

        outer.Add(self._heading(panel, tr("素材を探す", "Find Samples")), 0, wx.ALL, 4)
        self._text = self._text_field(panel, outer, tr("検索語", "Search"))
        self._notes = note_names()
        self._key = self._choice(panel, outer, tr("キー", "Key"), [tr("すべてのキー", "All keys")] + self._notes)
        self._relative = wx.CheckBox(panel, label=tr("平行調も含める", "Include relative key"))
        label_control(self._relative, tr("平行調も含める", "Include relative key"))
        outer.Add(self._relative, 0, wx.ALL, 4)
        self._bpm = self._text_field(panel, outer, tr("BPM。空欄なら指定しない", "BPM. Leave empty for any"))
        self._kind = self._choice(panel, outer, tr("種別", "Type"), [name for name, _ in KIND_CHOICES])
        self._pack = self._choice(panel, outer, tr("パック", "Pack"), [tr("すべてのパック", "All packs")])
        # **検索を実行はタブで止まる。** 種別やパックをタブで選んだあと、そのまま実行できるように（2026-10-13 の指摘。
        # ほかのボタンと一緒にタブから外したら、検索語の欄まで戻らないと実行できず不便だった）
        self._search_btn = wx.Button(panel, label=tr("検索を実行", "Search"))
        label_control(self._search_btn, tr("検索を実行", "Search"))
        outer.Add(self._search_btn, 0, wx.ALL, 4)

        outer.Add(self._heading(panel, tr("検索結果一覧", "Search Results")), 0, wx.ALL, 4)
        self._results = wx.ListBox(panel, style=wx.LB_SINGLE)
        # **名前は短く。** タブで来るたびに読まれる（使い方はコマンド+シフト+K の一覧にある）
        label_control(self._results, tr("検索結果一覧", "Search Results"))
        self._results.SetMinSize((520, 260))
        outer.Add(self._results, 1, wx.EXPAND | wx.ALL, 4)

        outer.Add(self._heading(panel, tr("読み込む", "Load")), 0, wx.ALL, 4)
        self._target_values = list(TARGET_LABELS)
        self._target = self._choice(panel, outer, tr("読み込み先", "Destination"), [TARGET_LABELS[v] for v in self._target_values])
        current = self.controller.settings.target
        self._target.SetSelection(self._target_values.index(current) if current in self._target_values else 0)
        self._pad = self._choice(panel, outer, tr("パッド", "Pad"), self.controller.pad_labels())
        self._pad.SetSelection(self.controller.pad_index - 1)
        self._pad.Enable(self.controller.dmd_mode)
        self._track = self._choice(panel, outer, tr("貼り付け先のトラック", "Paste track"),
                                   [tr("まだ Logic から読んでいません", "Not read from Logic yet")])
        self._tempo_values = [value for _, value in TEMPO_CHOICES]
        self._tempo = self._choice(panel, outer, tr("テンポの合わせ方", "Tempo mode"), [label for label, _ in TEMPO_CHOICES])
        self._tempo.SetSelection(self._tempo_values.index(self.controller.tempo_scale))
        self._fit_key_values = [value for _, value in KEY_CHOICES]
        # 「キー」は検索の欄にもあるので、言葉を変えて聞き分けられるようにする
        self._fit_key = self._choice(panel, outer, tr("合わせるキー", "Match key"), [label for label, _ in KEY_CHOICES])
        self._fit_key.SetSelection(self._fit_key_values.index(self.controller.key_choice))
        self._transpose_values = [value for _, value in TRANSPOSE_CHOICES]
        self._transpose = self._choice(panel, outer, tr("移調", "Transpose"), [label for label, _ in TRANSPOSE_CHOICES])
        self._transpose.SetSelection(self._transpose_values.index(self.controller.transpose))
        self._load_btn = _KeyedButton(panel, label=tr("Quick Sampler に読み込む", "Load into Quick Sampler"))
        label_control(self._load_btn, tr("Quick Sampler に読み込む", "Load into Quick Sampler"))
        outer.Add(self._load_btn, 0, wx.ALL, 4)
        self._settings_btn = _KeyedButton(panel, label=tr("読み込みの設定", "Load Settings"))
        outer.Add(self._settings_btn, 0, wx.ALL, 4)
        self._target_btn = _KeyedButton(panel, label=tr("読み込み先を調べる", "Check Destination"))
        label_control(self._target_btn, tr("読み込み先を調べる", "Check Destination"))
        outer.Add(self._target_btn, 0, wx.ALL, 4)
        self._flex_btn = _KeyedButton(panel, label=tr("Flex を切り替える", "Toggle Flex"))
        label_control(self._flex_btn, tr("Flex を切り替える", "Toggle Flex"))
        outer.Add(self._flex_btn, 0, wx.ALL, 4)
        self._file_btn = _KeyedButton(panel, label=tr("ファイルを選んで読み込む", "Choose File and Load"))
        label_control(self._file_btn, tr("ファイルを選んで読み込む", "Choose File and Load"))
        outer.Add(self._file_btn, 0, wx.ALL, 4)

        self._status = _static(panel, tr("準備完了", "Ready"), name=tr("状態", "Status"))
        outer.Add(self._status, 0, wx.ALL, 4)

        panel.SetSizer(outer)
        panel.FitInside()
        # **スクロールバーは出さない**（スクロールはできる。フォーカスのある欄へは自動で動く）。出ていると、
        # タブの一周に「スクロールバー 0.75」という止まり場所が 1 つ増える（2026-10-13 に実測）
        panel.ShowScrollbars(wx.SHOW_SB_NEVER, wx.SHOW_SB_NEVER)
        self._outer = outer
        self._reload_packs()

    def _heading(self, parent, text: str) -> wx.StaticText:
        st = _static(parent, text)
        font = st.GetFont()
        font.MakeBold()
        st.SetFont(font)
        return st

    def _text_field(self, parent, sizer, label: str) -> wx.TextCtrl:
        sizer.Add(_static(parent, label), 0, wx.LEFT | wx.TOP, 4)
        ctrl = wx.TextCtrl(parent, style=wx.TE_PROCESS_ENTER)
        label_control(ctrl, label)
        sizer.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
        return ctrl

    def _choice(self, parent, sizer, label: str, choices: list[str]) -> wx.Choice:
        sizer.Add(_static(parent, label), 0, wx.LEFT | wx.TOP, 4)
        ctrl = wx.Choice(parent, choices=choices)
        ctrl.SetSelection(0)
        label_control(ctrl, label)
        sizer.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
        return ctrl

    def _fit_to_content(self) -> None:
        """**大きさは中身から決めて、画面の作業領域で頭打ちにする。**"""
        need = self._outer.GetMinSize()
        display = wx.Display.GetFromWindow(self)
        area = wx.Display(display if display >= 0 else 0).GetClientArea()
        self.SetSize((min(need.width + 40, area.width), min(need.height + 60, int(area.height * 0.92))))
        self.SetMinSize((360, 320))
        # **作業領域の中に置く。** 大きさだけ決めると、窓の上端が 213 の高さに置かれ、下の 100 ピクセルほど
        # （ファイルを選んで読み込むのボタンと状態の行）が画面の外に出ていた（1920x1080 の画面、2026-10-13 に実測）
        self.CentreOnScreen()

    # -- 配線 -------------------------------------------------------------

    def _bind(self) -> None:
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=wx.ID_EXIT)
        i = self._ids
        for name, handler in (
            ("settings", self._on_settings), ("load", self._on_load), ("preview", self._on_preview),
            ("preview_fit", self._on_preview_fit), ("tempo_scale", self._on_tempo_key),
            ("key_match", self._on_key_match_key), ("target_cycle", self._on_target_cycle),
            ("detect", self._on_detect), ("flex", self._on_flex), ("follow", self._on_follow),
            ("pick", self._on_pick_file), ("copy", self._on_copy), ("rescan", self._on_rescan),
            ("pack_order", self._on_pack_order), ("focus_list", self._on_focus_list),
            ("focus_search", self._on_focus_search), ("splice_root", self._on_choose_splice),
            ("km_helper", self._on_km_helper),
            ("ax", self._on_accessibility), ("keys", self._on_keys), ("version", self._on_version),
            ("next_pad", self._on_next_pad), ("prev_pad", self._on_prev_pad),
            ("target_qs", lambda e: self._on_target_key("qs")), ("target_dmd", lambda e: self._on_target_key("dmd")),
            ("target_track", lambda e: self._on_target_key("track")),
        ):
            self.Bind(wx.EVT_MENU, handler, id=i[name])
        self._search_btn.Bind(wx.EVT_BUTTON, self._on_search)
        self._text.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._bpm.Bind(wx.EVT_TEXT_ENTER, self._on_search)
        self._load_btn.Bind(wx.EVT_BUTTON, self._on_load)
        self._settings_btn.Bind(wx.EVT_BUTTON, self._on_settings)
        self._target_btn.Bind(wx.EVT_BUTTON, self._on_detect)
        self._flex_btn.Bind(wx.EVT_BUTTON, self._on_flex)
        self._file_btn.Bind(wx.EVT_BUTTON, self._on_pick_file)
        self._target.Bind(wx.EVT_CHOICE, self._on_target_change)
        self._pad.Bind(wx.EVT_CHOICE, self._on_pad_change)
        self._track.Bind(wx.EVT_CHOICE, self._on_track_change)
        self._tempo.Bind(wx.EVT_CHOICE, self._on_tempo_change)
        self._fit_key.Bind(wx.EVT_CHOICE, self._on_fit_key_change)
        self._transpose.Bind(wx.EVT_CHOICE, self._on_transpose_change)
        self._results.Bind(wx.EVT_LISTBOX, self._on_select)
        self._results.Bind(wx.EVT_LISTBOX_DCLICK, self._on_load)
        self._results.Bind(wx.EVT_SET_FOCUS, self._on_list_focus)
        # **キー処理は 1 か所に集める。** 部品ごとに書くと取りこぼす
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.Bind(wx.EVT_ACTIVATE, self._on_activate)

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        """窓全体のキー処理。**修飾なしの文字キーは横取りしない**（一覧で頭文字へ飛べなくなる）。"""
        focused = wx.Window.FindFocus()
        if (isinstance(focused, wx.Button) and evt.GetModifiers() == wx.MOD_NONE
                and evt.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER)):
            press_button(focused)
            return
        if focused is self._results and evt.GetModifiers() == wx.MOD_NONE:
            key = evt.GetKeyCode()
            if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
                self._on_load(evt)
                return
            if key == wx.WXK_SPACE:
                self._on_preview(evt)
                return
        if (focused is self._results and evt.GetModifiers() == wx.MOD_SHIFT
                and evt.GetKeyCode() == wx.WXK_SPACE):
            self._on_preview_fit(evt)
            return
        evt.Skip()

    # -- 値の取り出し ------------------------------------------------------

    def _search_form(self) -> SearchForm:
        key_index = self._key.GetSelection()
        kind_index = self._kind.GetSelection()
        return SearchForm(
            text=self._text.GetValue().strip(),
            key="" if key_index <= 0 else self._notes[key_index - 1],
            relative=self._relative.GetValue(),
            bpm=self._bpm.GetValue().strip(),
            kind=KIND_CHOICES[max(kind_index, 0)][1],
            pack=self._chosen_pack(),
        )

    def _selected_index(self) -> int | None:
        index = self._results.GetSelection()
        return None if index == wx.NOT_FOUND else index

    # -- 探す・聴く ---------------------------------------------------------

    def _reload_packs(self, keep: str = "") -> None:
        """パックの欄を作り直す。**パック名の横に中のサンプルの数を出す。** keep のパックを選び直す。"""
        choices = self.controller.pack_choices()
        self._pack_names = [name for _, name in choices]
        self._pack.Set([label for label, _ in choices])
        self._pack.SetSelection(self._pack_names.index(keep) if keep in self._pack_names else 0)

    def _chosen_pack(self) -> str:
        index = self._pack.GetSelection()
        names = getattr(self, "_pack_names", [])
        return names[index] if 0 <= index < len(names) else ""

    def _on_search(self, _evt) -> None:
        play_sound("start")
        outcome = self.controller.search(self._search_form())
        self._report(outcome)
        self._results.Set(self.controller.result_labels())
        if self.controller.results:
            self._results.SetSelection(0)

    def _on_select(self, _evt) -> None:
        """一覧で選び直したとき。「自動で試聴」が入っていれば、**元の音**を鳴らす（どの読み込み先でも）。"""
        index = self._selected_index()
        path = self.controller.result_path(index) if index is not None else None
        outcome = self.controller.preview_selected(path)
        if outcome is not None and outcome.ok:
            # 前の素材の合わせた音を作っている途中なら、それは鳴らさない（選び直した素材の元の音を遮らない）
            self._cancel_fitted_preview()
        if outcome is not None and not outcome.ok:
            self._report(outcome)

    def _cancel_fitted_preview(self) -> None:
        """作りかけの合わせた音（シフト+スペース）を、できあがっても鳴らさない。作ること自体は止めない。"""
        self._preview_token = getattr(self, "_preview_token", 0) + 1

    def _on_preview(self, _evt) -> None:
        """スペース。**元の音のまま**鳴らす（どの読み込み先でも。2026-10-13 の要望）。"""
        self._cancel_fitted_preview()
        index = self._selected_index()
        path = self.controller.result_path(index) if index is not None else None
        self._report(self.controller.toggle_preview(path), sound=False)

    def _on_preview_fit(self, _evt) -> None:
        """シフト+スペース。テンポとキーをプロジェクトに合わせた音を鳴らす（貼るときと同じ音）。"""
        index = self._selected_index()
        if index is None:
            self._report(tr("一覧から素材を選んでください。", "Choose a sample in the list."), ok=False)
            return
        self._start_fitted_preview(index, toggle=True)

    def _start_fitted_preview(self, index: int, *, toggle: bool) -> None:
        """テンポとキーを合わせた音を作って鳴らす（シフト+スペース）。**作るのは別のスレッド。**

        シフト+スペースをもう一度押したら止める。**作るのは 1 つずつ**: 作っている間に別の素材を頼まれたら、
        いま作っているものが終わってから、いちばん新しい頼みだけを作る（前の結果は捨てる）。
        """
        entry = self.controller.result_entry(index)
        if entry is None:
            self._report(tr("一覧から素材を選んでください。", "Choose a sample in the list."), ok=False)
            return
        if toggle:
            stopped = self.controller.stop_if_previewing(entry.path)
            if stopped is not None:
                self._report(stopped, sound=False)
                return
        self._preview_token = getattr(self, "_preview_token", 0) + 1
        self.controller.stop_preview()
        if getattr(self, "_preview_running", False):
            self._preview_pending = (entry, self._preview_token)
            return
        self._run_fitted_preview(entry, self._preview_token)

    def _run_fitted_preview(self, entry, token: int) -> None:
        self._preview_running = True
        kind = self.controller.kind_of(entry)
        pack = getattr(entry, "pack", "") or ""

        def work() -> None:
            try:
                # 音を作るときだけ合図の音を鳴らす（作ってあれば、すぐ鳴るので要らない）
                fitted, note = self.controller.fitted_preview(entry.path, entry, kind, pack,
                                                              on_render=lambda: wx.CallAfter(play_sound, "start"))
            except Exception as exc:
                traceback.print_exc()
                fitted, note = None, tr(f"[エラー] 試聴の音を作れませんでした（{type(exc).__name__}: {exc}）。",
                                        f"[Error] Could not make the preview sound ({type(exc).__name__}: {exc}).")
            wx.CallAfter(self._finish_fitted_preview, token, fitted, entry.path, note)

        threading.Thread(target=work, daemon=True).start()

    def _finish_fitted_preview(self, token: int, fitted: str | None, source: str, note: str) -> None:
        self._preview_running = False
        pending, self._preview_pending = getattr(self, "_preview_pending", None), None
        if not self._alive:
            return
        if pending is not None and pending[1] == getattr(self, "_preview_token", 0):
            self._run_fitted_preview(*pending)      # いちばん新しい頼みを作る（いまの結果は古いので捨てる）
            return
        if token != getattr(self, "_preview_token", 0):
            return
        if not fitted:
            if note:
                self._report(note, ok=False)
            return
        outcome = self.controller.play_fitted(fitted, source)
        if note:
            self._report(Outcome(outcome.ok and not note.startswith(WARNING), note + outcome.message_ja), sound=False)
        else:
            self._report(outcome, sound=False)

    def _on_list_focus(self, evt) -> None:
        evt.Skip()
        if self._results.GetCount() and self._results.GetSelection() == wx.NOT_FOUND:
            self._results.SetSelection(0)

    def _on_focus_list(self, _evt) -> None:
        """Cmd+I。一覧へ移る。**どこからでも戻れるようにする。**"""
        if not self.controller.results:
            self._report(tr("検索結果がありません。先に検索してください。", "There are no search results. Search first."), ok=False)
            return
        self._results.SetFocus()
        self._on_list_focus(wx.FocusEvent())

    def _on_focus_search(self, _evt) -> None:
        """コマンド+F。検索語の欄へ。**どこにいるか分からなくなったときの戻り場所**（2026-10-13 の要望）。
        入っている語は選んでおく（そのまま打てば置き換わる）。欄の名前は VoiceOver がフォーカスの移動で読む。"""
        self._text.SetFocus()
        self._text.SelectAll()

    def _on_copy(self, _evt) -> None:
        index = self._selected_index()
        path = self.controller.result_path(index) if index is not None else None
        if path is None:
            self._report(tr("一覧から素材を選んでください。", "Choose a sample in the list."), ok=False)
            return
        if wx.TheClipboard.Open():
            wx.TheClipboard.SetData(wx.TextDataObject(path))
            wx.TheClipboard.Close()
            self._report(tr(f"パスをコピーしました。{Path(path).name}", f"Path copied. {Path(path).name}"), ok=True)
            return
        self._report(tr("[警告] クリップボードを開けませんでした。", "[Warning] Could not open the clipboard."), ok=False)

    def _on_rescan(self, _evt) -> None:
        play_sound("start")
        self._report(tr("索引を更新しています。少しお待ちください。", "Updating the index. Please wait."), sound=False)
        wx.Yield()
        outcome = self.controller.rescan_library()
        if outcome.ok:
            self._reload_packs(keep=self._chosen_pack())
        self._report(outcome)

    def _on_pack_order(self, _evt) -> None:
        """Cmd+Shift+G。**読み上げは並べ替えより先**（自動読み上げに上書きされないように）。"""
        outcome = self.controller.cycle_pack_order()
        self._report(outcome)
        self._reload_packs(keep=self._chosen_pack())

    def _on_choose_splice(self, _evt) -> None:
        """操作メニューの「Splice のフォルダを選ぶ」。選んだら、そのフォルダで索引を作り直す（2026-10-24。配布に向けて）。

        Splice のフォルダは利用者が動かせる（Splice のアプリの設定）。決まった場所に無いと、検索が何も見つけない。
        """
        with wx.DirDialog(self, tr("Splice の素材が入っているフォルダを選ぶ", "Choose the folder that holds your Splice samples"),
                          defaultPath=str(self.controller.splice_root),
                          style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                self._report(tr("選ばずに閉じました。", "Closed without choosing."), sound=False, delay_ms=ANNOUNCE_DELAY_MS)
                return
            path = dialog.GetPath()
        outcome = self.controller.set_splice_root(path)
        if not outcome.ok:
            self._report(outcome, delay_ms=ANNOUNCE_DELAY_MS)
            return
        self._results.Set([])
        play_sound("start")
        self._report(outcome.message_ja + tr("索引を作り直しています。少しお待ちください。", " Rebuilding the index. Please wait."),
                     sound=False)
        wx.Yield()
        rescan = self.controller.rescan_library()
        self._reload_packs()
        self._report(rescan)

    # -- 設定 -------------------------------------------------------------

    def _on_settings(self, _evt) -> None:
        with SettingsDialog(self, self.controller.settings) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                self._report(tr("設定を変えずに閉じました。", "Closed without changing settings."), ok=True, sound=False,
                             delay_ms=ANNOUNCE_DELAY_MS)
                return
            new = dialog.result()
        self.controller.update_settings(new)
        self._refresh_settings_button()
        self._report(tr(f"設定を変えました。{describe(new)}。", f"Settings changed. {describe(new)}."), ok=True,
                     delay_ms=ANNOUNCE_DELAY_MS)

    def _refresh_settings_button(self) -> None:
        """設定のボタンの名前は「読み込みの設定」だけにする。

        2026-10-12 までは、いまの設定を全部名前に入れていた（74 字）。タブで通るたびに読まれ、
        どのボタンにいるのか分からなくなった（2026-10-13 の指摘）。設定は変えたときに読み上げ、
        コマンド+S の画面の欄でも聞ける。
        """
        self._set_button_name(self._settings_btn, tr("読み込みの設定", "Load Settings"))

    def _set_button_name(self, button: wx.Button, name: str) -> None:
        button.SetLabel(name)
        button.SetName(name)
        label_control(button, name)
        self._panel.Layout()

    # -- 読み込み先とパッド -------------------------------------------------

    def _refresh_pad_choice(self) -> None:
        """パッドの欄を今の一覧で作り直す。**選んでいるパッドは保つ。** 読み込み先に合わせて、使う欄だけを有効にする。"""
        self._pad.Set(self.controller.pad_labels())
        self._pad.SetSelection(self.controller.pad_index - 1)
        self._pad.Enable(self.controller.dmd_mode)
        track = self.controller.track_mode
        self._track.Enable(track)
        self._tempo.Enable(track)
        self._fit_key.Enable(track)
        self._transpose.Enable(track)
        self._flex_btn.Enable(not track)
        self._refresh_step_items()
        if track:
            load = tr("オーディオトラックに貼る", "Paste on Audio Track")
        elif self.controller.dmd_mode:
            load = tr("DMD のパッドに読み込む", "Load into DMD Pad")
        else:
            load = tr("Quick Sampler に読み込む", "Load into Quick Sampler")
        self._set_button_name(self._load_btn, load)
        self._refresh_settings_button()

    def _on_target_change(self, _evt) -> None:
        # **VoiceOver が欄の新しい値を読み終えてから、結果を読む**（ほかの欄と同じく 800 ミリ秒あける。
        # それまではすぐ読ませていて、欄の値の読み上げを遮っていた。2026-10-24 のレビュー）
        pressed_at = time.monotonic()
        index = self._target.GetSelection()
        target = self._target_values[index] if 0 <= index < len(self._target_values) else "qs"
        outcome = self.controller.set_target(target)
        self._refresh_pad_choice()
        if self.controller.dmd_mode or self.controller.track_mode:
            # パッドの一覧・プロジェクトを Logic から読む（別スレッド）。結果は読み込み先のボタンと読み上げで伝える
            self._start_detect(True, pressed_at=pressed_at, brief=True)
        else:
            self._report(outcome, sound=False, delay_ms=_after_key_delay(pressed_at))

    # キーで切り替えたときに先に言う言葉（そのあとに、Logic から読み直した結果を続ける）
    _TARGET_SWITCHED = {"qs": tr("読み込み先を Quick Sampler の画面にしました。", "Destination: Quick Sampler window. "),
                        "dmd": tr("読み込み先を DMD のパッドにしました。", "Destination: DMD pad. "),
                        "track": tr("読み込み先をオーディオトラックにしました。", "Destination: audio track. ")}

    def _on_target_key(self, target: str) -> None:
        """コマンド+1・2・3。読み込み先を切り替える（「読み込み先」の欄で選ぶのと同じ）。

        **VoiceOver がメニューの項目名を読み終えてから結果を読む**（押してから 800 ミリ秒あける）。
        DMD のパッドとオーディオトラックは、Logic から読み直して、何に入れるか（貼り付け先のトラックなど）まで言う。
        """
        pressed_at = time.monotonic()
        if target in self._target_values:
            self._target.SetSelection(self._target_values.index(target))
        outcome = self.controller.set_target(target)
        self._refresh_pad_choice()
        if not outcome.ok or target == "qs":
            self._report(outcome, sound=not outcome.ok, delay_ms=_after_key_delay(pressed_at))
            return
        self._start_detect(True, pressed_at=pressed_at, prefix=self._TARGET_SWITCHED[target], brief=True)

    def _on_target_cycle(self, _evt) -> None:
        """コマンド+シフト+L。読み込み先を Quick Sampler → DMD のパッド → オーディオトラック → … と順送りする。"""
        order = list(self._target_values)
        current = self.controller.settings.target
        nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
        self._on_target_key(nxt)

    def _refresh_step_items(self) -> None:
        """メニューの「次の…へ」「前の…へ」の言葉を、いまの読み込み先にそろえる（キーは同じ）。"""
        bar = self.GetMenuBar()
        if bar is None:
            return
        if self.controller.track_mode:
            words = ((tr("次のトラックへ", "Next Track"),
                      tr("コマンド+右ブラケット。次のオーディオトラックを貼り付け先にする",
                         "Command-Right Bracket. Make the next audio track the paste track")),
                     (tr("前のトラックへ", "Previous Track"),
                      tr("コマンド+左ブラケット。前のオーディオトラックを貼り付け先にする",
                         "Command-Left Bracket. Make the previous audio track the paste track")))
        else:
            words = ((tr("次のパッドへ", "Next Pad"),
                      tr("コマンド+右ブラケット。読み込み先が DMD のパッドのとき", "Command-Right Bracket. When the destination is a DMD pad")),
                     (tr("前のパッドへ", "Previous Pad"),
                      tr("コマンド+左ブラケット。読み込み先が DMD のパッドのとき", "Command-Left Bracket. When the destination is a DMD pad")))
        for name, (label, help_text), key in zip(("next_pad", "prev_pad"), words, ("Ctrl+]", "Ctrl+[")):
            item = bar.FindItemById(self._ids[name])
            if item is not None and item.GetItemLabel() != f"{label}\t{key}":
                item.SetItemLabel(f"{label}\t{key}")
                item.SetHelp(help_text)

    def _refresh_track_choice(self) -> None:
        """「貼り付け先のトラック」の欄を、いまの一覧で作り直す。選んでいるトラックに合わせる。"""
        labels = self.controller.track_labels()
        if not labels:
            return
        if list(self._track.GetItems()) != labels:
            self._track.Set(labels)
        row = self.controller.selected_track_row()
        if row is not None and self._track.GetSelection() != row:
            self._track.SetSelection(row)

    def _on_track_change(self, _evt) -> None:
        """欄でトラックを選んだら、Logic でそのトラックを選ぶ（貼り付け先にする）。

        **いま選ばれているはずの行でも選び直す**（アプリが覚えている選択は、Logic で選び直すと古くなる）。
        VoiceOver が欄の新しい値を読み終えてから結果を読む（メニューのキーと同じく 800 ミリ秒あける）。
        """
        row = self._track.GetSelection()
        if row == wx.NOT_FOUND:
            return
        self._start_quick_job(lambda: self.controller.choose_track(row), pressed_at=time.monotonic())

    def _on_pad_change(self, _evt) -> None:
        # 欄の値が変わったことは VoiceOver が読むので、ここでは読み上げない（重ねない）
        self.controller.set_pad(self._pad.GetSelection() + 1)

    def _on_tempo_change(self, _evt) -> None:
        index = self._tempo.GetSelection()
        if 0 <= index < len(self._tempo_values):
            self.controller.set_tempo_scale(self._tempo_values[index])   # 読み上げは VoiceOver に任せる

    def _on_tempo_key(self, _evt) -> None:
        """コマンド+シフト+T。ハーフ → Even → ダブルと順送りする。"""
        pressed_at = time.monotonic()
        outcome = self.controller.cycle_tempo_scale()
        self._report_fit_choice(outcome, self._tempo, self._tempo_values, self.controller.tempo_scale, pressed_at)

    def _on_key_match_key(self, _evt) -> None:
        """コマンド+K。キーをプロジェクトに合わせるか、合わせないかを切り替える。"""
        pressed_at = time.monotonic()
        outcome = self.controller.toggle_key_match()
        self._report_fit_choice(outcome, self._fit_key, self._fit_key_values, self.controller.key_choice, pressed_at)

    def _report_fit_choice(self, outcome: Outcome, field: wx.Choice, values: list, value, pressed_at: float) -> None:
        """キーで合わせ方を変えたあと: 欄も同じ値にし、**メニューの項目名を読み終えてから**結果を読む。
        オーディオトラック以外が読み込み先のときは、どこに効くかを添える（いまの読み込み先には効かないので）。"""
        if value in values:
            field.SetSelection(values.index(value))
        if outcome.ok and not self.controller.track_mode:
            outcome = Outcome(True, outcome.message_ja + tr("オーディオトラックに貼るときと、シフト+スペースの試聴に効きます。",
                                                            " Applies when pasting on an audio track and to the Shift-Space preview."))
        self._report(outcome, sound=not outcome.ok, delay_ms=_after_key_delay(pressed_at))

    def _on_fit_key_change(self, _evt) -> None:
        index = self._fit_key.GetSelection()
        if 0 <= index < len(self._fit_key_values):
            self.controller.set_key_choice(self._fit_key_values[index])   # 読み上げは VoiceOver に任せる

    def _on_transpose_change(self, _evt) -> None:
        index = self._transpose.GetSelection()
        if 0 <= index < len(self._transpose_values):
            self.controller.set_transpose(self._transpose_values[index])

    def _step_pad(self, delta: int) -> None:
        if self.controller.track_mode:
            # オーディオトラックのときは、次・前のオーディオトラックを貼り付け先にする（Logic で選ぶので別スレッド）
            self._start_quick_job(lambda: self.controller.step_track(delta), pressed_at=time.monotonic())
            return
        if not self.controller.dmd_mode:
            self._report(tr("読み込み先が DMD のパッドか、オーディオトラックのときに使えます。",
                            "Available when the destination is a DMD pad or an audio track."), ok=False)
            return
        outcome = self.controller.step_pad(delta)
        self._pad.SetSelection(self.controller.pad_index - 1)
        # VoiceOver がメニューの項目名を読み終えてから、パッドの中身を読ませる
        self._report(outcome, sound=False, delay_ms=PAD_STEP_DELAY_MS)

    def _on_next_pad(self, _evt) -> None:
        self._step_pad(1)

    def _on_prev_pad(self, _evt) -> None:
        self._step_pad(-1)

    # -- Logic を操作する ----------------------------------------------------

    def _start_quick_job(self, job, *, pressed_at: float | None = None) -> None:
        """Logic のトラックを選ぶだけの短い用事。**このアプリを前に置いたまま**、別スレッドで走らせる。

        pressed_at（メニューのキーを押した時刻）を渡すと、そこから 800 ミリ秒以上あけて結果を読む
        （VoiceOver が先にメニューの項目名を読むため。2026-09-25 の指摘）。
        """
        if self._job_running:
            self._report(tr("いま Logic を操作しています。終わるまでお待ちください。",
                             "Logic is being operated now. Please wait until it finishes."), ok=False)
            return
        if self.controller.busy:
            tries = getattr(self, "_quick_tries", 0)
            if tries < BUSY_RETRIES:      # 試聴がプロジェクトを読んでいるだけなら、すぐ空く
                self._quick_tries = tries + 1
                wx.CallLater(BUSY_RETRY_MS, self._start_quick_job, job, pressed_at=pressed_at)
                return
            self._quick_tries = 0
            self._report(tr("いま Logic を操作しています。終わるまでお待ちください。",
                             "Logic is being operated now. Please wait until it finishes."), ok=False)
            return
        self._quick_tries = 0
        self._job_running = True
        self._quick_timer = wx.CallLater(QUICK_JOB_SOUND_MS, play_sound, "start")

        def work() -> None:
            outcome = _guarded(job)
            wx.CallAfter(self._finish_quick_job, outcome, pressed_at)

        threading.Thread(target=work, daemon=True).start()

    def _finish_quick_job(self, outcome: Outcome, pressed_at: float | None) -> None:
        self._job_running = False
        timer = getattr(self, "_quick_timer", None)
        if timer is not None and timer.IsRunning():
            timer.Stop()
        if not self._alive:
            return
        # 読み上げを先に（欄やボタンの書き換えの読み上げに上書きされないように）。貼れないトラックだけ失敗の音
        self._report(outcome, sound=not outcome.ok, delay_ms=_after_key_delay(pressed_at))
        self._refresh_track_choice()
        self._set_target_name(outcome)

    def _start_job(self, job, *, returning: str) -> None:
        """Logic を操作する処理を別スレッドで走らせる。**同時に 1 つだけ。**

        returning は、終わったあとこのアプリへ戻るか。
        "setting" は読み込みの設定（読み込んだあと）に従う。"always" は必ず戻る
        （Flex の切り替えのように、このアプリでキーを押して頼んだ用事）。
        """
        if self._job_running:
            self._report(tr("いま Logic を操作しています。終わるまでお待ちください。",
                             "Logic is being operated now. Please wait until it finishes."), ok=False)
            return
        if self.controller.busy:
            tries = getattr(self, "_job_tries", 0)
            if tries < BUSY_RETRIES:      # 試聴がプロジェクトを読んでいるだけなら、すぐ空く
                self._job_tries = tries + 1
                wx.CallLater(BUSY_RETRY_MS, self._start_job, job, returning=returning)
                return
            self._job_tries = 0
            self._report(tr("いま Logic を操作しています。終わるまでお待ちください。",
                             "Logic is being operated now. Please wait until it finishes."), ok=False)
            return
        self._job_tries = 0
        self._job_running = True
        play_sound("start")
        threading.Thread(target=self._job_worker, args=(job, returning), daemon=True).start()

    def _job_worker(self, job, returning: str) -> None:
        """別スレッド。**wx には触らない。** 例外でも必ず _finish_job へ戻る（戻らないと「操作中」のまま）。"""
        outcome = _guarded(job)
        if returning == "always":
            returned = quick_sampler.return_to_self()
        else:
            returned = self.controller.return_after_load()
        # 読み込み先のボタンの名前を新しくしておく（Tab で戻ったときに今の状態が聞こえる）
        target = _guarded(self.controller.detect_target) if outcome.ok else None
        wx.CallAfter(self._finish_job, outcome, returned, target)

    def _finish_job(self, outcome: Outcome, returned: bool, target: Outcome | None) -> None:
        """UI のスレッドで呼ばれる。"""
        self._job_running = False
        if not self._alive:
            return
        if returned:
            self.Raise()
            if self.controller.results:
                self._results.SetFocus()
        # **読み上げはボタンを書き換える前**（自動読み上げに上書きされないように）
        self._report(outcome, delay_ms=RETURN_DELAY_MS if returned else 0)
        if target is not None:
            self._set_target_name(target)
        if self.controller.dmd_mode:
            self._refresh_pad_choice()
        if self.controller.track_mode:
            self._refresh_track_choice()

    def _on_load(self, _evt) -> None:
        index = self._selected_index()
        if index is None:
            self._report(tr("一覧から素材を選んでください。", "Choose a sample in the list."), ok=False)
            return
        self._start_job(lambda: self.controller.load_entry(index), returning="setting")

    def _on_pick_file(self, _evt) -> None:
        """索引に無いファイルも読み込めるようにする（試聴はできない）。"""
        with wx.FileDialog(self, tr("読み込むファイルを選ぶ", "Choose a file to load"),
                           wildcard=tr("音声ファイル", "Audio files") + "|*.wav;*.aif;*.aiff;*.caf;*.flac;*.mp3;*.m4a|"
                           + tr("すべて", "All files") + "|*.*",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                self._report(tr("選ばずに閉じました。", "Closed without choosing."), sound=False, delay_ms=ANNOUNCE_DELAY_MS)
                return
            path = dialog.GetPath()
        self._start_job(lambda: self.controller.load_path(path), returning="setting")

    def _on_detect(self, _evt) -> None:
        self._start_detect(True)

    def _start_detect(self, announce_it: bool, *, pressed_at: float | None = None, prefix: str = "",
                      brief: bool = False) -> None:
        """読み込み先を Logic から読み直す（別スレッド）。prefix は結果の前に言う言葉（キーで読み込み先を切り替えたとき）。
        pressed_at（メニューのキーを押した時刻）を渡すと、そこから 800 ミリ秒以上あけて読む。
        brief は短く言う（切り替えたとき。オーディオトラックなら「貼り付け先は Audio 1。120 BPM、Cメジャー」）。"""
        if not announce_it and not quick_sampler.is_trusted():
            self._set_target_name(Outcome(False, tr("アクセシビリティの許可がまだありません", "No accessibility permission yet")))
            return
        if self._job_running or self.controller.busy:
            if prefix:      # 切り替えたことだけは伝える（黙ると、切り替わったか分からない）
                self._report(prefix + tr("いま Logic を操作しているので、読み直していません。終わったらコマンド+T で読み直してください。",
                                         "Logic is being operated, so nothing was read. Press Command-T when it finishes."),
                             sound=False, delay_ms=_after_key_delay(pressed_at))
            return
        self._job_running = True
        threading.Thread(target=self._detect_worker, args=(announce_it, pressed_at, prefix, brief), daemon=True).start()

    def _detect_worker(self, announce_it: bool, pressed_at: float | None = None, prefix: str = "",
                       brief: bool = False) -> None:
        outcome = _guarded(lambda: self.controller.detect_target(brief))
        wx.CallAfter(self._finish_detect, outcome, announce_it, pressed_at, prefix)

    def _finish_detect(self, outcome: Outcome, announce_it: bool, pressed_at: float | None = None,
                       prefix: str = "") -> None:
        self._job_running = False
        if not self._alive:
            return
        if announce_it:
            spoken = Outcome(outcome.ok, prefix + outcome.message_ja) if prefix else outcome
            self._report(spoken, delay_ms=_after_key_delay(pressed_at))
        self._set_target_name(outcome)
        if self.controller.dmd_mode:
            self._refresh_pad_choice()
        if self.controller.track_mode:
            self._refresh_track_choice()

    def _set_target_name(self, outcome: Outcome) -> None:
        """「読み込み先を調べる」のボタンの名前に、**いまの入れ先か、調べられなかった理由を短く**添える。

        2026-10-12 までは調べた結果を丸ごと入れていて、148 字あった。タブで通るたびに 10 秒を超えて読まれた
        （2026-10-13 に実測）。詳しいことはコマンド+T で読む。
        """
        self._set_button_name(self._target_btn, tr("読み込み先を調べる", "Check Destination") + self._target_state(outcome))
        if self.controller.track_mode:
            self._refresh_fit_key_label()

    def _target_state(self, outcome: Outcome) -> str:
        """「。いまは Audio 1」「。いまは Deluxe Classic（貼れません）」「。いまは パッド 5」、
        調べられなかったときは理由（「。Logic Pro が起動していません」）。

        **調べられなかったときに、前に読んだ「いまは …」を残さない**（Logic が落ちていても「いまは Audio 1」と言い続けた）。
        「貼れません」は、操作の成否（「いま Logic を操作しています」で断られたなど）ではなく、読んだトラックの状態で決める。
        """
        text = outcome.message_ja
        reason = text.removeprefix(f"{ERROR} ").split(PERIOD)[0][:30]
        project = self.controller.project
        track = getattr(project, "track_name", "") if self.controller.track_mode and project is not None else ""
        lead = tr("。", ". ")
        if text.startswith(ERROR) or (not outcome.ok and not track):
            return f"{lead}{reason}" if reason else ""
        if self.controller.track_mode:
            if not track:
                return f"{lead}{reason}" if reason else ""
            return lead + tr(f"いまは {track}", f"Now {track}") + (
                "" if self.controller.track_pasteable else tr("（貼れません）", " (cannot paste)"))
        if self.controller.dmd_mode:
            return lead + tr(f"いまは パッド {self.controller.pad_index}", f"Now pad {self.controller.pad_index}")
        short = text.removeprefix(tr("読み込み先は ", "Destination: ")).split(PERIOD)[0].split(tr("（", " ("))[0][:30]
        return lead + tr(f"いまは {short}", f"Now {short}") if short else ""

    def _refresh_fit_key_label(self) -> None:
        """「プロジェクトのキーに合わせる」の項目に、いまのプロジェクトのキーを添える（読み直したとき）。"""
        project = self.controller.project
        query = logic_key_query(getattr(project, "key", "") if project is not None else "")
        label = KEY_CHOICES[0][0] + (tr(f"（いまは {key_label(query)}）", f" (now {key_label(query)})") if query else "")
        if self._fit_key.GetString(0) != label:
            self._fit_key.SetString(0, label)

    def _on_flex(self, _evt) -> None:
        self._start_job(lambda: self.controller.change_flex("flex"), returning="always")

    def _on_follow(self, _evt) -> None:
        self._start_job(lambda: self.controller.change_flex("follow"), returning="always")

    def _on_accessibility(self, _evt) -> None:
        quick_sampler.request_trust_prompt()
        if quick_sampler.open_accessibility_settings():
            self._report(tr("システム設定のアクセシビリティを開きました。一覧で Logicサンプルローダー を入れてください。",
                            "Opened Accessibility in System Settings. Turn on Logic Sample Loader in the list."),
                         ok=True)
        else:
            self._report(tr("[エラー] システム設定を開けませんでした。", "[Error] Could not open System Settings."), ok=False)

    def _on_km_helper(self, _evt) -> None:
        """操作メニューの「Keyboard Maestro の補助を入れる」（配布版だけ）。入っていないマクロだけを渡す。"""
        ok, message = km_helper.install(sys.executable)
        self._report(Outcome(ok, sentence(message)))

    # -- ヘルプ -----------------------------------------------------------

    def _on_keys(self, _evt) -> None:
        self._report(tr(KEY_HELP, KEY_HELP_EN), sound=False)

    def _on_version(self, _evt) -> None:
        self._report(version_text(), sound=False)

    def _on_close(self, evt) -> None:
        # **閉じたあとに別スレッドの結果で触らない**
        self._alive = False
        self.controller.stop_preview()
        self.controller.clear_previews()     # 試聴のために作った音（貼ったものは作業用フォルダに残る）
        evt.Skip()

    # -- 読み上げ ---------------------------------------------------------

    def _report(self, message, *, ok: bool | None = None, sound: bool = True, delay_ms: int = 0) -> None:
        """結果を伝える。**音 → 読み上げ → 画面**の順。`Outcome` を渡せば成否から音を選ぶ。"""
        if isinstance(message, Outcome):
            if ok is None:
                ok = message.ok
            message = message.message_ja
        if not message:
            return
        if sound:
            if ok is None:
                ok = not message.startswith((ERROR, WARNING))
            play_sound("success" if ok else "failure")
        if delay_ms:
            wx.CallLater(delay_ms, _speak, message)
        else:
            _speak(message)
        self._status.SetLabel(message.replace("\n", " "))


def version_text() -> str:
    """`.app` の Info.plist からビルドの日時とコミットを読む。

    **どの `.app` から起動したかは環境変数で受け取る**（`LOGICLOADER_APP`。ランチャが渡す）。
    ランチャは `.app` の外のプロジェクトの `app.py` を exec するので、`__file__` を辿っても
    `.app` に行き着かない（実際に踏んだ）。
    """
    bundle = os.environ.get("LOGICLOADER_APP", "").strip()
    plist = Path(bundle) / "Contents" / "Info.plist" if bundle else None
    if plist is None or not plist.is_file():
        return tr(f"ソースから直接起動しています。ビルドの日時を見るには .app から起動してください。{ABOUT_AI}",
                  f"Running directly from source. Open the .app to see the build date. {ABOUT_AI}")
    try:
        info = plistlib.loads(plist.read_bytes())
    except Exception:
        return tr("バージョン情報を読めませんでした。", "Could not read the version information.")
    unknown = tr("不明", "unknown")
    version = info.get('CFBundleShortVersionString', '?')
    date, commit = info.get('LLBuildDate', unknown), info.get('LLGitCommit', unknown)
    # 名前は表示の言語で言う（Info.plist の CFBundleDisplayName は日本語の名前）
    return tr(f"{info.get('CFBundleDisplayName', APP_TITLE)}、バージョン {version}。"
              f"ビルド {date}、コミット {commit}。{ABOUT_AI}",
              f"{APP_TITLE}, version {version}. Build {date}, commit {commit}. {ABOUT_AI}")


# バージョン情報の終わりに添える。**AI で作ったことを書く**（2026-10-24 の利用者の指示。配布物の文書と同じ）
ABOUT_AI = tr("作者は Takahashi。AI（Anthropic の Claude）で作りました。",
              "By Takahashi. Made with AI (Anthropic's Claude).")
