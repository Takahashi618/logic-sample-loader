"""Keyboard Maestro の補助のマクロ（コントロール+シフト+L・T・P）の定義を作る。**定義の形はここ 1 か所。**

使うところは 2 つ。どちらも同じ形の定義を作り、違うのは「補助をどう呼ぶか」（シェルの 1 行）だけ:

| 使うところ | 呼び方 | グループ |
| --- | --- | --- |
| ソースから動かすときの導入（`tools/install_qs_helper.py`） | python3.14 で `~/Library/Application Support/logic_samples/qs_sample_menu.py` | logic_samples 補助 |
| 配布版のアプリ（操作メニューの「Keyboard Maestro の補助を入れる」） | `.app` の中の実行ファイルを `--helper` で（python は要らない） | Logicサンプルローダー 補助 |

**配布版のマクロは .app の場所を直接書かない。** 小さなシェルスクリプト（`helper_script`）を呼び、その中に
.app の場所を書く。アプリを開くたびに書き直すので、.app を動かしても、もう一度開けば追従する
（Keyboard Maestro の定義を取り込み直さずに済む。取り込み直すとマクロが二重になる）。

マクロは、補助の標準出力（読み上げる 1 行）を変数に受け、VoiceOver に読ませる（LM と同じ
`Application("VoiceOver").output`）。失敗しても `exit 0` で終え、Keyboard Maestro の失敗の通知を出さない。
**取り込んだマクロは無効の状態で入る**（Keyboard Maestro の安全策）。利用者が編集画面で有効にする。

OS 非依存（plist を組み立てるだけ。Keyboard Maestro の定義ファイルは読むだけ）。
"""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

from core.i18n import tr

__all__ = [
    "Macro",
    "KM_PLIST",
    "CONTROL_SHIFT",
    "LOGIC_TARGET",
    "macro_plist",
    "group_plist",
    "installed_macros",
    "DIST_GROUP_UID",
    "DIST_GROUP_NAME",
    "DIST_MACROS",
    "DIST_SAY_VARIABLE",
    "helper_script",
    "dist_command",
]

KM_PLIST = Path.home() / "Library/Application Support/Keyboard Maestro/Keyboard Maestro Macros.plist"
CONTROL_SHIFT = 4096 + 512         # コントロール + シフト（Carbon の値）
# Logic Pro の中だけで効かせる
LOGIC_TARGET = {"Targeting": "Included",
                "TargetingApps": [{"BundleIdentifier": "com.apple.logic10", "Name": "Logic Pro",
                                   "NewFile": "/Applications/Logic Pro.app"}]}


@dataclass(frozen=True)
class Macro:
    uid: str
    name: str
    keycode: int
    label: str
    args: str                      # 補助に渡す引数（--quiet は別に付ける）


def macro_plist(m: Macro, command: str, say_variable: str) -> dict:
    """マクロ 1 つの定義。command（シェルの 1 行）で補助を実行し、読み上げる 1 行を VoiceOver に渡す。"""
    speak = (f'var kme = Application("Keyboard Maestro Engine");\n'
             f'var msg = kme.getvariable("{say_variable}");\n'
             f'if (msg && msg.length) {{ Application("VoiceOver").output(msg); }}')
    return {
        "UID": m.uid,
        "Name": m.name,
        "IsActive": True,
        "Triggers": [{"MacroTriggerType": "HotKey", "KeyCode": m.keycode,
                      "Modifiers": CONTROL_SHIFT, "FireType": "Pressed"}],
        "Actions": [
            {   # 補助を実行し、読み上げる 1 行を変数に受ける。失敗しても止めない（exit 0）
                "MacroActionType": "ExecuteShellScript",
                "Text": command,
                "UseText": True,
                "Path": "",
                "Source": "Nothing",
                "DisplayKind": "Variable",
                "Variable": say_variable,
                "IncludeStdErr": False,
                "IncludedVariables": ["9999"],
                "TrimResults": True,
                "TrimResultsNew": True,
                "HonourFailureSettings": True,
                "TimeOutAbortsMacro": True,
            },
            {   # 1 行があれば VoiceOver に読ませる（LM と同じ方法）
                "MacroActionType": "ExecuteJavaScriptForAutomation",
                "Text": speak,
                "UseText": True,
                "UseModernSyntax": True,
                "Path": "",
                "DisplayKind": "None",
                "IncludeStdErr": False,
                "IncludedVariables": ["9999"],
                "TrimResults": True,
                "TrimResultsNew": True,
                "HonourFailureSettings": True,
                "TimeOutAbortsMacro": True,
            },
        ],
    }


def group_plist(uid: str, name: str, macros: list[dict]) -> list:
    """Keyboard Maestro に取り込めるマクロの定義（グループ 1 つ。Logic の中だけで効く）。"""
    return [{
        "UID": uid,
        "Name": name,
        "IsActive": True,
        "Activate": "Normal",
        "Targeting": LOGIC_TARGET,
        "Macros": macros,
    }]


def installed_macros(path: Path = KM_PLIST) -> dict:
    """Keyboard Maestro に入っているマクロ（識別子 → (グループ名, グループ有効, マクロ有効)）。**読むだけ。**

    定義ファイルが無い・読めないときは空（Keyboard Maestro が無い、まだ一度も起動していない）。
    """
    try:
        data = plistlib.loads(path.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    found = {}
    for g in data.get("MacroGroups", []) if isinstance(data, dict) else []:
        for m in g.get("Macros", []):
            found[m.get("UID")] = (g.get("Name"), g.get("IsActive", True), m.get("IsActive", True))
    return found


# --------------------------------------------------------------------------
# 配布版（PyInstaller で固めた .app）のマクロ。**手元の導入（logic_samples 補助）とは識別子を分ける**
# --------------------------------------------------------------------------

DIST_GROUP_UID = "9602746D-5359-468F-9C3D-F61769FFD212"
# 名前は表示の言語に合わせる（入っているかは識別子で見るので、言語を変えても二重にならない）
DIST_GROUP_NAME = tr("Logicサンプルローダー 補助", "Logic Sample Loader Helper")
DIST_SAY_VARIABLE = "LogicSampleLoader_Say"
DIST_MACROS = (
    Macro("4E7F8B94-52E8-408B-BA65-55669E9F6B4B",
          tr("Quick Sampler: サンプル読み込みのメニューを開く", "Quick Sampler: Open the sample load menu"),
          37, tr("コントロール+シフト+L", "Control-Shift-L"), ""),
    Macro("728E1666-9F00-4CEC-A62F-58C17A109DDC",
          tr("Quick Sampler: Flex を切り替える（音程を変えても速さを保つ）",
             "Quick Sampler: Toggle Flex (keep the speed when changing pitch)"),
          17, tr("コントロール+シフト+T", "Control-Shift-T"), "--flex"),
    Macro("A64E6FBC-CE16-42DA-B825-C39306860C54",
          tr("Quick Sampler: Follow Tempo を切り替える（プロジェクトのテンポに合わせる）",
             "Quick Sampler: Toggle Follow Tempo (follow the project tempo)"),
          35, tr("コントロール+シフト+P", "Control-Shift-P"), "--follow"),
)


def helper_script(executable: str) -> str:
    """マクロが呼ぶシェルスクリプトの中身。**.app の場所はここにだけ書く**（アプリを開くたびに書き直す）。"""
    quoted = "'" + executable.replace("'", "'\"'\"'") + "'"
    return ("#!/bin/sh\n"
            "# Logicサンプルローダーの補助（Keyboard Maestro のマクロが呼ぶ）。\n"
            "# アプリを開くたびに書き直す（.app を動かしても、もう一度開けば追従する）\n"
            f"EXE={quoted}\n"
            'if [ ! -x "$EXE" ]; then\n'
            f'  echo "{tr("Logicサンプルローダーが見つかりません。アプリを一度開いてから、もう一度押してください", "Logic Sample Loader was not found. Open the app once, then press the key again")}"\n'
            "  exit 0\n"
            "fi\n"
            'exec "$EXE" --helper "$@"\n')


def dist_command(script: Path, m: Macro) -> str:
    """配布版のマクロのシェルの 1 行。"""
    args = f" {m.args}" if m.args else ""
    return f'/bin/sh "{script}"{args} --quiet; exit 0'
