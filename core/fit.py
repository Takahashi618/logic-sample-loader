"""ループを Logic のプロジェクトのテンポとキーに合わせる**計画**（音には触らない）。

オーディオトラックへ貼る前に「何倍の長さにし、何半音ずらすか」を決める。実際に音を作るのは
`integration/render.py`、Logic に貼るのは補助（`tools/qs_sample_menu.py` の `paste_audio`）。

| 決めること | 規則 | 出どころ |
| --- | --- | --- |
| 素材の BPM とキー | 索引（ファイル名から読んだもの） | `ptcore` |
| 合わせる先 | Logic のコントロールバー（Tempo・Key Signature）。キーはアプリで選び直せる | 補助の `read_project` |
| 何半音ずらすか | **平行調は移調しない**。-6〜+5 の近い向き | `ptcore/prep.py` |
| ハーフ・Even・ダブル | 素材の BPM を、**プロジェクトの BPM** の半分・そのまま・倍にする（利用者が選ぶ） | ここ |
| 長さ | ループの長さが小節の区切りに合うなら、**ちょうどその小節数**にそろえる | ここ |

### ハーフ・Even・ダブル（2026-10-13）

**基準はプロジェクトの BPM**（素材の BPM ではない）。120 BPM のプロジェクトなら、ハーフは 60 BPM、
Even は 120 BPM、ダブルは 240 BPM に素材を合わせる。素材の BPM はファイル名のとおりに数える。

2026-10-12 までは「87 BPM の素材は 174 の半分の数え方」とみなして、倍・半分を自動で読み替えていた。
利用者が選べるようになったので、**読み替えはやめた**（同じ素材・同じプロジェクトでハーフと Even が
同じ音になることがあり、選んだ意味が無くなるため）。代わりに、Even で大きく伸び縮みするときは
「ハーフにすると伸び縮みが小さくなります」と添える。

### ワンショットはテンポを合わせない

単発音に BPM は無い（あっても演奏の速さではない）。キーが分かっていればキーだけ合わせる
（ベースの単発音など）。

OS 非依存。**規則（小節の丸め・しきい値・平行調）は ptcore/prep.py のものをそのまま使う**（写しを持たない。
二重に持って食い違わないように）。ptcore が見つからないときは、テンポもキーも合わせない
（サンプルレートだけ合わせる）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

from core.i18n import is_english, tr
from core.sibling import load_ptcore

__all__ = [
    "KEY_PROJECT",
    "KEY_OFF",
    "TEMPO_HALF",
    "TEMPO_EVEN",
    "TEMPO_DOUBLE",
    "TEMPO_CHOICES",
    "tempo_word",
    "NO_TEMPO_KINDS",
    "tempo_applies",
    "SHARP_NAMES",
    "Source",
    "Goal",
    "FitPlan",
    "source_from_entry",
    "logic_key_query",
    "key_label",
    "key_choices",
    "transpose_choices",
    "suggest_scale",
    "plan_fit",
    "format_bpm",
]

KEY_PROJECT = "project"   # Logic のプロジェクトのキー（Key Signature）に合わせる
KEY_OFF = "off"           # キーは合わせない

# テンポの合わせ方。**プロジェクトの BPM に掛ける数**（素材の BPM ではない）
TEMPO_HALF, TEMPO_EVEN, TEMPO_DOUBLE = 0.5, 1.0, 2.0
_TEMPO_WORDS = {TEMPO_HALF: tr("ハーフ", "Half"), TEMPO_EVEN: "Even", TEMPO_DOUBLE: tr("ダブル", "Double")}
# ファイル名に付ける印。**Even にも付ける**: 2026-10-12 までの「120bpm」は倍・半分を自動で読み替えた音のことがあり、
# 同じ名前で使い回すと違う速さの音を貼ってしまう（同じ条件なら同じ名前で使い回す作りのため）
_TEMPO_TAGS = {TEMPO_HALF: "half", TEMPO_EVEN: "even", TEMPO_DOUBLE: "double"}
# 主画面の「テンポ」の欄に並べる (言葉, 値)。遅い順
TEMPO_CHOICES: list[tuple[str, float]] = [
    (tr("ハーフ（プロジェクトの BPM の半分に合わせる）", "Half (half the project BPM)"), TEMPO_HALF),
    (tr("Even（プロジェクトの BPM に合わせる）", "Even (the project BPM)"), TEMPO_EVEN),
    (tr("ダブル（プロジェクトの BPM の倍に合わせる）", "Double (twice the project BPM)"), TEMPO_DOUBLE),
]


def tempo_word(scale: float) -> str:
    """0.5 → 「ハーフ」、1 → 「Even」、2 → 「ダブル」。"""
    return _TEMPO_WORDS.get(scale, tr(f"{format_bpm(scale)} 倍", f"{format_bpm(scale)}x"))


# テンポを合わせない種別（単発音に BPM は無い。あっても演奏の速さではない）
NO_TEMPO_KINDS = ("one_shot", "fx")


def tempo_applies(kind: str) -> bool:
    """この種別の素材にテンポを合わせるか。ワンショットと効果音は合わせない（種別が分からなければ合わせる）。"""
    return kind not in NO_TEMPO_KINDS

SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_EPS = 1e-9
NO_PTCORE_NOTE = tr("素材の情報を読む部品（ptcore）が見つからないので、テンポとキーは合わせていません",
                    "The sample information component (ptcore) was not found, so tempo and key were not matched")


def _needs_render(ratio: float, semitones: int, out_rate: int, source_rate: int) -> bool:
    """音を作り直すか。**判断はここ 1 か所**（計画を作るときと、FitPlan.needs_render が同じものを見る）。

    サンプルレートだけ違うときも作る（Logic に変換させると、元の素材の隣に複製を書く）。
    """
    return abs(ratio - 1.0) > _EPS or semitones != 0 or out_rate != source_rate


@dataclass(frozen=True)
class Source:
    """素材。索引の 1 件（`ptcore.library.SampleEntry`）か、ファイル名と音声ヘッダから作る。"""

    name: str
    frames: int
    sample_rate: int
    channels: int = 2
    kind: str = ""                   # loop / one_shot / fx / 空文字
    bpm: int | None = None
    bpm_confident: bool = False
    key: str | None = None           # "F#"（ptcore の表記。シャープ系）
    key_quality: str | None = None   # "minor" / "major" / None
    key_confident: bool = False

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    @property
    def key_query(self) -> str:
        """ptcore に渡せるキーの書き方（"F#m"、"Cmaj"、"A"）。分からなければ空文字。"""
        if not self.key:
            return ""
        suffix = {"minor": "m", "major": "maj"}.get(self.key_quality or "", "")
        return f"{self.key}{suffix}"


@dataclass(frozen=True)
class Goal:
    """合わせる先。None は「合わせない」。"""

    tempo: float | None = None       # プロジェクトの BPM
    tempo_scale: float = TEMPO_EVEN  # ハーフ（0.5）・Even（1）・ダブル（2）。プロジェクトの BPM に掛ける
    key: str | None = None           # "Am"、"C#maj"（ptcore の書き方）
    transpose: int = 0               # キーを合わせたうえで、さらにずらす半音
    sample_rate: int | None = None   # プロジェクトのサンプルレート（None なら素材のまま）


@dataclass(frozen=True)
class FitPlan:
    """何をするかの結論。"""

    source: Source
    goal: Goal
    ratio: float = 1.0               # 長さの比（出力 ÷ 入力）。1 より小さいと速くなる
    key_shift: int = 0               # キーを合わせるための半音
    out_rate: int = 0
    out_frames: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def semitones(self) -> int:
        return self.key_shift + self.goal.transpose

    @property
    def tempo_changed(self) -> bool:
        return abs(self.ratio - 1.0) > _EPS

    @property
    def target_tempo(self) -> float | None:
        """素材を合わせる BPM（プロジェクトの BPM × ハーフ・Even・ダブル）。合わせないなら None。"""
        if not self.goal.tempo:
            return None
        return self.goal.tempo * self.goal.tempo_scale

    @property
    def tempo_fitted(self) -> bool:
        """テンポを合わせる計画か（合わせる先と素材の BPM があり、ワンショット・効果音でない）。

        結果として速さが変わらなかったとき（102 BPM の素材を 102 BPM に）も真。読み上げで「何に合わせたか」を
        言ってよいかの判断に使う（ワンショットは BPM があっても合わせないので、「ハーフと同じなので」と言わない）。
        """
        return bool(self.target_tempo and self.source.bpm and tempo_applies(self.source.kind))

    @property
    def needs_render(self) -> bool:
        """音を作り直すか（_needs_render）。"""
        return _needs_render(self.ratio, self.semitones, self.out_rate, self.source.sample_rate)

    @property
    def result_key(self) -> str:
        """ずらしたあとのキー（ptcore の書き方）。素材のキーが分からなければ空文字。"""
        pc = _pitch_class(self.source.key)
        if pc is None:
            return ""
        name = SHARP_NAMES[(pc + self.semitones) % 12]
        suffix = {"minor": "m", "major": "maj"}.get(self.source.key_quality or "", "")
        return f"{name}{suffix}"

    def tag(self) -> str:
        """作ったファイルの名前に付ける印（「120bpm_even_+3_Am」）。**同じ条件なら同じ名前**になる。

        Logic はリージョンにファイル名を付けるので、VoiceOver で名前を聞けば何に合わせたかが分かる。
        """
        parts = []
        if self.tempo_changed and self.goal.tempo:
            # 「120bpm_even」「120bpm_half」（プロジェクトの BPM と合わせ方。読み上げの言い方と同じ並び）
            mark = _TEMPO_TAGS.get(self.goal.tempo_scale, "")
            parts.append(f"{format_bpm(self.goal.tempo)}bpm" + (f"_{mark}" if mark else ""))
        if self.semitones:
            parts.append(f"{self.semitones:+d}")
            if self.result_key:
                parts.append(self.result_key)
        if self.out_rate and self.out_rate != self.source.sample_rate:
            parts.append(f"{_rate_short(self.out_rate)}k")
        return "_".join(parts) or "fit"

    def describe_ja(self) -> str:
        """何をしたかの読み上げ（「102 BPM を 120 BPM に。F#マイナーを Aマイナーに（3 半音上げ）」）。

        ハーフ・ダブルは「102 BPM を、120 BPM のハーフ（60 BPM）に」。英語の画面では英語で言う（describe_en）。
        """
        if is_english():
            return self.describe_en()
        parts = []
        src, goal = self.source, self.goal
        if self.tempo_fitted:
            even = goal.tempo_scale == TEMPO_EVEN
            aim = (f"{format_bpm(goal.tempo)} BPM" if even else
                   f"{format_bpm(goal.tempo)} BPM の{tempo_word(goal.tempo_scale)}（{format_bpm(self.target_tempo)} BPM）")
            if self.tempo_changed:
                parts.append(f"{src.bpm} BPM を {aim} に" if even else f"{src.bpm} BPM を、{aim}に")
            elif not even:
                parts.append(f"{src.bpm} BPM は {aim}と同じなので、速さはそのまま")
        if self.semitones:
            move = f"{abs(self.semitones)} 半音{'上げ' if self.semitones > 0 else '下げ'}"
            if src.key and self.result_key:
                parts.append(f"{key_label(src.key_query)}を{key_label(self.result_key)}に（{move}）")
            else:
                parts.append(move)
        elif goal.key and src.key:
            parts.append(f"キーは{key_label(src.key_query)}のまま")
        return "。".join(parts)

    def describe_en(self) -> str:
        """describe_ja の英語（"102 BPM to 120 BPM. F# minor to A minor (up 3 semitones)"）。"""
        parts = []
        src, goal = self.source, self.goal
        if self.tempo_fitted:
            even = goal.tempo_scale == TEMPO_EVEN
            aim = (f"{format_bpm(goal.tempo)} BPM" if even else
                   f"{tempo_word(goal.tempo_scale).lower()} of {format_bpm(goal.tempo)} BPM ({format_bpm(self.target_tempo)} BPM)")
            if self.tempo_changed:
                parts.append(f"{src.bpm} BPM to {aim}")
            elif not even:
                parts.append(f"{src.bpm} BPM is already the {aim}, so the speed is unchanged")
        if self.semitones:
            n = abs(self.semitones)
            move = f"{'up' if self.semitones > 0 else 'down'} {n} semitone{'s' if n != 1 else ''}"
            if src.key and self.result_key:
                parts.append(f"{key_label(src.key_query)} to {key_label(self.result_key)} ({move})")
            else:
                parts.append(move.capitalize())
        elif goal.key and src.key:
            parts.append(f"Key stays {key_label(src.key_query)}")
        return ". ".join(parts)


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------

def source_from_entry(entry: Any, *, frames: int | None = None, sample_rate: int | None = None,
                      kind: str | None = None) -> Source:
    """索引の 1 件から作る。frames と sample_rate は音声ヘッダの値で上書きできる。"""
    rate = int(sample_rate or getattr(entry, "sample_rate", 0) or 0)
    count = frames
    if count is None:
        duration = float(getattr(entry, "duration", 0.0) or 0.0)
        count = int(round(duration * rate)) if rate else 0
    return Source(
        name=str(getattr(entry, "filename", "") or ""),
        frames=int(count or 0),
        sample_rate=rate,
        channels=int(getattr(entry, "channels", 0) or 0),
        kind=kind if kind is not None else (getattr(entry, "kind", "") or ""),
        bpm=getattr(entry, "bpm", None),
        bpm_confident=getattr(entry, "bpm_confidence", "none") == "high",
        key=getattr(entry, "key", None),
        key_quality=getattr(entry, "key_quality", None),
        key_confident=getattr(entry, "key_confidence", "none") == "high",
    )


# --------------------------------------------------------------------------
# キーの書き方
# --------------------------------------------------------------------------

def _pitch_class(key: str | None) -> int | None:
    if not key:
        return None
    root = key[0].upper()
    if root not in _PC:
        return None
    pc = _PC[root]
    rest = key[1:2]
    if rest in ("#", "♯"):
        pc += 1
    elif rest in ("b", "♭"):
        pc -= 1
    return pc % 12


def logic_key_query(text: str | None) -> str:
    """Logic の Key Signature の表示（「C Major」「F♯ Minor」「B♭ Major」）を ptcore の書き方にする。

    日本語の表示の Logic は「Cメジャー」「F♯マイナー」と出す（2026-11-02 実測）。どちらも読む。
    読めなければ空文字（「合わせない」と同じ扱いになる）。
    """
    if not text:
        return ""
    import re

    m = re.match(r"^\s*([A-Ga-g])\s*([#♯b♭]?)\s*(major|minor|maj|min|m|メジャー|マイナー)?\s*$", text.strip(),
                 re.IGNORECASE)
    if not m:
        return ""
    pc = _pitch_class(m.group(1).upper() + (m.group(2) or ""))
    if pc is None:
        return ""
    quality = (m.group(3) or "").lower()
    suffix = ("m" if quality in ("minor", "min", "m", "マイナー")
              else "maj" if quality in ("major", "maj", "メジャー") else "")
    return f"{SHARP_NAMES[pc]}{suffix}"


def key_label(query: str) -> str:
    """"Am" を「Aマイナー」にする（ptcore と同じ言い方）。読めなければそのまま返す。英語の画面では「A minor」。"""
    prep = None if is_english() else load_ptcore("prep")
    if prep is not None:
        try:
            return prep.key_label(query)
        except (ValueError, KeyError):
            return query
    pc = _pitch_class(query)
    if pc is None:
        return query
    name = SHARP_NAMES[pc]
    if query.endswith("maj"):
        return tr(f"{name}メジャー", f"{name} major")
    if query.endswith("m"):
        return tr(f"{name}マイナー", f"{name} minor")
    return name


def key_choices() -> list[tuple[str, str]]:
    """主画面の「キー」の欄に並べる (言葉, 値)。先頭の 2 つは「プロジェクトに合わせる」「合わせない」。"""
    items = [(tr("プロジェクトのキーに合わせる", "Match the project key"), KEY_PROJECT),
             (tr("キーは合わせない（素材のまま）", "Don't match key (keep the sample's)"), KEY_OFF)]
    for name in SHARP_NAMES:
        for suffix in ("maj", "m"):
            query = f"{name}{suffix}"
            items.append((tr(f"{key_label(query)}に合わせる", f"Match {key_label(query)}"), query))
    return items


def transpose_choices() -> list[tuple[str, int]]:
    """主画面の「移調」の欄に並べる (言葉, 半音)。-12 から +12 まで。"""
    items = []
    for n in range(-12, 13):
        if n == 0:
            items.append((tr("移調しない", "No transpose"), 0))
        else:
            items.append((tr(f"{abs(n)} 半音{'上げる' if n > 0 else '下げる'}",
                             f"{'Up' if n > 0 else 'Down'} {abs(n)} semitone{'s' if abs(n) != 1 else ''}"), n))
    return items


# --------------------------------------------------------------------------
# 計画
# --------------------------------------------------------------------------

def format_bpm(value: float) -> str:
    """120.0 → "120"、123.5 → "123.5"。"""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _rate_short(rate: int) -> str:
    return f"{rate / 1000:.1f}".rstrip("0").rstrip(".")


def suggest_scale(source_bpm: float, project_bpm: float) -> float:
    """ハーフ・Even・ダブルのうち、素材の伸び縮みが最も小さいもの。同じなら Even。

    87 BPM の素材を 174 BPM のプロジェクトへ入れるなら、ハーフ（87 BPM）でそのまま使える。
    """
    best, best_cost = TEMPO_EVEN, abs(math.log(source_bpm / project_bpm))
    for scale in (TEMPO_HALF, TEMPO_DOUBLE):
        cost = abs(math.log(source_bpm / (project_bpm * scale)))
        if cost + 1e-12 < best_cost:
            best, best_cost = scale, cost
    return best


def _semitones(source: Source, target_key: str) -> tuple[int, bool]:
    """(キーを合わせる半音, 長短が片方分からなかったか)。ptcore が無ければ (0, False)。"""
    prep = load_ptcore("prep")
    if prep is None or not source.key:
        return 0, False
    try:
        shift = prep.pitch_shift_semitones(source.key_query, target_key, source.key_quality or None, None)
    except (ValueError, KeyError):
        return 0, False
    target_minor = target_key.endswith("m")
    target_major = target_key.endswith("maj")
    unsure = not source.key_quality or not (target_minor or target_major)
    return int(shift), unsure


def plan_fit(source: Source, goal: Goal) -> FitPlan:
    """素材と合わせる先を突き合わせて、計画を作る。**失敗しない**（分からないところは合わせない）。"""
    notes: list[str] = []
    rate = int(goal.sample_rate or source.sample_rate or 0)
    prep = load_ptcore("prep")
    if prep is None:
        # 規則（小節の丸め・しきい値・平行調）が無いので合わせない。移調の指定も使わない（そのまま貼る）
        plain = replace(goal, tempo=None, key=None, transpose=0)
        frames = int(round(source.frames * rate / source.sample_rate)) if source.sample_rate else 0
        return FitPlan(source=source, goal=plain, out_rate=rate, out_frames=frames, notes=(NO_PTCORE_NOTE,))
    loopish = tempo_applies(source.kind)

    ratio = 1.0
    if goal.tempo and loopish:
        if source.bpm:
            # 素材の BPM（ファイル名のとおり）を、プロジェクトの BPM × ハーフ・Even・ダブルにする
            ratio = source.bpm / (goal.tempo * goal.tempo_scale)
            if abs(ratio - 1.0) > prep.STRETCH_WARN_RATIO:
                notes.append(tr("速さを大きく変えたので、音が荒れることがあります",
                                "The speed changed a lot, so the sound may be rough"))
                better = suggest_scale(source.bpm, goal.tempo)
                if goal.tempo_scale == TEMPO_EVEN and better != TEMPO_EVEN:
                    notes.append(tr(f"{tempo_word(better)}にすると、伸び縮みが小さくなります",
                                    f"{tempo_word(better)} would stretch it less"))
            if not source.bpm_confident:
                notes.append(tr("素材の BPM はファイル名から推し量った値です",
                                "The sample BPM is a guess from the file name"))
        elif source.kind == "loop":
            notes.append(tr("素材の BPM が分からないので、速さは変えていません",
                            "The sample BPM is unknown, so the speed was not changed"))

    key_shift = 0
    if goal.key:
        if source.key:
            key_shift, unsure = _semitones(source, goal.key)
            if unsure:
                notes.append(tr("長調か短調かが分からないので、主音どうしで合わせました",
                                "Major or minor is unknown, so the root notes were matched"))
            elif not source.key_confident and key_shift:
                notes.append(tr("素材のキーはファイル名から推し量った値です",
                                "The sample key is a guess from the file name"))
        elif source.kind == "loop":
            notes.append(tr("素材のキーが分からないので、キーは合わせていません",
                            "The sample key is unknown, so the key was not matched"))
    if abs(key_shift + goal.transpose) >= prep.PITCH_WARN_SEMITONES:
        notes.append(tr("音程を大きく変えたので、音色が変わって聞こえることがあります",
                        "The pitch changed a lot, so the tone may sound different"))

    # 長さ。まずは伸び縮みの比のとおり。作り直すときに限り、ループが小節の区切りに合うなら
    # **ちょうどその拍数**にそろえる（数サンプルの端数でも、Logic のリージョンの長さが
    # 「2 小節と 1 ティック」のようになり、並べたときにずれていく）
    out_frames = int(round(source.frames * ratio * rate / source.sample_rate)) if source.sample_rate else 0
    if source.bpm and source.duration > 0 and source.kind == "loop":
        effective = source.bpm
        src_bars = source.duration * effective / 60.0 / 4.0
        nearest = prep.nearest_musical_bars(src_bars)
        if abs(src_bars - nearest) <= prep.BAR_FIT_TOLERANCE:
            if _needs_render(ratio, key_shift + goal.transpose, rate, source.sample_rate) and rate:
                out_frames = int(round(nearest * 4.0 * 60.0 / (effective / ratio) * rate))
        elif abs(ratio - 1.0) > _EPS:
            notes.append(tr("ループの長さが BPM と合わないので、テンポがずれるかもしれません",
                            "The loop length does not fit its BPM, so the tempo may drift"))

    return FitPlan(source=source, goal=goal, ratio=ratio, key_shift=key_shift,
                   out_rate=rate, out_frames=out_frames, notes=tuple(notes))
