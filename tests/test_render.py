"""integration/render.py の単体テスト。**音は一時フォルダの中で作る**（Splice にも ~/Music にも触らない）。

ffmpeg が無い環境では、実際に書き出すテストは飛ばす。
"""

from __future__ import annotations

import math
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import fit  # noqa: E402
from integration import render  # noqa: E402


def _write_tone(path: Path, seconds: float, rate: int = 44100) -> int:
    """440 Hz のステレオ 16 ビットを書く。サンプル数を返す。"""
    frames = int(round(seconds * rate))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            v = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            data += struct.pack("<hh", v, v)
        w.writeframes(bytes(data))
    return frames


class ToolPathTest(unittest.TestCase):
    def test_homebrew_is_added_for_finder_launches(self):
        # Finder から開いたアプリの PATH には Homebrew が無い（launchd の既定）
        import os
        saved = os.environ.get("PATH", "")
        try:
            os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
            render.ensure_tool_path()
            parts = os.environ["PATH"].split(os.pathsep)
            for d in render.TOOL_DIRS:
                if os.path.isdir(d):
                    self.assertIn(d, parts)
            self.assertEqual(parts[:4], ["/usr/bin", "/bin", "/usr/sbin", "/sbin"])   # 前のものは順番どおり
            render.ensure_tool_path()
            self.assertEqual(len(os.environ["PATH"].split(os.pathsep)), len(parts))  # 二重に足さない
        finally:
            os.environ["PATH"] = saved


class FilterTest(unittest.TestCase):
    def test_chain_ends_with_exact_length(self):
        chain = render.ffmpeg_filter(44100, 3, 0.85, 44100, 176400)
        self.assertTrue(chain.startswith("asetrate=52444,aresample=44100,atempo="))
        self.assertTrue(chain.endswith("apad=whole_len=176400,atrim=end_sample=176400"))

    def test_rate_conversion_and_finish_only(self):
        self.assertEqual(render.ffmpeg_filter(48000, 0, 1.0, 44100, 1000),
                         "aresample=44100,apad=whole_len=1000,atrim=end_sample=1000")
        # rubberband で伸縮と移調を済ませたあとは、仕上げだけ
        self.assertEqual(render.ffmpeg_filter(44100, 3, 0.85, 44100, 5, stretch=False),
                         "apad=whole_len=5,atrim=end_sample=5")


class RubberbandCommandTest(unittest.TestCase):
    """rubberband に渡す引数（実行はしない）。"""

    def test_fine_engine_centre_focus_and_fallback(self):
        tmp = Path(tempfile.mkdtemp())
        src = tmp / "Tone.wav"
        frames = _write_tone(src, 0.2)
        plan = fit.plan_fit(fit.Source(name=src.name, frames=frames, sample_rate=44100, channels=2, kind="one_shot",
                                       key="F#", key_quality="minor"), fit.Goal(key="Am", sample_rate=44100))
        calls = []

        def fake_run(command):
            calls.append(command)
            if command[0] == "rubberband" and "--fine" in command:
                return "unknown option --fine"          # 古い rubberband のつもり
            if command[0] == "rubberband":
                Path(command[-1]).write_bytes(src.read_bytes())
                return ""
            shutil.copyfile(src, command[-1])            # ffmpeg の仕上げのつもり
            return ""

        saved = (render._run, render.backend_name, render._frames_of)
        render._run, render.backend_name = fake_run, lambda: "rubberband"
        render._frames_of = lambda path: plan.out_frames
        try:
            res = render.render(src, tmp / "out.wav", plan)
        finally:
            render._run, render.backend_name, render._frames_of = saved
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.backend, "rubberband")
        first, second = calls[0], calls[1]
        self.assertEqual(first[:4], ["rubberband", "--fine", "-q", "--centre-focus"])   # R3、ステレオの中央を保つ
        self.assertNotIn("--fine", second)                   # 古い版なら付けずにやり直す
        self.assertIn("--pitch", second)
        self.assertEqual(second[second.index("--pitch") + 1], f"{3:.10f}")
        self.assertEqual(calls[2][0].rsplit("/", 1)[-1], "ffmpeg")                  # 仕上げは ffmpeg


@unittest.skipIf(shutil.which("ffmpeg") is None, "ffmpeg が無い")
class RenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "Tone_102_F#min.wav"
        self.frames = _write_tone(self.src, 8 * 60 / 102)       # 102 BPM の 2 小節

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _plan(self, goal: fit.Goal) -> fit.FitPlan:
        source = fit.Source(name=self.src.name, frames=self.frames, sample_rate=44100, kind="loop", bpm=102,
                            bpm_confident=True, key="F#", key_quality="minor", key_confident=True)
        return fit.plan_fit(source, goal)

    def test_exact_length_and_no_leftovers(self):
        plan = self._plan(fit.Goal(tempo=120.0, key="Cmaj", sample_rate=44100))
        out = self.tmp / "out" / "Tone__120bpm_even_+3_Am.wav"
        res = render.render(self.src, out, plan)
        self.assertTrue(res.ok, res.message)
        from core.sibling import load_ptcore
        info = load_ptcore("audiofile").read_audio_info(str(out))
        self.assertEqual((info.frames, info.sample_rate, info.channels, info.bit_depth), (176400, 44100, 2, 24))
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), [out.name])   # 一時ファイルが残らない
        self.assertEqual(out.stat().st_mode & 0o777, 0o644)

    def test_rate_conversion(self):
        plan = self._plan(fit.Goal(tempo=120.0, sample_rate=48000))
        out = self.tmp / "Tone__120bpm_48k.wav"
        res = render.render(self.src, out, plan)
        self.assertTrue(res.ok, res.message)
        from core.sibling import load_ptcore
        info = load_ptcore("audiofile").read_audio_info(str(out))
        self.assertEqual((info.frames, info.sample_rate), (192000, 48000))     # 4 秒ちょうど

    def test_non_wav_sources_are_probed_and_decoded(self):
        # MP3・CAF・FLAC・M4A は ptcore が読めない。ffprobe で調べ、書き出す前に WAV に直す（2026-09-26 のレビュー）
        import subprocess
        from ui import controller as C

        for ext in ("m4a", "mp3", "caf"):
            with self.subTest(ext=ext):
                other = self.tmp / f"Tone_102_F#min.{ext}"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(self.src), str(other)],
                               check=True)
                c = C.AppController(settings_path=self.tmp / "s.json", stage_root=self.tmp / "st",
                                    preview_root=self.tmp / "pv")
                source = c.source_for(other, kind="loop")
                self.assertEqual(source.sample_rate, 44100)
                self.assertAlmostEqual(source.frames / 44100, self.frames / 44100, delta=0.1)
                plan = fit.plan_fit(source, fit.Goal(tempo=120.0, key="Cmaj", sample_rate=44100))
                self.assertEqual(plan.tag(), "120bpm_even_+3_Am")            # 「_44.1k」は付かない（レートは同じ）
                for forced in ("ffmpeg", "rubberband"):
                    if shutil.which(forced) is None:
                        continue
                    saved = render.backend_name
                    render.backend_name = lambda f=forced: f
                    try:
                        res = render.render(other, self.tmp / f"out_{ext}_{forced}.wav", plan)
                    finally:
                        render.backend_name = saved
                    self.assertTrue(res.ok, f"{forced}: {res.message}")
                    from core.sibling import load_ptcore
                    info = load_ptcore("audiofile").read_audio_info(res.path)
                    self.assertEqual(info.frames, 176400)                # ちょうど 2 小節
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.name.startswith(".")], [])   # 一時ファイルが残らない

    def test_os_errors_become_results(self):
        plan = self._plan(fit.Goal(tempo=120.0, sample_rate=44100))
        blocked = self.tmp / "file_not_folder"
        blocked.write_text("x")
        res = render.render(self.src, blocked / "out.wav", plan)     # 書き出す先がファイル（フォルダを作れない）
        self.assertFalse(res.ok)
        saved = render.tempfile.mkstemp

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        render.tempfile.mkstemp = boom
        try:
            res = render.render(self.src, self.tmp / "out.wav", plan)
        finally:
            render.tempfile.mkstemp = saved
        self.assertFalse(res.ok)
        self.assertIn("書き出せませんでした", res.message)          # 例外を投げない

    def test_missing_source(self):
        res = render.render(self.tmp / "nope.wav", self.tmp / "x.wav", self._plan(fit.Goal(tempo=120.0)))
        self.assertFalse(res.ok)
        self.assertIn("見つかりません", res.message)


if __name__ == "__main__":
    unittest.main()
