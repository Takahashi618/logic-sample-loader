"""Quick Sampler の操作。**中身は `tools/qs_sample_menu.py` にある。**

Keyboard Maestro の補助（コントロール+シフト+L・T・P）と同じファイルを使う。
Quick Sampler の画面の寸法や、届かない部品の探し方は Logic の版で変わりうるので、
**直す場所を 1 か所にしておく**（補助とアプリで食い違うと、片方だけ動かなくなる）。

`tools/` はパッケージではないので、ファイルの場所を指定して読む。
読めなければ None を返し、アプリは「Quick Sampler を操作できません」と伝える。
"""

from __future__ import annotations

import os
import subprocess
from types import ModuleType

from core.sibling import PROJECT_ROOT, load_file_module

__all__ = [
    "ENGINE_PATH",
    "engine",
    "is_trusted",
    "request_trust_prompt",
    "open_accessibility_settings",
    "return_to_self",
]

ENGINE_PATH = PROJECT_ROOT / "tools" / "qs_sample_menu.py"

# システム設定の「アクセシビリティ」の画面
ACCESSIBILITY_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


def engine() -> ModuleType | None:
    """Quick Sampler を操作する部品。読めなければ None。"""
    return load_file_module(ENGINE_PATH, "_logicloader_qs_engine")


def is_trusted() -> bool:
    """このアプリにアクセシビリティの許可があるか。"""
    try:
        import ApplicationServices as AS
    except ImportError:
        return False
    return bool(AS.AXIsProcessTrusted())


def request_trust_prompt() -> None:
    """macOS に「このアプリがアクセシビリティを求めている」と知らせる（許可の一覧に載せる）。

    **許可を与えるのは利用者本人。** これはシステム設定の一覧にアプリを載せるだけで、
    入り切りはしない。利用者が明示的に頼んだときだけ呼ぶ。
    """
    try:
        import ApplicationServices as AS
        AS.AXIsProcessTrustedWithOptions({AS.kAXTrustedCheckOptionPrompt: True})
    except Exception:
        pass


def open_accessibility_settings() -> bool:
    """システム設定のアクセシビリティの画面を開く（入り切りはしない）。"""
    try:
        subprocess.run(["open", ACCESSIBILITY_PANE], check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def return_to_self() -> bool:
    """このアプリを最前面に戻す。読み込みで Logic を前に出したあとに使う。"""
    qs = engine()
    if qs is None:
        return False
    try:
        return bool(qs.bring_to_front(os.getpid()))
    except Exception:
        return False
