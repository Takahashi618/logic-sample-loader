"""読み込みの設定ダイアログ（Cmd+S）。

作者の別のアプリの設定の画面と同じく Cmd+S。
**同じキーが同じ意味を持つ**ようにしてある（アプリを持ち替えても迷わない）。

守っていること:

- 板は `wx.ScrolledWindow`。素の `wx.Panel` だと欄が高さ 0 に潰れる
- 読み上げ名は `label_control` で付ける
- 大きさは中身から決めて、画面の作業領域で頭打ちにする
- **Enter と Escape はボタンの上かどうかで意味を変える**（`wx_keys`）
- **Escape と Cmd+W のどちらでも閉じられること**
"""

from __future__ import annotations

from dataclasses import replace

import wx

from core.i18n import tr
from core.settings import CHOICES, LoadSettings
from ui.borrowed import handle_dialog_enter_escape, label_control

__all__ = ["SettingsDialog", "FIELDS"]

# 画面に並べる順と、欄の名前（読み上げに出る）
FIELDS: list[tuple[str, str]] = [
    ("mode", tr("読み込み方", "Load mode")),
    ("flex", tr("速さを保つ（Flex）。入れると、音程を変えても速さが変わりません",
                "Keep speed (Flex). When on, changing the pitch does not change the speed")),
    ("follow", tr("Follow Tempo。Flex を入れたときだけ効きます", "Follow Tempo. Works only when Flex is on")),
    ("after", tr("読み込んだあと", "After loading")),
    ("snap", tr("オーディオトラックに貼る位置", "Where to paste on the audio track")),
    ("playhead", tr("オーディオトラックに貼ったあとの再生位置", "Playhead after pasting on the audio track")),
]


def _static(parent: wx.Window, text: str) -> wx.StaticText:
    st = wx.StaticText(parent, label=text)
    st.SetName(text)
    return st


class SettingsDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, settings: LoadSettings) -> None:
        super().__init__(parent, title=tr("読み込みの設定", "Load Settings"))
        self._settings = settings
        self._choices: dict[str, wx.Choice] = {}
        self._build(settings)
        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self._fit_to_content()

    def _build(self, settings: LoadSettings) -> None:
        panel = wx.ScrolledWindow(self, style=wx.TAB_TRAVERSAL | wx.VSCROLL)
        panel.SetScrollRate(0, 20)
        outer = wx.BoxSizer(wx.VERTICAL)
        for key, label in FIELDS:
            options = CHOICES[key]
            outer.Add(_static(panel, label), 0, wx.LEFT | wx.TOP, 4)
            ctrl = wx.Choice(panel, choices=list(options.values()))
            values = list(options)
            current = getattr(settings, key)
            ctrl.SetSelection(values.index(current) if current in values else 0)
            label_control(ctrl, label)
            outer.Add(ctrl, 0, wx.EXPAND | wx.ALL, 4)
            self._choices[key] = ctrl

        self._auto_preview = wx.CheckBox(panel, label=tr("一覧で選び直したら、自動で試聴する",
                                                         "Preview automatically when the selection changes"))
        self._auto_preview.SetValue(settings.auto_preview)
        label_control(self._auto_preview, tr("一覧で選び直したら、自動で試聴する。切っているときはスペースで試聴",
                                             "Preview automatically when the selection changes. When off, preview with Space"))
        outer.Add(self._auto_preview, 0, wx.ALL, 4)

        pad_label = tr("DMD のパッドへ読み込んだら、次のパッドへ進む", "Move to the next pad after loading into a DMD pad")
        self._pad_advance = wx.CheckBox(panel, label=pad_label)
        self._pad_advance.SetValue(settings.pad_advance)
        label_control(self._pad_advance, pad_label)
        outer.Add(self._pad_advance, 0, wx.ALL, 4)

        self._fit_tempo = wx.CheckBox(panel, label=tr("オーディオトラックに貼るとき、テンポをプロジェクトに合わせる",
                                                      "Match the project tempo when pasting on an audio track"))
        self._fit_tempo.SetValue(settings.fit_tempo)
        label_control(self._fit_tempo, tr("オーディオトラックに貼るとき、素材の BPM をプロジェクトのテンポに合わせる",
                                          "When pasting on an audio track, stretch the sample BPM to the project tempo"))
        outer.Add(self._fit_tempo, 0, wx.ALL, 4)

        row = wx.BoxSizer(wx.HORIZONTAL)
        ok = wx.Button(panel, wx.ID_OK, tr("決定", "OK"))
        cancel = wx.Button(panel, wx.ID_CANCEL, tr("キャンセル", "Cancel"))
        label_control(ok, tr("決定", "OK"))
        label_control(cancel, tr("キャンセル", "Cancel"))
        ok.SetDefault()
        # **OK / キャンセルは EndModal を直接呼ぶ**（CreateStdDialogButtonSizer は確実に閉じない）
        ok.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_OK))
        cancel.Bind(wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CANCEL))
        row.Add(ok, 0, wx.ALL, 4)
        row.Add(cancel, 0, wx.ALL, 4)
        outer.Add(row, 0, wx.ALL, 4)
        self.SetAffirmativeId(wx.ID_OK)
        self.SetEscapeId(wx.ID_CANCEL)

        panel.SetSizer(outer)
        panel.FitInside()
        self._outer = outer
        self._panel = panel
        first = self._choices[FIELDS[0][0]]
        wx.CallAfter(first.SetFocus)

    def _fit_to_content(self) -> None:
        """**大きさは中身から決めて、画面の作業領域で頭打ちにする。**"""
        need = self._outer.GetMinSize()
        area = wx.Display(wx.Display.GetFromWindow(self) if wx.Display.GetFromWindow(self) >= 0 else 0).GetClientArea()
        self.SetSize((min(need.width + 40, area.width), min(need.height + 60, int(area.height * 0.9))))

    def _on_char_hook(self, evt: wx.KeyEvent) -> None:
        # Cmd+W でも閉じる（下位の窓は Escape と Cmd+W のどちらでも閉じられること）
        if evt.GetKeyCode() in (ord("W"), ord("w")) and evt.GetModifiers() == wx.MOD_CMD:
            self.EndModal(wx.ID_CANCEL)
            return
        if handle_dialog_enter_escape(self, evt):
            return
        evt.Skip()

    def result(self) -> LoadSettings:
        values = {}
        for key, ctrl in self._choices.items():
            options = list(CHOICES[key])
            index = ctrl.GetSelection()
            values[key] = options[index] if 0 <= index < len(options) else getattr(self._settings, key)
        return replace(self._settings, auto_preview=self._auto_preview.GetValue(),
                       pad_advance=self._pad_advance.GetValue(), fit_tempo=self._fit_tempo.GetValue(), **values)
