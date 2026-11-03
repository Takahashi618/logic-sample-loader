from __future__ import annotations
from pathlib import Path
from typing import Protocol, runtime_checkable

@runtime_checkable
class PlayerBackend(Protocol):

    def load_file(self, path: str) -> None:
        ...

    def play(self) -> None:
        ...

    def pause(self) -> None:
        ...

    def resume(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def seek(self, pos: float) -> None:
        ...

    def set_volume(self, volume: float) -> None:
        ...

    @property
    def active(self) -> bool:
        ...

    @property
    def playing(self) -> bool:
        ...

    @property
    def paused(self) -> bool:
        ...

    @property
    def curr_pos(self) -> float:
        ...

    @property
    def duration(self) -> float:
        ...

class NullBackend:

    def __init__(self) -> None:
        self._loaded: str | None = None
        self._active = False
        self._paused = False
        self._pos = 0.0
        self._duration = 0.0
        self._volume = 1.0

    def load_file(self, path: str) -> None:
        self._loaded = path
        self._active = False
        self._paused = False
        self._pos = 0.0
        self._duration = 5.0

    def play(self) -> None:
        if self._loaded is None:
            return
        self._active = True
        self._paused = False
        self._pos = 0.0

    def pause(self) -> None:
        if self._active:
            self._paused = True

    def resume(self) -> None:
        if self._active:
            self._paused = False

    def stop(self) -> None:
        self._active = False
        self._paused = False
        self._pos = 0.0

    def seek(self, pos: float) -> None:
        if self._active:
            self._pos = max(0.0, min(pos, self._duration))

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(volume, 1.0))

    @property
    def active(self) -> bool:
        return self._active

    @property
    def playing(self) -> bool:
        return self._active and (not self._paused)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def curr_pos(self) -> float:
        return self._pos

    @property
    def duration(self) -> float:
        return self._duration

def create_default_backend() -> PlayerBackend:
    try:
        from just_playback import Playback
    except ImportError as exc:
        raise RuntimeError("just_playback が見つかりません。'pip install just_playback' を実行してください。") from exc
    return Playback()
_WORDS_JA = {'no_file': 'ファイルが読み込まれていません', 'sep': '、', 'stopped': '停止中', 'paused': '一時停止中', 'playing': '再生中', 'position': '{:.1f}秒目', 'no_position': '位置不明', 'length': '全長{:.1f}秒'}
_WORDS_EN = {'no_file': 'No file is loaded', 'sep': ', ', 'stopped': 'stopped', 'paused': 'paused', 'playing': 'playing', 'position': 'at {:.1f} seconds', 'no_position': 'position unknown', 'length': 'length {:.1f} seconds'}

class PreviewPlayer:
    SEEK_STEP = 1.0

    def __init__(self, backend: PlayerBackend | None=None) -> None:
        self._backend = backend if backend is not None else create_default_backend()
        self._current_path: str | None = None

    @property
    def current_path(self) -> str | None:
        return self._current_path

    @property
    def is_active(self) -> bool:
        try:
            return bool(self._backend.active)
        except Exception:
            return False

    def load(self, path: str | Path) -> None:
        path_str = str(path)
        self._backend.load_file(path_str)
        self._current_path = path_str

    def play(self) -> None:
        self._backend.play()

    def stop(self) -> None:
        self._backend.stop()

    def toggle_play_stop(self) -> None:
        if self._backend.active:
            self._backend.stop()
        else:
            self._backend.play()

    def toggle_pause(self) -> None:
        if not self._backend.active:
            return
        if self._backend.paused:
            self._backend.resume()
        else:
            self._backend.pause()

    def seek_relative(self, delta: float) -> None:
        if not self._backend.active:
            self._backend.play()
        target = self._backend.curr_pos + delta
        self._backend.seek(max(0.0, target))

    def seek_to(self, pos: float) -> None:
        if not self._backend.active:
            self._backend.play()
        self._backend.seek(max(0.0, pos))

    def set_volume(self, volume: float) -> None:
        self._backend.set_volume(volume)

    def describe_state_ja(self) -> str:
        return self._describe_state(_WORDS_JA)

    def describe_state_en(self) -> str:
        return self._describe_state(_WORDS_EN)

    def _describe_state(self, words: dict[str, str]) -> str:
        if self._current_path is None:
            return words['no_file']
        name = Path(self._current_path).name
        sep = words['sep']
        if not self._backend.active:
            return f"{name}{sep}{words['stopped']}"
        state = words['paused'] if self._backend.paused else words['playing']
        pos = self._backend.curr_pos
        dur = self._backend.duration
        pos_txt = words['position'].format(pos) if isinstance(pos, (int, float)) else words['no_position']
        if isinstance(dur, (int, float)) and dur > 0:
            length = words['length'].format(dur)
            return f'{name}{sep}{state}{sep}{pos_txt}{sep}{length}'
        return f'{name}{sep}{state}{sep}{pos_txt}'
