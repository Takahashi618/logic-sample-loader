from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
_SOUND_DIR = Path('/System/Library/Sounds')
_SOUNDS = {'success': 'Glass.aiff', 'failure': 'Basso.aiff', 'start': 'Tink.aiff'}
_ENV_SILENT = 'SM_SILENT'
FAILURE_PREFIXES = ('[エラー]', '[警告]', '[Error]', '[Warning]')

def is_silent() -> bool:
    return os.environ.get(_ENV_SILENT, '') not in ('', '0', 'false', 'False')

def sound_path(kind: str) -> Path | None:
    name = _SOUNDS.get(kind)
    if name is None:
        return None
    path = _SOUND_DIR / name
    return path if path.exists() else None

def play(kind: str) -> bool:
    if is_silent() or sys.platform != 'darwin':
        return False
    path = sound_path(kind)
    if path is None:
        return False
    try:
        subprocess.Popen(['afplay', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False

def play_for_message(message: str) -> bool:
    if message.startswith(FAILURE_PREFIXES):
        return play('failure')
    return play('success')
