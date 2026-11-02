"""英語の画面で出る文言を集めて JSON で出す（tests/test_i18n.py が別のプロセスで動かす）。

言語は起動したときに決まるので、`LOGICLOADER_LANG=en` を付けて新しいプロセスで動かす。
窓は出さない。Logic にも Splice にも触らない（テストのスタブを使う）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LOGICLOADER_SILENT", "1")


def main() -> int:
    import wx

    from core import fit, i18n
    from core.settings import CHOICES, TARGET_LABELS, LoadSettings, describe
    from integration import km_helper, km_macros, render
    from integration.quick_sampler import engine
    from tests.test_controller import FakeEngine, FakeLibrary
    from ui import controller as ctl
    from ui.main_frame import KEY_HELP_EN, MainFrame, version_text
    from ui.settings_dialog import FIELDS, SettingsDialog

    qs = engine()
    out: dict[str, list[str]] = {"lang": [i18n.LANG, qs.LANG]}
    out["settings"] = [v for options in CHOICES.values() for v in options.values()] + list(TARGET_LABELS.values())
    out["settings"] += [describe(LoadSettings(target=t, auto_preview=True)) for t in ("qs", "dmd", "track")]
    out["settings"] += [label for _, label in FIELDS]
    out["choices"] = [label for label, _ in ctl.KEY_CHOICES + ctl.TRANSPOSE_CHOICES + ctl.TEMPO_CHOICES + ctl.KIND_CHOICES]
    out["helper"] = list(qs.MESSAGES.values()) + [qs.SWITCHED_MESSAGE]

    st = qs.ProjectState(0, tempo=120.0, key="C Major", meter=(4, 4), sample_rate=48000, division=16,
                         position=(3, 2, 1, 1), track_index=4, track_name="Audio 1", can_import=True, checked=True,
                         channel_mode="Mono", regions=[("Loop", "Region starts at 3 bars and ends at 5 bars , Audio")],
                         paste_at=(3, 1, 1, 1), selected_count=2)
    lagging = qs.ProjectState(0, track_index=5, track_name="Audio 2", strip_name="Audio 1")
    flex = qs.FlexState(True, True, True)
    out["phrases"] = [qs.track_phrase(st), qs.project_phrase(st), qs.track_phrase(lagging), qs.project_phrase(lagging),
                      qs.position_phrase((3, 2, 2, 1)), qs.length_phrase((1, 3, 3, 176)), qs.length_phrase((0, 0, 1, 0)),
                      qs.flex_message(flex), qs.follow_message(flex), qs.state_phrase(flex), qs.state_phrase(None),
                      qs.pad_notes("x", False), qs.key_phrase("F Minor")]

    src = fit.Source(name="loop.wav", frames=48000 * 4, sample_rate=48000, kind="loop", bpm=90, key="F#",
                     key_quality="minor")
    plan = fit.plan_fit(src, fit.Goal(tempo=120.0, tempo_scale=fit.TEMPO_EVEN, key="Am", transpose=2,
                                      sample_rate=48000))
    half = fit.plan_fit(src, fit.Goal(tempo=120.0, tempo_scale=fit.TEMPO_HALF, key="F#m"))
    out["fit"] = [plan.describe_ja(), half.describe_ja(), *plan.notes, *half.notes, fit.key_label("C#maj"),
                  fit.tempo_word(fit.TEMPO_DOUBLE), render.describe_backend()]

    class Player:
        current_path = "/x/kick.wav"

        def describe_state_ja(self):
            return "kick.wav、再生中、0.0秒目、全長8.0秒"

    class Entry:
        description, filename, bpm, key, key_quality, kind, duration = "", "kick.wav", 120, "A", "minor", "loop", 2.0

    tmp = Path(tempfile.mkdtemp())
    c = ctl.AppController(library_factory=lambda: FakeLibrary([]), engine=lambda: FakeEngine(), trusted=lambda: True,
                          settings_path=tmp / "s.json", stage_root=tmp / "st", preview_root=tmp / "pv")
    out["controller"] = [ctl.player_state(Player()), ctl.summary_en(Entry()), ctl.pad_phrase(3, None),
                         c.fit_phrase(), c.set_target("track").message_ja, c.set_target("dmd").message_ja,
                         c.set_target("qs").message_ja, c.cycle_tempo_scale().message_ja, c.toggle_key_match().message_ja,
                         c.set_transpose(-1).message_ja, c.brief_project_phrase(), c.search(ctl.SearchForm(text="kick")).message_ja,
                         ctl.SearchForm(text="a", key="C", relative=True, bpm="120", kind="loop", pack="P").describe_ja(),
                         *[label for label, _ in c.pack_choices()]]
    out["km"] = [km_macros.DIST_GROUP_NAME, *[m.name for m in km_macros.DIST_MACROS], *[m.label for m in km_macros.DIST_MACROS],
                 km_helper.install("/x", km_app=tmp / "none.app")[1]]

    app = wx.App(False)
    frame = MainFrame(c, distribution=True)
    bar = frame.GetMenuBar()
    menus = [bar.GetMenuLabel(i) for i in range(bar.GetMenuCount())]
    items = [item for i in range(bar.GetMenuCount()) for item in bar.GetMenu(i).GetMenuItems() if not item.IsSeparator()]
    out["menus"] = menus
    out["menu_items"] = [item.GetItemLabel() for item in items] + [item.GetHelp() for item in items]
    widgets = []

    def walk(w):
        for child in w.GetChildren():
            if isinstance(child, (wx.StaticText, wx.Button, wx.CheckBox)):
                widgets.append(child.GetLabel())
            if isinstance(child, wx.Choice):
                widgets.extend(child.GetItems())
            widgets.append(child.GetName())
            walk(child)

    walk(frame)
    dialog = SettingsDialog(frame, c.settings)
    walk(dialog)
    out["widgets"] = [w for w in widgets if w]
    out["window"] = [frame.GetTitle(), dialog.GetTitle(), version_text()]
    out["key_help"] = [KEY_HELP_EN]
    dialog.Destroy()
    frame.Destroy()
    del app
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
