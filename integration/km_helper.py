"""配布版の Keyboard Maestro の補助を入れる（操作メニューの「Keyboard Maestro の補助を入れる」）。

ソースから動かすときは `tools/install_qs_helper.py --import` で入れる（python3.14 で補助を動かす）。配布版には python が
無いので、**.app の中の実行ファイルを `--helper` で呼ぶ**（app.py。画面は作らずに補助だけを動かす）。

1. マクロが呼ぶシェルスクリプト（`HELPER_SCRIPT`）を書く。中に .app の場所を書く
2. Keyboard Maestro に**まだ入っていないマクロだけ**の定義を書いて、Keyboard Maestro で開く（取り込まれる）。
   入っているものまで渡すと二重になる（Keyboard Maestro の公式マニュアル。手元の導入と同じ判断）
3. 取り込んだマクロは無効の状態で入るので、利用者が Keyboard Maestro の編集画面で有効にする

**.app を動かしても、もう一度開けば追従する**（開くたびに `refresh_script` がスクリプトを書き直す。
マクロのほうはスクリプトの場所しか知らない）。

Keyboard Maestro の定義ファイルは読むだけ。書き換えない。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Callable

from core.i18n import tr
from core.settings import SETTINGS_PATH
from integration.km_macros import (DIST_GROUP_NAME, DIST_GROUP_UID, DIST_MACROS, DIST_SAY_VARIABLE, KM_PLIST,
                                   dist_command, group_plist, helper_script, installed_macros, macro_plist)

__all__ = ["HELPER_SCRIPT", "IMPORT_FILE", "KM_APP", "write_script", "refresh_script", "install"]

HELPER_SCRIPT = SETTINGS_PATH.with_name("km_helper.sh")
IMPORT_FILE = SETTINGS_PATH.with_name("Logicサンプルローダー補助.kmmacros")
KM_APP = Path("/Applications/Keyboard Maestro.app")


def write_script(executable: str, path: Path = HELPER_SCRIPT) -> bool:
    """マクロが呼ぶスクリプトを書く（同じ中身なら書かない）。書いたら True。**不可分に書く。**"""
    text = helper_script(executable)
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    return True


def refresh_script(executable: str, path: Path = HELPER_SCRIPT) -> bool:
    """補助を入れてあるときだけ、スクリプトの中の .app の場所を、いま動いている .app に合わせる。"""
    if not path.is_file():
        return False
    try:
        return write_script(executable, path)
    except OSError:
        return False


def install(executable: str, *, script: Path = HELPER_SCRIPT, import_file: Path = IMPORT_FILE,
            km_plist: Path = KM_PLIST, km_app: Path = KM_APP,
            opener: Callable[[list[str]], object] | None = None) -> tuple[bool, str]:
    """補助を入れる。(成否, 読み上げる文)。"""
    if not km_app.exists():
        return False, tr("Keyboard Maestro が見つかりません（/Applications の中）。Logic の中で使うキー"
                         "（コントロール+シフト+L・T・P）は Keyboard Maestro が要ります。アプリだけでも、読み込みと Flex は使えます",
                         "Keyboard Maestro was not found (in /Applications). The keys used inside Logic "
                         "(Control-Shift-L, T and P) need Keyboard Maestro. Loading and Flex still work from this app")
    try:
        write_script(executable, script)
    except OSError as exc:
        return False, tr(f"[エラー] 補助のスクリプトを書けませんでした: {exc}", f"[Error] Could not write the helper script: {exc}")
    installed = installed_macros(km_plist)
    todo = [m for m in DIST_MACROS if m.uid not in installed]
    if not todo:
        on = [installed[m.uid][1] and installed[m.uid][2] for m in DIST_MACROS]
        states = tr("、".join(f"{m.label}は{'有効' if v else '無効'}" for m, v in zip(DIST_MACROS, on)),
                    ", ".join(f"{m.label} {'enabled' if v else 'disabled'}" for m, v in zip(DIST_MACROS, on)))
        return True, tr(f"Keyboard Maestro の補助はもう入っています（グループ「{DIST_GROUP_NAME}」。{states}）。"
                        "取り込み直すと二重になるので、取り込みませんでした",
                        f"The Keyboard Maestro helper is already installed (group \"{DIST_GROUP_NAME}\": {states}). "
                        "Importing again would duplicate it, so nothing was imported")
    data = group_plist(DIST_GROUP_UID, DIST_GROUP_NAME,
                       [macro_plist(m, dist_command(script, m), DIST_SAY_VARIABLE) for m in todo])
    try:
        import_file.parent.mkdir(parents=True, exist_ok=True)
        import_file.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_XML))
    except OSError as exc:
        return False, tr(f"[エラー] マクロの定義を書けませんでした: {exc}", f"[Error] Could not write the macro definitions: {exc}")
    run = opener or (lambda args: subprocess.run(args, check=True, capture_output=True))
    try:
        run(["open", "-a", str(km_app), str(import_file)])
    except (OSError, subprocess.CalledProcessError) as exc:
        return False, tr(f"[エラー] Keyboard Maestro で定義を開けませんでした: {exc}",
                         f"[Error] Could not open the definitions in Keyboard Maestro: {exc}")
    keys = tr("、", ", ").join(m.label for m in todo)
    return True, tr(f"Keyboard Maestro にマクロを {len(todo)} つ渡しました（{keys}）。取り込んだマクロは無効の状態で入ります。"
                    f"Keyboard Maestro の編集画面で、グループ「{DIST_GROUP_NAME}」のマクロを有効にしてください",
                    f"Sent {len(todo)} macros to Keyboard Maestro ({keys}). Imported macros arrive disabled. "
                    f"Enable the macros in the group \"{DIST_GROUP_NAME}\" in the Keyboard Maestro editor")
