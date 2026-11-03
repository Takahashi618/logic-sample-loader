"""作者の別のプロジェクトの部品を借りる（探し方は `core/sibling.py`）。

| 借りるもの | 借り先 | 何をするか |
| --- | --- | --- |
| `ax_label.label_control` | `speech_and_preview/ui/` | macOS で読み上げ名を確実に付ける（`SetName` は入力欄に効かない） |
| `announce.announce` | `speech_and_preview/ui/` | 画面を見ない利用者へ能動的に読み上げる |
| `player.PreviewPlayer` | `speech_and_preview/audio/` | 試聴。**スペース 1 つで再生と停止**を切り替える |
| `sounds.play` | `speech_and_preview/ui/` | 操作の結果を短い音で知らせる（成功・失敗・開始） |
| `wx_keys` | `speech_and_preview/ui/` | ボタンを Enter でも押せるようにする、ダイアログの Enter / Escape |
| `kinds` | `sample_kinds/core/` | 種別（長さを優先して判定）とパックの並び順 |
| `notes.NOTE_NAMES` | `sample_kinds/core/` | 検索のキー欄に並べる音名 |

**同じ操作感にすることに意味がある。** 作者の別のアプリと使い分けるので、試聴のキーや言葉が揃っていないと
持ち替えるたびに迷う。

**`sys.path` に足す方式は使えない。** あちらのパッケージ名が `ui` と `core` で、
このプロジェクトの同名のパッケージと衝突する。ファイルの場所を指定して読む。

**見つからなくても落ちない。** 読み上げ名が付かないだけで、アプリは起動する。
ただしビルドは、借りられない部品があれば止める（`missing_borrowed`）。
この利用者にとって読み上げは必須なので、欠けたまま「ビルド成功」と言わない。

### 静かにする

`LOGICLOADER_SILENT=1` で、読み上げと効果音を両方止める（試験や、Claude が代わりに
動かして確かめるとき用）。**既定は鳴らす**（利用者が普段使うとき）。効果音は借り先の
`SM_SILENT` でも止まる。
"""

from __future__ import annotations

import os
from types import ModuleType

from core.sibling import find_sibling, load_file_module

__all__ = [
    "label_control",
    "announce",
    "make_player",
    "play_sound",
    "press_button",
    "handle_dialog_enter_escape",
    "kinds",
    "note_names",
    "borrowed_from",
    "missing_borrowed",
    "is_silent",
]

SILENT_ENV = "LOGICLOADER_SILENT"


def is_silent() -> bool:
    return os.environ.get(SILENT_ENV, "") not in ("", "0", "false", "False")


def _load(project: str, marker: str, subdir: str, name: str) -> ModuleType | None:
    base = find_sibling(project, marker)
    if base is None:
        return None
    return load_file_module(base / subdir / f"{name}.py", f"_borrowed_{project}_{name}")


# 借り先は中立の名前で呼ぶ（手元の本当のフォルダ名は sibling_dirs.json。core/sibling.py）
_SM = ("speech_and_preview", "ui/ax_label.py")
_SFZ = ("sample_kinds", "core/kinds.py")

_ax_label = _load(*_SM, "ui", "ax_label")
_announce = _load(*_SM, "ui", "announce")
_player = _load(*_SM, "audio", "player")
_sounds = _load(*_SM, "ui", "sounds")
_wx_keys = _load(*_SM, "ui", "wx_keys")
kinds = _load(*_SFZ, "core", "kinds")
_notes = _load(*_SFZ, "core", "notes")

# 借りているものの一覧（診断用）。**ビルドの工程 1 がここを見る。**
_BORROWED: dict[str, ModuleType | None] = {
    "ax_label（読み上げ名）": _ax_label,
    "announce（能動読み上げ）": _announce,
    "player（試聴）": _player,
    "sounds（効果音）": _sounds,
    "wx_keys（Enter でボタンを押す）": _wx_keys,
    "kinds（種別とパックの並び順）": kinds,
    "notes（音名）": _notes,
}


def borrowed_from() -> str:
    """借りてきた場所。診断用（バージョン情報で読み上げる）。"""
    sm = find_sibling(*_SM)
    sfz = find_sibling(*_SFZ)
    return f"{sm or '見つからない'}、{sfz or '見つからない'}"


def missing_borrowed() -> list[str]:
    """借りられなかった部品の名前。**全部借りられていれば空。**"""
    return [name for name, module in _BORROWED.items() if module is None]


def note_names() -> list[str]:
    if _notes is not None:
        return list(_notes.NOTE_NAMES)
    return ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def make_player():
    """試聴プレイヤーを作る。**借りられなければ None**（読み込みは試聴なしでもできる）。"""
    if _player is None:
        return None
    try:
        return _player.PreviewPlayer()
    except Exception:
        return None


def label_control(ctrl, label: str) -> None:
    """読み上げ名を付ける。**借りられなければ wx の標準だけで済ませる。**"""
    if _ax_label is not None:
        _ax_label.label_control(ctrl, label)
        return
    try:
        ctrl.SetName(label)
    except Exception:
        pass


def announce(message: str) -> None:
    """能動的に読み上げる。**借りられなければ何もしない**（画面の文字は出ている）。"""
    if not message or is_silent() or _announce is None:
        return
    _announce.announce(message)


def play_sound(kind: str) -> None:
    """短い通知音（`success` / `failure` / `start`）。**読み上げより先に鳴らす。**"""
    if is_silent() or _sounds is None:
        return
    try:
        _sounds.play(kind)
    except Exception:
        pass


def press_button(button) -> None:
    """ボタンをキー操作から押したことにする（macOS の wx は既定ボタン以外を Enter で押せない）。"""
    if _wx_keys is not None:
        try:
            _wx_keys.press_button(button)
            return
        except Exception:
            pass
    import wx

    click = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    click.SetEventObject(button)
    button.GetEventHandler().ProcessEvent(click)


def handle_dialog_enter_escape(dialog, evt) -> bool:
    """ダイアログ共通の Enter / Escape 処理。処理したら True。"""
    if _wx_keys is None:
        return False
    try:
        return bool(_wx_keys.handle_dialog_enter_escape(dialog, evt))
    except Exception:
        return False
