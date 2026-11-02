"""Logicサンプルローダーの入口。

    python3.14 app.py

Splice の素材を**聴きながら選んで**、Logic で開いている Quick Sampler に読み込む。

このファイルのある場所が自動で `sys.path` に入るので、どのカレントディレクトリからでも起動できる。

### 起動のしかたは 3 つ

| 引数 | すること |
| --- | --- |
| なし | 画面を出す（ふつうの起動） |
| `--helper …` | 画面を作らず、補助（`tools/qs_sample_menu.py`）だけを動かす。**配布版の Keyboard Maestro のマクロが使う**（配布版には python が無いので、.app の中の実行ファイルで動かす） |
| `--selftest` | 窓を出さずに組み立てて、借りた部品がそろっているかを 1 行で言う（配布版を作るときの確認） |

### 配布版（PyInstaller で固めた .app。2026-10-24 から）

手元の版はランチャ（.app の中のシェルスクリプト）がログの行き先と .app の場所を決めるが、配布版にはランチャが
無いので、ここで同じことをする（`_frozen_setup`）。同梱した借り物の .py を読み込んでも、**.app の中に
`__pycache__` を書かない**（書くと署名が壊れ、Finder から開いても無反応になる）。

### macOS でアプリ名を出す

`python app.py` で起動すると、macOS はアプリ名を「Python」と表示する。**`wx.App` を作る前に**
NSBundle の情報辞書を書き換えると、アプリメニューの表示名が変わる。wx が作るアプリメニューは
「Quit …」のように英語のまま残るので、NSMenu の項目名も直接書き換える
（wxPython のアプリで確立した手順）。

### 表示の言語（2026-10-27 から）

Mac の言語の設定に合わせて、日本語か英語で出す（`core/i18n.py`）。英語のときはアプリ名を
「Logic Sample Loader」にし、アプリメニューは wx の英語のまま残す。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# アプリの名前（表示の言語で変える。ログの場所や設定の場所は変えない）
APP_NAME = "Logicサンプルローダー"
APP_NAME_EN = "Logic Sample Loader"
FROZEN = bool(getattr(sys, "frozen", False))
LOG_PATH = Path.home() / "Library" / "Logs" / "LogicSampleLoader.log"

if FROZEN:
    sys.dont_write_bytecode = True          # .app の中に __pycache__ を書かない（署名が壊れる）
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _frozen_setup() -> None:
    """配布版だけ。手元の版でランチャがしていること（ログの行き先・.app の場所）をここでする。"""
    from core.version import VERSION

    bundle = Path(sys.executable).resolve().parents[2]       # …/Logicサンプルローダー.app/Contents/MacOS/実行ファイル
    os.environ.setdefault("LOGICLOADER_APP", str(bundle))    # バージョン情報が Info.plist を読む
    try:
        # Keyboard Maestro の補助を入れてあれば、マクロが呼ぶスクリプトを、いまの .app の場所に合わせる
        # （ログを開けなくても行う。ログより先に済ませる）
        from integration import km_helper
        km_helper.refresh_script(sys.executable)
    except Exception:
        pass
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stdout = sys.stderr = log
    print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} 起動（配布版 {VERSION}、{bundle}）", flush=True)


def run_helper(argv: list[str]) -> int:
    """`--helper …`: 補助（qs_sample_menu）を動かす。**画面は作らない。** 標準出力の 1 行を Keyboard Maestro が読み上げる。"""
    from integration import quick_sampler

    qs = quick_sampler.engine()
    if qs is None:
        from core.i18n import tr
        print(tr("Logicサンプルローダーの補助を読み込めません", "Cannot load the Logic Sample Loader helper"))
        return 1
    return int(qs.main(argv) or 0)


def selftest() -> int:
    """`--selftest`: 窓を出さずに組み立てて、借りた部品と補助がそろっているかを確かめる。**音も読み上げも出さない。**

    配布版を作るとき（tools/build_release.py）に、組み上げた .app の中の実行ファイルで動かす。
    設定と作業用フォルダは一時フォルダ（利用者の設定に触らない）。
    """
    import json
    import tempfile

    os.environ["LOGICLOADER_SILENT"] = "1"
    os.environ["SM_SILENT"] = "1"
    from core.sibling import FROZEN as frozen, load_ptcore
    from core.version import VERSION
    from integration import quick_sampler
    from ui.borrowed import missing_borrowed

    problems = list(missing_borrowed())
    qs = quick_sampler.engine()
    if qs is None:
        problems.append("補助（qs_sample_menu）")
    for name in ("library", "splicename", "audiofile", "prep", "dsp"):
        if load_ptcore(name) is None:
            problems.append(f"ptcore.{name}")
    # 補助と借り物は関数の中で読むので、読み込めるかをここで確かめる（配布版で取りこぼすと、その機能だけが黙って止まる）
    for name in ("objc", "Foundation", "AppKit", "ApplicationServices", "Quartz", "CoreFoundation", "just_playback"):
        try:
            __import__(name)
        except Exception as exc:
            problems.append(f"{name}（{type(exc).__name__}: {str(exc)[:120]}）")
    import wx
    from ui.controller import AppController
    from ui.main_frame import MainFrame

    with tempfile.TemporaryDirectory() as name:     # 設定と作業用フォルダの置き場所（終わったら消す）
        tmp = Path(name)
        app = wx.App(False)
        frame = MainFrame(AppController(settings_path=tmp / "s.json", stage_root=tmp / "stage", preview_root=tmp / "pv"))
        if frame.GetMenuBar() is None:
            problems.append("メニュー")
        frame.Destroy()
        del app
    print(json.dumps({"ok": not problems, "problems": problems, "version": VERSION, "frozen": frozen,
                      "helper": getattr(qs, "VERSION", ""), "python": sys.version.split()[0]}, ensure_ascii=False))
    return 0 if not problems else 1


def _set_app_name(name: str) -> None:
    """メニューバーに出るアプリ名を変える。**`wx.App` より先に呼ぶ。** 失敗しても起動は止めない。"""
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSBundle
    except ImportError:
        return
    try:
        bundle = NSBundle.mainBundle()
        if bundle is None:
            return
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
    except Exception:
        pass


def _localize_app_menu(name: str) -> None:
    """アプリメニューの項目名を日本語にする。**窓を出したあとに呼ぶ。** 英語の画面では呼ばない（wx の英語のまま）。"""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApp
    except ImportError:
        return
    try:
        main_menu = NSApp().mainMenu()
        if main_menu is None or main_menu.numberOfItems() == 0:
            return
        app_menu = main_menu.itemAtIndex_(0).submenu()
        if app_menu is None:
            return
        rules = (
            ("Quit", f"{name}を終了"),
            ("Hide Others", "ほかを隠す"),
            ("Hide", f"{name}を隠す"),
            ("Show All", "すべてを表示"),
            ("Services", "サービス"),
            ("About", f"{name}について"),
            ("Preferences", "設定…"),
        )
        for i in range(app_menu.numberOfItems()):
            item = app_menu.itemAtIndex_(i)
            title = str(item.title() or "")
            for prefix, replacement in rules:
                if title.startswith(prefix):
                    item.setTitle_(replacement)
                    break
    except Exception:
        pass


def build_frame():
    """本番と同じ道筋で窓を組み立てる（ビルドの起動確認もこれを通す）。"""
    from ui.controller import AppController
    from ui.main_frame import MainFrame

    return MainFrame(AppController())


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--helper"]:
        return run_helper(argv[1:])
    if argv[:1] == ["--selftest"]:
        return selftest()
    if FROZEN:
        _frozen_setup()
    import wx

    from core.i18n import LANG

    name = APP_NAME_EN if LANG == "en" else APP_NAME
    _set_app_name(name)
    app = wx.App(False)
    app.SetAppName(name)
    app.SetAppDisplayName(name)
    try:
        app.locale = wx.Locale(wx.LANGUAGE_ENGLISH if LANG == "en" else wx.LANGUAGE_JAPANESE)
    except Exception:
        pass
    frame = build_frame()
    frame.Show()
    if LANG != "en":
        _localize_app_menu(name)
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 表示の言語: {LANG}", file=sys.stderr, flush=True)
    app.MainLoop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
