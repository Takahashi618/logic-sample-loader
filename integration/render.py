"""計画（`core/fit.py` の FitPlan）どおりに音を作る。

加工の道具は `ptcore/dsp.py` と同じもの:

| 道具 | 使いどころ | 状態 |
| --- | --- | --- |
| `rubberband` | 伸縮と移調。音楽の素材では**こちらが明確に良い** | Homebrew で入れる（4.0.0 で確かめた）。R3（`--fine`）で使う |
| `ffmpeg` | 伸縮と移調（`asetrate` + `atempo`）、サンプルレートの変換、長さの仕上げ | Homebrew で入れる（8.1.2 で確かめた）。これは必須 |

rubberband があれば伸縮と移調はそちらに任せ、ffmpeg で仕上げる。無ければ ffmpeg だけで作る。

### 長さをサンプル単位でそろえる

ffmpeg の `atempo` は、長さが数ミリ秒ずれる（4 秒のループで 120 サンプル短かった。2026-09-25 実測）。
Logic に貼るとリージョンの長さが「2 小節」ちょうどにならず、並べたときにずれていく。
**最後に `apad` と `atrim` で、計画のサンプル数ちょうどにする。**

### 書き出しは不可分に

作りかけのファイルを Logic や試聴に掴ませないよう、同じフォルダの一時ファイルに書いてから名前を変える。

**失敗しても例外は投げない。** `RenderResult.ok` を見ること。失敗したら元の素材では貼らない
（意図しない速さ・音程で貼るより、何もしないほうがよい）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.i18n import tr
from core.sibling import load_ptcore

__all__ = ["RenderResult", "AudioProbe", "render", "probe", "backend_name", "describe_backend", "ffmpeg_filter",
           "ensure_tool_path", "HEADER_EXTENSIONS"]

TIMEOUT = 180.0

# Homebrew の置き場所。**Finder から開いたアプリの PATH には入っていない**（launchd の既定は
# /usr/bin:/bin:/usr/sbin:/sbin）。端末から起動すると動くのに、.app から開くと「ffmpeg が見つかりません」になる
TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def ensure_tool_path() -> None:
    """PATH に Homebrew の場所が無ければ足す（このアプリの中だけ）。"""
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    missing = [d for d in TOOL_DIRS if d not in parts and os.path.isdir(d)]
    if missing:
        os.environ["PATH"] = os.pathsep.join(parts + missing)


ensure_tool_path()


# ptcore.audiofile がヘッダを読める形式。**これ以外（MP3・CAF・FLAC・M4A）は ffprobe で調べ、書き出す前に WAV に直す**
# （サンプルレートが 0 のままだと ffmpeg の asetrate=0 で失敗し、M4A は rubberband も読めなかった。2026-09-26）
HEADER_EXTENSIONS = (".wav", ".aif", ".aiff")


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    path: str = ""
    backend: str = ""
    message: str = ""


@dataclass(frozen=True)
class AudioProbe:
    frames: int
    sample_rate: int
    channels: int


def probe(path: Path | str) -> AudioProbe | None:
    """ffprobe で音声の長さ（サンプル数）・サンプルレート・チャンネル数を調べる。調べられなければ None。

    ptcore.audiofile が読めない形式（MP3・CAF・FLAC・M4A）のため。長さは秒 × サンプルレート（MP3 は
    前後の無音の扱いで数十サンプルずれることがある。ループなら、合わせるときに小節の区切りへそろえる）。
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        done = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries",
                               "stream=sample_rate,channels,duration", "-of", "json", str(path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, text=True,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    try:
        import json

        stream = (json.loads(done.stdout or "{}").get("streams") or [{}])[0]
        rate = int(stream.get("sample_rate") or 0)
        channels = int(stream.get("channels") or 0)
        seconds = float(stream.get("duration") or 0.0)
    except (ValueError, TypeError, IndexError):
        return None
    if rate <= 0:
        return None
    return AudioProbe(frames=int(round(seconds * rate)), sample_rate=rate, channels=channels)


def _dsp():
    return load_ptcore("dsp")


def backend_name() -> str:
    """伸縮と移調に使う道具の名前（"rubberband" / "ffmpeg" / 空文字）。"""
    dsp = _dsp()
    if dsp is None:
        return ""
    backend = dsp.choose_backend()
    return backend.name if backend.available() else ""


def describe_backend() -> str:
    """設定の画面やバージョン情報で読み上げる 1 行。"""
    name = backend_name()
    if not name:
        return tr("ffmpeg が見つからないので、テンポとキーを合わせられません（brew install ffmpeg で入ります）",
                  "ffmpeg was not found, so tempo and key cannot be matched (install it with brew install ffmpeg)")
    if name == "ffmpeg":
        return tr("加工には ffmpeg を使います。brew install rubberband を入れると音質が上がります",
                  "Processing uses ffmpeg. Installing rubberband (brew install rubberband) improves quality")
    return tr("加工には rubberband を使い、ffmpeg で仕上げます", "Processing uses rubberband, finished with ffmpeg")


def ffmpeg_filter(source_rate: int, semitones: float, ratio: float, out_rate: int, out_frames: int,
                  *, stretch: bool = True) -> str:
    """ffmpeg のフィルタ列。stretch が偽なら、伸縮と移調は済んでいるものとして仕上げだけにする。"""
    dsp = _dsp()
    parts: list[str] = []
    rate = source_rate
    if stretch and dsp is not None:
        chain = dsp.build_filter_chain(source_rate, semitones, ratio)
        if chain != "anull":
            parts.append(chain)
    if out_rate and out_rate != rate:
        parts.append(f"aresample={out_rate}")
    if out_frames > 0:
        parts.append(f"apad=whole_len={out_frames}")
        parts.append(f"atrim=end_sample={out_frames}")
    return ",".join(parts) or "anull"


def _run(command: list[str]) -> str:
    """実行して、失敗なら理由を返す（成功なら空文字）。"""
    try:
        done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT,
                              text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return tr(f"{TIMEOUT:.0f} 秒で終わりませんでした", f"did not finish in {TIMEOUT:.0f} seconds")
    except OSError as exc:
        return tr(f"{command[0]} を起動できません: {exc}", f"cannot start {command[0]}: {exc}")
    if done.returncode != 0:
        lines = (done.stderr or "").strip().splitlines()
        return lines[-1] if lines else tr(f"終了コード {done.returncode}", f"exit code {done.returncode}")
    return ""


def _header(path: Path) -> Any | None:
    """音声ヘッダ（ptcore.audiofile）。読めなければ None。**例外は投げない**（render の約束）。"""
    audiofile = load_ptcore("audiofile")
    if audiofile is None:
        return None
    try:
        return audiofile.read_audio_info(str(path))
    except Exception:
        return None


def _frames_of(path: Path) -> int | None:
    info = _header(path)
    return int(getattr(info, "frames", 0) or 0) if info is not None else None


def render(source: Path | str, dest: Path | str, plan: Any) -> RenderResult:
    """source を plan どおりに加工して dest（WAV、24 ビット）に書く。**例外は投げない**（ok を見る）。"""
    try:
        return _render(Path(source), Path(dest), plan)
    except OSError as exc:
        # 一時ファイルを作れない・名前を変えられない（ディスクがいっぱい、書き込めないフォルダ）など
        return RenderResult(False, message=tr(f"書き出せませんでした: {exc}", f"Could not write the file: {exc}"))


def _render(src: Path, out: Path, plan: Any) -> RenderResult:
    if not src.is_file():
        return RenderResult(False, message=tr(f"素材が見つかりません: {src.name}", f"Sample not found: {src.name}"))
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return RenderResult(False, message=tr("ffmpeg が見つかりません（brew install ffmpeg で入ります）",
                                                        "ffmpeg was not found (install it with brew install ffmpeg)"))
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RenderResult(False, message=tr(f"書き出す場所を作れません: {exc}", f"Cannot create the output folder: {exc}"))

    semitones = float(plan.semitones)
    ratio = float(plan.ratio)
    source_rate = int(plan.source.sample_rate)
    out_rate = int(plan.out_rate or source_rate)
    out_frames = int(plan.out_frames)
    backend = backend_name()
    stretch_needed = abs(ratio - 1.0) > 1e-9 or semitones != 0

    fd, part_name = tempfile.mkstemp(prefix=".part-", suffix=".wav", dir=str(out.parent))
    os.close(fd)
    part = Path(part_name)
    middle: Path | None = None
    decoded: Path | None = None
    try:
        used = "ffmpeg"
        if src.suffix.lower() not in HEADER_EXTENSIONS:
            # MP3・CAF・FLAC・M4A は先に WAV に直す（rubberband は M4A を読めない。サンプル数も確かめられる）
            fd, decoded_name = tempfile.mkstemp(prefix=".dec-", suffix=".wav", dir=str(out.parent))
            os.close(fd)
            decoded = Path(decoded_name)
            why = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-vn",
                        "-c:a", "pcm_s24le", str(decoded)])
            if why:
                return RenderResult(False, message=tr(f"素材を WAV に直せませんでした: {why}",
                                                                f"Could not convert the sample to WAV: {why}"))
            src = decoded
        if source_rate <= 0:
            # 計画の時点で分からなかった（ヘッダも ffprobe も読めなかった）。直した WAV のヘッダで確かめる
            source_rate = int(getattr(_header(src), "sample_rate", 0) or 0)
            if source_rate <= 0:
                return RenderResult(False, message=tr("素材のサンプルレートが分かりません", "The sample rate of the sample is unknown"))
            out_rate = int(plan.out_rate or source_rate)
        feed, feed_rate, stretch_in_ffmpeg = src, source_rate, stretch_needed
        if stretch_needed and backend == "rubberband":
            fd, middle_name = tempfile.mkstemp(prefix=".rb-", suffix=".wav", dir=str(out.parent))
            os.close(fd)
            middle = Path(middle_name)
            # --centre-focus: ステレオの中央の音の定位を保ち、モノラルにまとめても崩れにくい（モノラルのトラックに貼ることがある）
            focus = ["--centre-focus"] if int(getattr(plan.source, "channels", 0) or 0) == 2 else []
            args = ["-q", *focus, "--time", f"{ratio:.10f}", "--pitch", f"{semitones:.10f}", str(src), str(middle)]
            # --fine（R3 の方式。音楽の素材で質が高い）は rubberband 3 から。古い版なら付けずにやり直す
            why = _run(["rubberband", "--fine", *args])
            if why:
                why = _run(["rubberband", *[a for a in args if a != "--centre-focus"]])
            if why:
                return RenderResult(False, message=tr(f"rubberband が失敗しました: {why}", f"rubberband failed: {why}"))
            feed, stretch_in_ffmpeg, used = middle, False, "rubberband"
        chain = ffmpeg_filter(feed_rate, semitones, ratio, out_rate, out_frames, stretch=stretch_in_ffmpeg)
        why = _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(feed),
                    "-filter:a", chain, "-c:a", "pcm_s24le", str(part)])
        if why:
            return RenderResult(False, message=tr(f"ffmpeg が失敗しました: {why}", f"ffmpeg failed: {why}"))
        got = _frames_of(part)
        if out_frames and got is not None and got != out_frames:
            return RenderResult(False, message=tr(f"長さが合いません（{got} サンプル。{out_frames} のはず）",
                                                                f"Length mismatch ({got} samples, expected {out_frames})"))
        os.chmod(part, 0o644)          # 一時ファイルは作った人だけ読める作りなので、ほかの複製と同じにする
        os.replace(part, out)
        return RenderResult(True, path=str(out), backend=used)
    finally:
        for leftover in (part, middle, decoded):
            if leftover is not None and leftover.exists():
                try:
                    leftover.unlink()
                except OSError:
                    pass
