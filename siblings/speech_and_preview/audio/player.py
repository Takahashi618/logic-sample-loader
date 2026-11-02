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
        if self._current_path is None:
            return 'ファイルが読み込まれていません'
        name = Path(self._current_path).name
        if not self._backend.active:
            return f'{name}、停止中'
        state = '一時停止中' if self._backend.paused else '再生中'
        pos = self._backend.curr_pos
        dur = self._backend.duration
        pos_txt = f'{pos:.1f}秒目' if isinstance(pos, (int, float)) else '位置不明'
        if isinstance(dur, (int, float)) and dur > 0:
            return f'{name}、{state}、{pos_txt}、全長{dur:.1f}秒'
        return f'{name}、{state}、{pos_txt}'
