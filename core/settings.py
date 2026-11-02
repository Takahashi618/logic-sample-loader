"""読み込みの設定（Cmd+S）と、その保存。

| 項目 | 既定 | 意味 |
| --- | --- | --- |
| 読み込み方 | Original | 「開く」ダイアログ下部の選択肢。Optimized は音程と音量を整え、ループ点を探し、無音を切る |
| 速さを保つ（Flex） | 素材に合わせる | ループなら入れる。**入れると音程を変えても速さが変わらない** |
| Follow Tempo | 切る | 入れるとプロジェクトのテンポに合わせて鳴る（Flex を入れたときだけ効く） |
| 読み込んだあと | このアプリに戻る | 次の素材をすぐ選べる。MIDI 鍵盤の音は Logic が背面でも鳴る |
| 自動で試聴 | 切る | 一覧で選び直すたびに鳴らす（3 つのアプリの約束はスペースなので、既定は切る） |
| 読み込み先 | Quick Sampler の画面 | ほかに Drum Machine Designer のパッド、Logic のオーディオトラック（主画面で切り替える） |
| 次のパッドへ進む | 入れる | パッドへ読み込んだら、次のパッドを選んでおく（Battery ローダーの「次のセルへ」と同じ考え方） |
| トラックに貼るとき、テンポを合わせる | 入れる | 素材の BPM をプロジェクトのテンポに合わせてから貼る（キーは主画面の欄で選ぶ） |
| トラックに貼る位置 | 再生位置のある小節の頭 | Logic の決まり。もう 1 つは「いちばん近い小節の頭」（再生位置を動かしてから貼る） |
| 貼ったあとの再生位置 | 終わりへ進める | 貼ったリージョンの終わり（途中なら次の小節の頭）へ進める。ループを続けて並べられる（DMD の「次のパッドへ」と同じ考え方）。ほかに「頭に置く」（すぐ再生して聴ける）、「動かさない」 |

### キーと移調は保存しない

主画面の「キー」と「移調」は、**アプリを閉じると「プロジェクトのキーに合わせる」「移調しない」に戻る。**
曲ごとに違う値なので、前の曲の値のまま次の曲に貼ると、気づかないうちに違うキーで貼ってしまう
（アプリを終了すると空に戻る）。

### Flex の既定を「素材に合わせる」にした理由

利用者の要望は「音程を変えても速さを変えたくない」（2026-09-25）。ループがまさにそれで、
Quick Sampler の既定（Flex 切り）だと鍵盤ごとに速さが変わる。**ワンショットには要らない**
（ドラムの単発音は短く、速さが変わっても困らない。時間伸縮で音が荒れるほうが困る）。
素材に合わせる（ループ → 速さを保つ）。

**Flex の状態は次の素材を読み込んでも引き継がれる**（2026-09-25 実測）ので、
読み込むたびにこの設定どおりに入れ直す。

### Follow Tempo の既定を「切る」にした理由

入れると、素材の速さをプロジェクトのテンポに合わせて変える。そのためには
**素材のテンポを Logic が正しく知っている必要がある。** Splice の WAV にはテンポの情報が
ほとんど無く（ACID を持つのは 3,126 件中 31 件）、Logic の推定は倍・半分に外れることがある
（Untagged Loops の表示で 36% しか当たらなかった）。
切っておけば、**元の速さのまま音程だけが変わる**。要望そのもの。

### 保存

`~/Library/Application Support/LogicSampleLoader/settings.json`。
**読めなければ既定で続け、書くときは不可分にする**（利用者が育てる状態ファイルは、壊れていても起動できるようにする）。

### Splice のフォルダ（2026-10-24 から）

Splice は素材を `~/Splice` に置くが、利用者がアプリの設定で動かせる（Splice のヘルプ「Where is my Splice folder?」）。
空なら決まった場所を順に探し（`resolve_splice_root`）、操作メニューの「Splice のフォルダを選ぶ」で選び直せる。
選んだのが `Splice` のフォルダそのものでも、中の `sounds/packs` を使う（索引はパックの並ぶフォルダから数える）。

OS 非依存。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from core.i18n import tr

__all__ = [
    "SETTINGS_PATH",
    "INDEX_PATH",
    "SPLICE_CANDIDATES",
    "resolve_splice_root",
    "LoadSettings",
    "MODE_LABELS",
    "FLEX_LABELS",
    "FOLLOW_LABELS",
    "AFTER_LABELS",
    "TARGET_LABELS",
    "SNAP_LABELS",
    "PLAYHEAD_LABELS",
    "CHOICES",
    "flex_for",
    "follow_for",
    "describe",
    "load_settings",
    "save_settings",
]

SETTINGS_PATH = Path.home() / "Library" / "Application Support" / "LogicSampleLoader" / "settings.json"
# 配布版の Splice の索引。**.app の中には書かない**（署名が壊れる）。手元の版は借り先の索引を共有する
INDEX_PATH = SETTINGS_PATH.with_name("splice_index.json")

# Splice のパックが並ぶフォルダの候補（上から順に、あるものを使う）。Splice の版で名前が違う
SPLICE_CANDIDATES = (Path("~/Splice/sounds/packs"), Path("~/Splice/Samples/packs"))
# 選んだフォルダの中にこれがあれば、そちらをパックの並ぶフォルダとして使う
_PACK_SUBFOLDERS = ("sounds/packs", "Samples/packs", "packs")


def resolve_splice_root(chosen: str = "") -> Path:
    """Splice のパックが並ぶフォルダ。chosen は設定の値（空なら自動）。

    - 選んだフォルダの中に `sounds/packs` などがあれば、そちら（`~/Splice` を選んでも、パックを数え間違えない）
    - 空なら SPLICE_CANDIDATES のうち、あるもの。どれも無ければ最初の候補（索引を作るときに「無い」と言う）
    """
    if chosen.strip():
        base = Path(chosen.strip()).expanduser()
        for sub in _PACK_SUBFOLDERS:
            if (base / sub).is_dir():
                return base / sub
        return base
    for candidate in SPLICE_CANDIDATES:
        if candidate.expanduser().is_dir():
            return candidate.expanduser()
    return SPLICE_CANDIDATES[0].expanduser()

# 読み上げに出す言葉。**英語の値のまま読ませない。** 並びは画面の選択肢の順。
MODE_LABELS: dict[str, str] = {
    "original": tr("Original。音程も音量も長さも元のまま", "Original. Pitch, level and length stay as they are"),
    "optimized": tr("Optimized。音程と音量を整え、ループ点を探し、前後の無音を切る",
                    "Optimized. Tunes and normalizes, finds loop points, trims silence at both ends"),
}
FLEX_LABELS: dict[str, str] = {
    "auto": tr("素材に合わせる。ループなら入れる", "Match the sample. On for loops"),
    "on": tr("いつも入れる", "Always on"),
    "off": tr("入れない", "Off"),
}
FOLLOW_LABELS: dict[str, str] = {
    "off": tr("切る。素材の元の速さで鳴らす", "Off. Plays at the sample's own speed"),
    "on": tr("入れる。プロジェクトのテンポに合わせる", "On. Follows the project tempo"),
}
AFTER_LABELS: dict[str, str] = {
    "back": tr("このアプリに戻る", "Return to this app"),
    "stay": tr("Logic のままにする", "Stay in Logic"),
}
# 読み込み先（主画面の欄で切り替える。ダイアログには出さない）
TARGET_LABELS: dict[str, str] = {
    "qs": tr("Quick Sampler の画面", "Quick Sampler window"),
    "dmd": tr("Drum Machine Designer のパッド", "Drum Machine Designer pad"),
    "track": tr("Logic のオーディオトラック（テンポとキーを合わせて貼る）",
                "Logic audio track (pasted at the project tempo and key)"),
}
# オーディオトラックに貼る位置。**Logic は再生位置のある小節の頭に置く**（2026-09-25 実測）
SNAP_LABELS: dict[str, str] = {
    "off": tr("再生位置のある小節の頭（Logic の決まりどおり）", "Start of the bar the playhead is in (Logic's rule)"),
    "bar": tr("いちばん近い小節の頭（再生位置を動かしてから貼る）",
              "Nearest bar start (moves the playhead first, then pastes)"),
}
# オーディオトラックに貼ったあとの再生位置（2026-09-26 の要望で 3 つから選べるようにした。それまでは入り切りだけ）
PLAYHEAD_LABELS: dict[str, str] = {
    "end": tr("貼ったオーディオの終わりへ進める（続けて貼ると、順に並ぶ）",
              "Move to the end of the pasted audio (paste again to line loops up)"),
    "start": tr("貼ったオーディオの頭に置く（すぐ再生して聴ける）",
                "Move to the start of the pasted audio (play to hear it right away)"),
    "stay": tr("動かさない", "Don't move"),
}

# 項目名 → その選択肢。**並びと値はここだけで決める**（画面とテストが同じものを見る）
CHOICES: dict[str, dict[str, str]] = {
    "mode": MODE_LABELS,
    "flex": FLEX_LABELS,
    "follow": FOLLOW_LABELS,
    "after": AFTER_LABELS,
    "snap": SNAP_LABELS,
    "playhead": PLAYHEAD_LABELS,
}


@dataclass(frozen=True)
class LoadSettings:
    mode: str = "original"
    flex: str = "auto"
    follow: str = "off"
    after: str = "back"
    auto_preview: bool = False
    # パックの並び順（Cmd+Shift+G）。値は借りている kinds.py の PACK_ORDER_*
    pack_order: str = "count"
    target: str = "qs"
    pad_advance: bool = True
    fit_tempo: bool = True
    snap: str = "off"
    playhead: str = "end"
    # Splice のフォルダ（空なら自動で探す。resolve_splice_root）
    splice_root: str = ""


def flex_for(settings: LoadSettings, kind: str) -> bool:
    """この素材を読み込むとき、Flex を入れるか。kind は `loop` / `one_shot` / `fx` / 空文字。"""
    if settings.flex == "on":
        return True
    if settings.flex == "off":
        return False
    return kind == "loop"


def follow_for(settings: LoadSettings) -> bool:
    return settings.follow == "on"


def describe(settings: LoadSettings) -> str:
    """設定を変えたときに読み上げる短い要約。**いまの読み込み先に効くものだけ**を言う（長いと聞き取れない）。

    2026-10-12 までは設定のボタンの名前にも入れていた（タブで通るたびに長く読まれたのでやめた）。
    """
    after = tr("このアプリに戻る", "return to this app") if settings.after == "back" else tr("Logic のまま", "stay in Logic")
    preview = tr("、自動で試聴", ", auto preview") if settings.auto_preview else ""
    if settings.target == "track":
        tempo = tr("テンポを合わせる", "Match tempo") if settings.fit_tempo else tr("テンポは合わせない", "Don't match tempo")
        where = (tr("いちばん近い小節の頭", "nearest bar start") if settings.snap == "bar"
                 else tr("再生位置のある小節の頭", "start of the playhead's bar"))
        playhead = {"end": tr("貼ったオーディオの終わりへ進める", "move to the end of the pasted audio"),
                    "start": tr("貼ったオーディオの頭に置く", "move to the start of the pasted audio"),
                    "stay": tr("動かさない", "don't move")}.get(settings.playhead, tr("動かさない", "don't move"))
        return tr(f"{tempo}、貼る位置は{where}、再生位置は{playhead}、貼ったあとは{after}{preview}",
                  f"{tempo}, paste at {where}, playhead: {playhead}, after pasting: {after}{preview}")
    mode = "Optimized" if settings.mode == "optimized" else "Original"
    flex = {"auto": tr("素材に合わせる", "match the sample"), "on": tr("いつも入れる", "always on"),
            "off": tr("入れない", "off")}[settings.flex]
    follow = tr("入れる", "on") if settings.follow == "on" else tr("切る", "off")
    advance = ""
    if settings.target == "dmd":
        advance = (tr("、パッドは次へ進む", ", move to the next pad") if settings.pad_advance
                   else tr("、パッドは進めない", ", stay on the pad"))
    return tr(f"{mode}、Flex は{flex}、Follow Tempo は{follow}、読み込んだあとは{after}{preview}{advance}",
              f"{mode}, Flex {flex}, Follow Tempo {follow}, after loading: {after}{preview}{advance}")


def _clean(raw: object) -> LoadSettings:
    """読んだ値を設定にする。**知らない値は既定に戻す**（手で書き換えられても起動できる）。"""
    base = LoadSettings()
    if not isinstance(raw, dict):
        return base
    if "playhead" not in raw and isinstance(raw.get("advance"), bool):
        # 1.4 までの「貼ったら再生位置を進める」（入り切り）を引き継ぐ
        raw = {**raw, "playhead": "end" if raw["advance"] else "stay"}
    known = {f.name for f in fields(LoadSettings)}
    values = {}
    for key, value in raw.items():
        if key not in known:
            continue
        if key in CHOICES:
            if isinstance(value, str) and value in CHOICES[key]:
                values[key] = value
        elif key in ("auto_preview", "pad_advance", "fit_tempo"):
            if isinstance(value, bool):
                values[key] = value
        elif key == "target":
            if isinstance(value, str) and value in TARGET_LABELS:
                values[key] = value
        elif key == "pack_order":
            if isinstance(value, str) and value:
                values[key] = value
        elif key == "splice_root":
            if isinstance(value, str):
                values[key] = value
    return replace(base, **values)


def load_settings(path: Path = SETTINGS_PATH) -> LoadSettings:
    """設定を読む。**読めなければ既定。** 壊れていても起動を止めない。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LoadSettings()
    return _clean(raw)


def save_settings(settings: LoadSettings, path: Path = SETTINGS_PATH) -> None:
    """設定を書く。**同じフォルダの一時ファイルに書いてから差し替える。**"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
