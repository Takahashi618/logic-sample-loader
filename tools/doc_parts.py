#!/usr/bin/env python3.14
"""マニュアルのうち「コードから作る部分」を作り直す。

    python3.14 -X utf8 tools/doc_parts.py           日本語と英語のマニュアル（docs/manual.html・manual_en.html）を
                                                    今のコードに合わせ、txt も作り直す
    python3.14 -X utf8 tools/doc_parts.py --check   合わせる必要があるかだけ調べる（あれば 1 で終わる。書き換えない）

**英語のマニュアルの表は、英語の画面から作る**（2026-10-27 から）。画面の言語は起動したときに決まるので、
言語ごとに別のプロセスで動かす（`--lang ja` / `--lang en`。付けなければ両方を順に）。

キーの一覧・設定の項目と既定値・タブで止まる欄の並びは、手で書き写すと必ずずれる。**本物の窓とメニューと設定の画面を作って読み**、
文書の印の間に差し込む。印は HTML のコメントなので、読み上げにも txt にも出ない:

    <!-- gen:keys -->
    …ここが作り直される…
    <!-- /gen:keys -->

版の番号は <span class="version">…</span> と <title> を core/version.py に合わせる。

★窓は配布版と同じ形（distribution=True）で作る。Logic にも Splice にも触らず、設定は一時フォルダ。
★文書にある印を生成側が知らなければ止める（綴り違いの印は、黙って古い中身のまま残る）。
"""

from __future__ import annotations

import argparse
import html
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MANUAL = DOCS / "manual.html"
MANUAL_TXT = DOCS / "manual.txt"

os.environ["LOGICLOADER_SILENT"] = "1"
os.environ["SM_SILENT"] = "1"
# 文書の言語 → (正本の HTML, 作る txt)。**画面もその言語で作る**（Mac の言語に左右されない。core/i18n.py）
MANUALS = {"ja": (MANUAL, MANUAL_TXT), "en": (DOCS / "manual_en.html", DOCS / "manual_en.txt")}
LANG = "ja"
if "--lang" in sys.argv[1:-1]:
    LANG = sys.argv[sys.argv.index("--lang") + 1]
os.environ["LOGICLOADER_LANG"] = LANG
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from core.version import VERSION  # noqa: E402

MARK_RE = re.compile(r"(<!-- gen:(?P<name>[a-z_]+) -->)(?P<body>.*?)(<!-- /gen:(?P=name) -->)", re.S)
VERSION_SPAN_RE = re.compile(r'(<span class="version">)[^<]*(</span>)')
TITLE_RE = re.compile(r"(<title>Logicサンプルローダー マニュアル（)[^）<]*(）)")
TITLE_RE_EN = re.compile(r"(<title>Logic Sample Loader Manual \()[^)<]*(\))")

# wx のアクセラレータの書き方 → 読み上げる名前（macOS の wx では Ctrl がコマンド）。**記号のキーはカタカナの名前も**
KEY_WORDS = {"Ctrl": "コマンド", "Shift": "シフト", "Alt": "オプション", "RawCtrl": "コントロール",
             "]": "右ブラケット（]）", "[": "左ブラケット（[）"}
# メニューの項目名だけでは足りないもの（読み込み先で意味が変わる）
ACTION_OVERRIDES = {
    "Ctrl+]": "次のパッドへ（読み込み先が DMD のパッドのとき）。次のオーディオトラックを貼り付け先に（オーディオトラックのとき）",
    "Ctrl+[": "前のパッドへ（読み込み先が DMD のパッドのとき）。前のオーディオトラックを貼り付け先に（オーディオトラックのとき）",
}
# 英語の画面の言い方（記号のキーは名前も。VoiceOver が ] と [ を読まなかった）
KEY_WORDS_EN = {"Ctrl": "Command", "Shift": "Shift", "Alt": "Option", "RawCtrl": "Control",
                "]": "Right Bracket (])", "[": "Left Bracket ([)"}
ACTION_OVERRIDES_EN = {
    "Ctrl+]": "Next pad (when the destination is a DMD pad). Next audio track as the paste track (when it is an audio track)",
    "Ctrl+[": "Previous pad (when the destination is a DMD pad). Previous audio track as the paste track (when it is an audio track)",
}
FIELD_WHEN_EN = {
    "Pad": "Only when the destination is a DMD pad",
    "Paste track": "Only when the destination is an audio track",
    "Tempo mode": "Only when the destination is an audio track",
    "Match key": "Only when the destination is an audio track",
    "Transpose": "Only when the destination is an audio track",
}
# タブで止まる欄のうち、読み込み先によって使えないもの
FIELD_WHEN = {
    "パッド": "読み込み先が DMD のパッドのときだけ",
    "貼り付け先のトラック": "読み込み先がオーディオトラックのときだけ",
    "テンポの合わせ方": "読み込み先がオーディオトラックのときだけ",
    "合わせるキー": "読み込み先がオーディオトラックのときだけ",
    "移調": "読み込み先がオーディオトラックのときだけ",
}


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def key_display(accel: str) -> str:
    if LANG == "en":
        return "-".join(KEY_WORDS_EN.get(token, token) for token in accel.split("+") if token)
    return "+".join(KEY_WORDS.get(token, token) for token in accel.split("+") if token)


def count_word(n: int) -> str:
    """「3 つ」「11 項目」のように数える（10 以上を「11 つ」としない）。"""
    return f"{n} つ" if n < 10 else f"{n} 項目"


def table(lead: str, head: tuple, rows: list[tuple]) -> str:
    """表の前に「何の表か・何行・どの列か」を段落で言う（caption は txt で表のあとに出るので使わない）。"""
    if LANG == "en":
        quoted = [f"“{h}”" for h in head]
        cols = ", ".join(quoted[:-1]) + f" and {quoted[-1]}" if len(quoted) > 1 else quoted[0]
        intro = f"{esc(lead)} The table has {len(rows)} rows and {len(head)} columns: {esc(cols)}."
    else:
        cols = "".join(f"「{h}」" for h in head)
        intro = f"{esc(lead)}表は {len(rows)} 行で、列は{cols}の {count_word(len(head))}です。"
    lines = [f"<p>{intro}</p>",
             "<table>", "<thead><tr>" + "".join(f'<th scope="col">{esc(h)}</th>' for h in head) + "</tr></thead>",
             "<tbody>"]
    lines += ["<tr>" + "".join(f"<td>{esc(c)}</td>" for c in row) + "</tr>" for row in rows]
    lines += ["</tbody>", "</table>"]
    return "\n".join(lines)


def read_ui() -> dict:
    """本物の窓・メニュー・設定の画面を作って読む（窓は出さない）。"""
    import wx

    from core.settings import CHOICES, LoadSettings
    from tests.test_controller import FakeEngine, FakeLibrary
    from ui.controller import AppController
    from ui.main_frame import MainFrame
    from ui.settings_dialog import FIELDS, SettingsDialog

    tmp = Path(tempfile.mkdtemp())
    app = wx.App(False)
    controller = AppController(library_factory=lambda: FakeLibrary([]), engine=lambda: FakeEngine(),
                               trusted=lambda: True, settings_path=tmp / "s.json", stage_root=tmp / "st",
                               preview_root=tmp / "pv")
    frame = MainFrame(controller, distribution=True)
    try:
        keys, menu_only = [], []
        bar = frame.GetMenuBar()
        for i in range(bar.GetMenuCount()):
            for item in bar.GetMenu(i).GetMenuItems():
                if item.IsSeparator():
                    continue
                label, _tab, accel = item.GetItemLabel().partition("\t")
                if accel:
                    keys.append((accel, label))
                else:
                    menu_only.append((bar.GetMenuLabel(i), label, item.GetHelp()))
        fields = []
        for child in frame._panel.GetChildren():
            # スクロールバーは出していない（ShowScrollbars で隠している）ので、タブでは止まらない
            if isinstance(child, (wx.StaticText, wx.ScrollBar)) or not child.AcceptsFocusFromKeyboard():
                continue
            fields.append(child.GetName())
        dialog = SettingsDialog(frame, LoadSettings())
        try:
            defaults = LoadSettings()
            sep, on, off = ("／", "入れる", "切る") if LANG == "ja" else (" / ", "On", "Off")
            settings = [(label, sep.join(CHOICES[key].values()), CHOICES[key][getattr(defaults, key)])
                        for key, label in FIELDS]
            for box in (dialog._auto_preview, dialog._pad_advance, dialog._fit_tempo):
                settings.append((box.GetLabel(), f"{on}{sep}{off}", on if box.GetValue() else off))
        finally:
            dialog.Destroy()
    finally:
        frame.Destroy()
        del app
    return {"keys": keys, "menu_only": menu_only, "fields": fields, "settings": settings}


def build_parts(ui: dict) -> dict[str, str]:
    if LANG == "en":
        return build_parts_en(ui)
    key_rows = [(key_display(accel), ACTION_OVERRIDES.get(accel, label)) for accel, label in ui["keys"]]
    menu_rows = [(menu, label, help_ or "") for menu, label, help_ in ui["menu_only"]]
    field_rows = [(str(n), name, FIELD_WHEN.get(name, "いつでも")) for n, name in enumerate(ui["fields"], 1)]
    return {
        "keys": table("メニューに割り当てたキーです。どの欄にいても効きます。", ("キー", "動作"), key_rows),
        "menu_only": table("キーを割り当てていない、メニューだけの項目です。VO+M でメニューバーへ移り、選びます。",
                           ("メニュー", "項目", "説明"), menu_rows),
        "tabs": table("タブで止まる欄を、止まる順に並べました。シフト+タブで逆の順に戻ります。",
                      ("順", "欄の名前", "使えるとき"), field_rows),
        "settings": table("コマンド+S の「読み込みの設定」の画面に並ぶ項目です。上から順に並んでいます。",
                          ("項目", "選べるもの", "既定"), ui["settings"]),
    }


def build_parts_en(ui: dict) -> dict[str, str]:
    """build_parts の英語（英語の画面から読んだ名前で作る）。"""
    key_rows = [(key_display(accel), ACTION_OVERRIDES_EN.get(accel, label)) for accel, label in ui["keys"]]
    menu_rows = [(menu, label, help_ or "") for menu, label, help_ in ui["menu_only"]]
    field_rows = [(str(n), name, FIELD_WHEN_EN.get(name, "Always")) for n, name in enumerate(ui["fields"], 1)]
    return {
        "keys": table("Keys assigned to menu items. They work from any field.", ("Key", "Action"), key_rows),
        "menu_only": table("Menu items without keys. Press VO-M to move to the menu bar and choose them.",
                           ("Menu", "Item", "Description"), menu_rows),
        "tabs": table("The fields Tab stops at, in order. Shift-Tab goes back in reverse order.",
                      ("Order", "Field", "Available"), field_rows),
        "settings": table("The items in Load Settings (Command-S), from top to bottom.",
                          ("Item", "Choices", "Default"), ui["settings"]),
    }


def apply(text: str, parts: dict[str, str]) -> str:
    names = {m.group("name") for m in MARK_RE.finditer(text)}
    unknown = names - set(parts)
    if unknown:
        raise SystemExit(f"[エラー] 生成側が知らない印があります: {sorted(unknown)}")
    missing = set(parts) - names
    if missing:
        raise SystemExit(f"[エラー] 文書に印がありません: {sorted(missing)}")
    text = MARK_RE.sub(lambda m: f"{m.group(1)}\n{parts[m.group('name')]}\n{m.group(4)}", text)
    text = VERSION_SPAN_RE.sub(lambda m: f"{m.group(1)}{VERSION}{m.group(2)}", text)
    text = TITLE_RE_EN.sub(lambda m: f"{m.group(1)}{VERSION}{m.group(2)}", text)
    return TITLE_RE.sub(lambda m: f"{m.group(1)}{VERSION}{m.group(2)}", text)


def txt_of(html_text: str) -> str:
    from html_to_txt import DocumentToText

    parser = DocumentToText()
    parser.feed(html_text)
    return parser.render()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="マニュアルのうちコードから作る部分を作り直す")
    ap.add_argument("--check", action="store_true", help="書き換えずに、古いかどうかだけ調べる")
    ap.add_argument("--lang", choices=sorted(MANUALS), help="この言語のマニュアルだけ（付けなければ両方を別のプロセスで）")
    args = ap.parse_args(argv)
    if args.lang is None:
        import subprocess

        codes = [subprocess.run([sys.executable, "-X", "utf8", __file__, "--lang", lang] + (["--check"] if args.check else []),
                                cwd=ROOT).returncode for lang in MANUALS]
        return max(codes)
    html_path, txt_path = MANUALS[LANG]
    source = html_path.read_text(encoding="utf-8")
    new = apply(source, build_parts(read_ui()))
    new_txt = txt_of(new)
    old_txt = txt_path.read_text(encoding="utf-8-sig") if txt_path.is_file() else ""
    stale = new != source or new_txt.replace("\r\n", "\n") != old_txt.replace("\r\n", "\n")
    if args.check:
        print(f"マニュアル（{LANG}）は古いです。tools/doc_parts.py で作り直してください" if stale
              else f"マニュアル（{LANG}）は今のコードと合っています")
        return 1 if stale else 0
    html_path.write_text(new, encoding="utf-8")
    txt_path.write_text(new_txt, encoding="utf-8-sig", newline="\r\n")
    print(f"作り直しました: {html_path.name}（{len(new)} 字）、{txt_path.name}（{len(new_txt)} 字）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
