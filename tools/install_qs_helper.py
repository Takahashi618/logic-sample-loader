#!/usr/bin/env python3.14
"""install_qs_helper.py - Quick Sampler の補助（qs_sample_menu.py）を組み立てて導入する

このプロジェクトの「ビルド」。成果物は次の 3 つで、置き場所は
`~/Library/Application Support/logic_samples/`（書類フォルダの外。Keyboard Maestro から
実行したときに書類フォルダの許可ダイアログを出さないため）。

  qs_sample_menu.py           補助の本体（リポジトリの tools/ から複製する）
  VERSION.txt                 印（版・日時・コミット）。検証が全部通ったときだけ書く
  logic_samples_qs.kmmacros   Keyboard Maestro のマクロ 3 つ（コントロール+シフト+L・T・P）

工程（仮組みしてから差し替える）:

  1. テストを全部通す
  2. 本体を仮の名前で複製する
  3. 仮の複製を、Keyboard Maestro と同じ python で起動確認する（--check）
  4. 通ったら正式な名前に差し替え、マクロのファイルを書き、最後に印を書く

失敗したら正式な成果物には触れず、仮の複製も消す。

    python3.14 tools/install_qs_helper.py            # ビルド（導入）
    python3.14 tools/install_qs_helper.py --status   # 導入の状態だけ見る
    python3.14 tools/install_qs_helper.py --import   # ビルドのあと、マクロを Keyboard Maestro に取り込む

**取り込むのは、まだ入っていないマクロだけ。** 同じグループの識別子で書いた定義を開くと、
Keyboard Maestro は既存のグループに足す（公式マニュアル）。入っているものまで入れると二重になる。
取り込んだマクロは既定で無効なので、Keyboard Maestro の画面で有効にする。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# マクロの定義の形は integration/km_macros.py 1 か所（配布版のアプリも同じものを使う）
from integration.km_macros import KM_PLIST, Macro, group_plist, installed_macros  # noqa: E402
from integration.km_macros import macro_plist as _macro_plist  # noqa: E402

SRC = ROOT / "tools" / "qs_sample_menu.py"
DEST_DIR = Path.home() / "Library" / "Application Support" / "logic_samples"
DEST = DEST_DIR / "qs_sample_menu.py"
STAMP = DEST_DIR / "VERSION.txt"
KMMACROS = DEST_DIR / "logic_samples_qs.kmmacros"
PYTHON = "/Library/Frameworks/Python.framework/Versions/3.14/bin/python3.14"

# Keyboard Maestro の識別子。取り込み直しても同じものになるよう固定する
GROUP_UID = "6F1C3A5E-3B7D-4E0A-9C21-5A1D2E7B9F40"
GROUP_NAME = "logic_samples 補助"
SAY_VARIABLE = "LogicSamples_QSSay"


# キーは LM・Logic のメニューのどちらとも重ならないものを選んだ
# （2026-09-24 と 09-25 に確認。コントロール+シフト+F は LM が使っている）
MACROS = (
    Macro("0B8E4D2A-7C6F-4A13-8E55-2D9F1B3C6A71", "Quick Sampler: サンプル読み込みのメニューを開く",
          37, "コントロール+シフト+L", ""),
    Macro("36DBF16A-BF96-4BAD-AADE-9CCAF4493E56", "Quick Sampler: Flex を切り替える（音程を変えても速さを保つ）",
          17, "コントロール+シフト+T", "--flex"),
    Macro("D7F3DBFB-4928-49F4-BDC3-C85A19BF9966",
          "Quick Sampler: Follow Tempo を切り替える（プロジェクトのテンポに合わせる）",
          35, "コントロール+シフト+P", "--follow"),
)
IMPORT_FILE = DEST_DIR / "logic_samples_qs_import.kmmacros"   # 取り込むときだけ作る（入っていない分だけ）

# 起動確認（--check）の終了コードのうち、通すもの。qs_sample_menu の EXIT_OK と EXIT_NO_LOGIC。
# **Logic が起動していないのは補助の不具合ではない**（それまでは失敗とみなし、Logic を閉じたまま
# ビルドすると工程 4 で止まっていた。2026-10-24 のレビュー）
CHECK_OK, CHECK_NO_LOGIC = 0, 2


def check_passed(returncode: int, stdout: str) -> bool:
    """仮の複製の --check が通ったか。**動いて版を名乗り、権限があり、Logic が無いだけなら通す。**"""
    return (returncode in (CHECK_OK, CHECK_NO_LOGIC) and "qs_sample_menu " in stdout
            and "権限: あり" in stdout)


def step(n: int, text: str) -> None:
    print(f"[{n}/4] {text}")


def fail(text: str) -> int:
    print(f"[失敗] {text}", file=sys.stderr)
    return 1


def git_rev() -> str:
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "tools"],
                           capture_output=True, text=True).stdout.strip()
    rev = r.stdout.strip() or "不明"
    return rev + ("（未コミットの変更あり）" if dirty else "")


def source_version() -> str:
    for line in SRC.read_text(encoding="utf-8").splitlines():
        if line.startswith("VERSION = "):
            return line.split("=", 1)[1].strip().strip('"')
    return "不明"


def macro_plist(m: Macro) -> dict:
    """マクロ 1 つの定義。補助を実行して、読み上げる 1 行を VoiceOver に渡す。"""
    # 標準出力（読み上げる 1 行）だけを変数に受ける。失敗しても exit 0 にして、
    # Keyboard Maestro の「スクリプトが失敗しました」の通知を出さない（伝えるのは次の読み上げ）
    args = f" {m.args}" if m.args else ""
    return _macro_plist(m, f'"{PYTHON}" "{DEST}"{args} --quiet; exit 0', SAY_VARIABLE)


def kmmacros_plist(macros=MACROS) -> list:
    """Keyboard Maestro に取り込めるマクロの定義（グループ 1 つ）。"""
    return group_plist(GROUP_UID, GROUP_NAME, [macro_plist(m) for m in macros])


def km_macros() -> dict:
    """Keyboard Maestro に入っているマクロ（識別子 → (グループ名, グループ有効, マクロ有効)）。読むだけ。"""
    return installed_macros(KM_PLIST)


def missing_macros() -> list:
    installed = km_macros()
    return [m for m in MACROS if m.uid not in installed]


def km_status() -> str:
    """Keyboard Maestro に取り込まれているか。定義ファイルを読むだけ。"""
    if not KM_PLIST.exists():
        return "Keyboard Maestro の定義ファイルが無い"
    installed = km_macros()
    parts = []
    for m in MACROS:
        if m.uid not in installed:
            parts.append(f"{m.label} はまだ取り込まれていない")
            continue
        gname, gactive, mactive = installed[m.uid]
        parts.append(f"{m.label} は取り込み済み（グループ「{gname}」は{'有効' if gactive else '無効'}、"
                     f"マクロは{'有効' if mactive else '無効'}）")
    return "。".join(parts)


def status() -> int:
    print(f"本体: {'あり' if DEST.exists() else 'なし'}  {DEST}")
    print(f"印: {STAMP.read_text(encoding='utf-8').strip() if STAMP.exists() else 'なし'}")
    print(f"マクロのファイル: {'あり' if KMMACROS.exists() else 'なし'}  {KMMACROS}")
    print(f"Keyboard Maestro: {km_status()}")
    return 0


def build() -> int:
    step(1, "テストを全部通す")
    # 音と読み上げは止める（テストの途中で利用者の VoiceOver を遮らない）
    env = dict(os.environ, LOGICLOADER_SILENT="1", SM_SILENT="1")
    r = subprocess.run([sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                       cwd=ROOT, capture_output=True, text=True, env=env)
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    print("    " + " / ".join(tail))
    if r.returncode != 0:
        return fail("テストが通らない。成果物には触れていない")

    step(2, "本体を仮の名前で複製する")
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_name(DEST.name + ".building")
    shutil.copy2(SRC, tmp)

    step(3, "仮の複製を、Keyboard Maestro と同じ python で起動確認する")
    if not Path(PYTHON).exists():
        tmp.unlink(missing_ok=True)
        return fail(f"python が無い: {PYTHON}")
    r = subprocess.run([PYTHON, "-X", "utf8", str(tmp), "--check", "--quiet"], capture_output=True, text=True)
    for line in r.stdout.strip().splitlines():
        print("    " + line)
    if not check_passed(r.returncode, r.stdout):
        tmp.unlink(missing_ok=True)
        return fail("起動確認が通らない。仮の複製を消した。正式な成果物は前のまま")
    if r.returncode == CHECK_NO_LOGIC:
        print("    Logic Pro が起動していないので、Quick Sampler の画面の確認は省いた")

    step(4, "正式な名前に差し替え、マクロのファイルと印を書く")
    os.replace(tmp, DEST)
    KMMACROS.write_bytes(plistlib.dumps(kmmacros_plist(), fmt=plistlib.FMT_XML))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STAMP.write_text(f"qs_sample_menu {source_version()}  ビルド {now}  コミット {git_rev()}\n", encoding="utf-8")
    stamp_time = dt.datetime.now().timestamp()
    for p in (DEST, KMMACROS, STAMP, DEST_DIR):
        os.utime(p, (stamp_time, stamp_time))
    print("    " + STAMP.read_text(encoding="utf-8").strip())
    print(f"    Keyboard Maestro: {km_status()}")
    return 0


def do_import() -> int:
    """入っていないマクロだけの定義を書いて開き、取り込む（ダブルクリックと同じ）。

    取り込んだマクロは既定で無効になる。
    """
    if not KMMACROS.exists():
        return fail("マクロのファイルが無い。先にビルドする")
    todo = missing_macros()
    if not todo:
        print(f"すべて取り込み済み。取り込み直さない（二重になるため）。{km_status()}")
        return 0
    IMPORT_FILE.write_bytes(plistlib.dumps(kmmacros_plist(todo), fmt=plistlib.FMT_XML))
    subprocess.run(["open", "-a", "Keyboard Maestro", str(IMPORT_FILE)], check=True)
    print("取り込みを依頼した: " + "、".join(m.label for m in todo)
          + "。Keyboard Maestro の画面で、マクロを有効にする必要がある（取り込んだマクロは既定で無効）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Quick Sampler の補助をビルド（導入）する")
    ap.add_argument("--status", action="store_true", help="導入の状態だけ見る")
    ap.add_argument("--import", dest="do_import", action="store_true", help="ビルドのあとマクロを取り込む")
    args = ap.parse_args(argv)
    if args.status:
        return status()
    code = build()
    if code == 0 and args.do_import:
        code = do_import()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
