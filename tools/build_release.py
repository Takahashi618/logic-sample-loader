#!/usr/bin/env python3.14
"""配布物（zip）を作る。**確かめる → 組み立てる → 並べる → zip → zip の中身を検証**の順。

    python3.14 -X utf8 tools/build_release.py             # dist/Logicサンプルローダー_<版>.zip を作る
    python3.14 -X utf8 tools/build_release.py --dirty     # 試し（書きかけの変更があっても止めない。コミットに + が付く）
    python3.14 -X utf8 tools/build_release.py --app-only  # .app の組み立てと確認だけ（zip は作らない）

配布版は **PyInstaller で固めた .app**
（python も wx も中に入る。受け取った人は python を入れなくてよい）。

工程（どこかで落ちたら zip を作らない）:

  1. 書きかけの変更が無いか     同梱するソースと、配布物の印（コミット）をずらさない
  2. テスト                     tests/（音と読み上げは止める）
  3. マニュアル                 tools/doc_parts.py --check（コードから作る部分が古くないか）と check_html.py
  4. .app の組み立て            PyInstaller（tools/release.spec）。build/release/ に作る
  5. .app の確認                中の実行ファイルで --selftest。署名が ad-hoc で、証明書の名前が入っていないか
  6. 同梱部品のライセンス       PyInstaller の目録（toc）と各部品の RECORD から作る（手書きは必ず漏れる）
  7. 並べる                     はじめにお読みください・マニュアル（日本語と英語）・ライセンス・.app
  8. zip                        ditto（.app の中のシンボリックリンクと署名を壊さない。zipfile は壊す）
  9. zip の検証                 展開して署名と --selftest、並び、txt の形（BOM・CRLF）、個人の情報が無いか

**署名は ad-hoc。** 手元の証明書で署名すると、証明書の名前が配布物に入る。
ad-hoc だと、受け取った人が新しい版に入れ替えたときにアクセシビリティの許可を入れ直すことになる（マニュアルに書く）。
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.sibling import BUNDLE_PARTS, find_sibling  # noqa: E402
from core.version import VERSION  # noqa: E402

APP_NAME = "Logicサンプルローダー"
BUILD = ROOT / "build" / "release"
DIST = ROOT / "dist"
SPEC = ROOT / "tools" / "release.spec"
APP = BUILD / "dist" / f"{APP_NAME}.app"
WORK = BUILD / "work"
STAGE = BUILD / "stage"
FOLDER = f"{APP_NAME}_{VERSION}"
ZIP = DIST / f"{FOLDER}.zip"
DOCS = ROOT / "docs"

# 配布物の直下に並べるもの（はじめにお読みくださいの説明と合わせる。ずれたら止める）
# 英語の 3 つは 2026-10-27 から（海外の利用者向け。アプリの画面も Mac の言語で英語になる）
TOP_FILES = ("はじめにお読みください.txt", "マニュアル.html", "マニュアル.txt", "ライセンス.txt", "同梱部品のライセンス.txt",
             "README (English).txt", "Manual (English).html", "Manual (English).txt")
TOP_APPS = (f"{APP_NAME}.app",)

# 検査の検索語（個人の情報・関係の無い名前）は**ソースに書かない**（検索語そのものが個人の情報になる）。
# 作者の手元の release_private.json にだけ置く。無ければ、その検査はしない（公開しているソースから組み立てるとき）
PRIVATE_FILE = ROOT / "release_private.json"


def _private_lists() -> dict:
    try:
        data = json.loads(PRIVATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_PRIVATE = _private_lists()
# 配布物に入っていてはいけないもの（作者の名前など）。**大文字小文字を問わず、全部のファイルで探す**
PERSONAL = tuple(w.encode() for w in _PRIVATE.get("personal", ()))
# 文字のファイル（自分たちで書いたもの）でだけ探すもの。python 本体や標準ライブラリの中には、
# 関係のない人のアドレスやパスの例が入っている（2026-10-24 に Python.framework と base_library.zip で当たった）
PERSONAL_TEXT = tuple(w.encode() for w in _PRIVATE.get("personal_text", ()))
TEXT_SUFFIXES = (".py", ".txt", ".html", ".md", ".plist", ".sh", ".json", ".kmmacros")

# 配布物の中の、自分たちで書いた文字のファイル（.app の中の平文の .py も）に入っていてはいけない言葉。
# 同梱する .py はコメントと説明文を除いてから入れる（clean_source）
PRIVATE_WORDS = tuple(_PRIVATE.get("words", ()))
PRIVATE_SUFFIXES = (".py", ".txt", ".html", ".sh", ".json", ".plist", ".strings")
# 借りている部品の、コメントではない文字列の書き換え（部品の中の場所 → [元, 新]）。関係の無い話を出さないため
BORROWED_EDITS = _PRIVATE.get("borrowed_edits", {})

RESULTS: list[tuple[str, bool, str]] = []


def clean_part(rel: str, text: str) -> str:
    """借りている部品（rel は部品の中の場所）の写しを作る。文字列の書き換え（BORROWED_EDITS）のあと clean_source。"""
    for old, new in BORROWED_EDITS.get(rel, ()):
        if old not in text:
            raise ValueError(f"{rel} に書き換える文字列がありません（借り先が変わった？）: {old}")
        text = text.replace(old, new)
    return clean_source(text)


def clean_source(text: str) -> str:
    """同梱する .py から、**コメントと説明文（docstring）を除く**（動きは変えない）。

    手元の版の .py には、開発の経過（実測の日付、ほかのプロジェクトや手元のメモの名前）がコメントとして残っている。
    配布物の .app の中には平文のまま入るので、それを出さない。ast で読んで書き直すとコメントは消える。説明文は
    関数・クラス・モジュールの先頭の文字列で、消すと中身が空になるところは pass にする。
    """
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                body.pop(0)
                if not body and not isinstance(node, ast.Module):
                    body.append(ast.Pass())
    return ast.unparse(tree) + "\n"


def step(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'OK' if ok else 'NG'}  {name}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def run(args: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], capture_output=True, text=True, **kw)


def last_line(done: subprocess.CompletedProcess) -> str:
    for text in (done.stderr, done.stdout):
        lines = (text or "").strip().splitlines()
        if lines:
            return lines[-1]
    return "出力なし"


def silent_env() -> dict:
    return dict(os.environ, LOGICLOADER_SILENT="1", SM_SILENT="1")


# --------------------------------------------------------------------------
# 1〜3. 確かめる
# --------------------------------------------------------------------------

def git_commit() -> str:
    commit = run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).stdout.strip() or "?"
    dirty = run(["git", "status", "--porcelain"], cwd=ROOT).stdout.strip()
    return commit + ("+" if dirty else "")


def check_clean(allow_dirty: bool) -> bool:
    dirty = [line for line in run(["git", "status", "--porcelain"], cwd=ROOT).stdout.splitlines() if line.strip()]
    if dirty and not allow_dirty:
        return step("1. 書きかけの変更", False, f"{len(dirty)} 件（コミットしてから作る。試すなら --dirty）: {dirty[0]}")
    return step("1. 書きかけの変更", True, "なし" if not dirty else f"{len(dirty)} 件あるが --dirty で続ける")


def run_tests() -> bool:
    done = run([sys.executable, "-X", "utf8", "-m", "unittest", "discover", "-s", "tests", "-t", "."],
               cwd=ROOT, env=silent_env())
    summary = next((ln for ln in reversed((done.stderr or "").splitlines()) if ln.startswith("Ran ")), "")
    return step("2. テスト", done.returncode == 0, f"{summary}、{last_line(done)}")


def check_docs(allow_dirty: bool) -> bool:
    parts = run([sys.executable, "-X", "utf8", ROOT / "tools" / "doc_parts.py", "--check"], cwd=ROOT, env=silent_env())
    if parts.returncode != 0:
        if not allow_dirty:
            return step("3. マニュアル", False, "コードから作る部分が古い。tools/doc_parts.py で作り直してコミットする")
        parts = run([sys.executable, "-X", "utf8", ROOT / "tools" / "doc_parts.py"], cwd=ROOT, env=silent_env())
        if parts.returncode != 0:
            return step("3. マニュアル", False, last_line(parts))
    html = run([sys.executable, "-X", "utf8", ROOT / "tools" / "check_html.py", DOCS / "manual.html", DOCS / "manual_en.html"],
               cwd=ROOT)
    if html.returncode != 0:
        return step("3. マニュアル", False, "check_html: " + last_line(html))
    return step("3. マニュアル", True, "コードと合っている。構造の検査も通過")


# --------------------------------------------------------------------------
# 4〜5. 組み立てて確かめる
# --------------------------------------------------------------------------

def build_app() -> bool:
    if BUILD.exists():
        shutil.rmtree(BUILD)
    env = dict(os.environ, LL_BUILD_DATE=time.strftime("%Y-%m-%d %H:%M"), LL_GIT_COMMIT=git_commit())
    done = run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                "--distpath", BUILD / "dist", "--workpath", WORK, SPEC], cwd=ROOT, env=env)
    if done.returncode != 0 or not APP.is_dir():
        return step("4. .app の組み立て", False, last_line(done))
    size = sum(p.stat().st_size for p in APP.rglob("*") if p.is_file() and not p.is_symlink())
    return step("4. .app の組み立て", True, f"{size / 1e6:.0f} MB、コミット {env['LL_GIT_COMMIT']}")


def selftest(app: Path) -> tuple[bool, str]:
    exe = app / "Contents" / "MacOS" / "LogicSampleLoader"
    try:
        done = run([exe, "--selftest"], timeout=180, env=silent_env())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    try:
        report = json.loads((done.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, last_line(done)
    if done.returncode != 0 or not report.get("ok") or not report.get("frozen") or report.get("version") != VERSION:
        return False, json.dumps(report, ensure_ascii=False)
    return True, f"版 {report['version']}、補助 {report['helper']}、python {report['python']}"


def signature_problems(app: Path) -> list[str]:
    """署名が壊れていないか、ad-hoc か（証明書の名前が入っていないか）。"""
    problems = []
    verify = run(["codesign", "--verify", "--deep", "--strict", app])
    if verify.returncode != 0:
        problems.append("署名の検証: " + last_line(verify))
    info = run(["codesign", "-dvv", app]).stderr
    if "Signature=adhoc" not in info:
        problems.append("ad-hoc の署名ではない")
    if "Authority=" in info:
        problems.append("証明書の名前が入っている")
    return problems


OWN_PACKAGES = ("app", "core", "ui", "integration")


def compiled_hits() -> list[str]:
    """.app に入れた自分たちのコンパイル済みのコード（PYZ）の文字列に、関係の無い名前や個人の情報が無いか。

    PYZ は圧縮されているので、ファイルを grep しても見えない。PyInstaller の読み取り部品で取り出して、
    コードの定数（説明文・文字列）を見る。説明文は写しの段階で除いてある（release.spec の clean_source）。
    """
    import types

    from PyInstaller.archive.readers import ZlibArchiveReader

    def strings(code):
        for c in code.co_consts:
            if isinstance(c, str):
                yield c
            elif isinstance(c, types.CodeType):
                yield from strings(c)

    words = PRIVATE_WORDS + tuple(w.decode() for w in PERSONAL)
    hits = []
    for pyz in sorted(WORK.rglob("PYZ-*.pyz")):
        reader = ZlibArchiveReader(str(pyz))
        for name in sorted(reader.toc):
            if name.split(".")[0] not in OWN_PACKAGES:
                continue
            code = reader.extract(name)
            text = "\n".join(strings(code)).lower() if isinstance(code, types.CodeType) else ""
            found = [w for w in words if w.lower() in text]
            if found:
                hits.append(f"{name}（{'・'.join(found)}）")
    return hits


def check_app() -> bool:
    ok, detail = selftest(APP)
    if not ok:
        return step("5. .app の確認", False, f"--selftest: {detail}")
    hits = compiled_hits()
    if hits:
        return step("5. .app の確認", False, f"コンパイルしたコードに関係の無い名前や個人の情報: {'、'.join(hits[:5])}")
    problems = signature_problems(APP)
    if problems:
        return step("5. .app の確認", False, "、".join(problems))
    return step("5. .app の確認", True, f"{detail}。署名は ad-hoc。コンパイルしたコードに関係の無い名前は無い")


# --------------------------------------------------------------------------
# 6. 同梱部品のライセンス（実際に入ったファイルから）
# --------------------------------------------------------------------------

DOC_SUFFIXES = ("", ".txt", ".md", ".rst")
LICENSE_NAME = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", re.I)


def toc_sources() -> set[Path]:
    """PyInstaller の目録（build/release/work/release/*.toc）から、.app に入れた元のファイルを全部拾う。"""
    found: set[Path] = set()

    def walk(node) -> None:
        if isinstance(node, (list, tuple)):
            if len(node) == 3 and all(isinstance(x, str) for x in node):
                path = Path(node[1])
                if path.is_absolute() and path.is_file():
                    found.add(path.resolve())
                return
            for child in node:
                walk(child)

    for toc in sorted((WORK / SPEC.stem).glob("*.toc")):
        try:
            walk(ast.literal_eval(toc.read_text(encoding="utf-8")))
        except (ValueError, SyntaxError):
            continue
    return found


def dist_index() -> dict[Path, metadata.Distribution]:
    """site-packages のファイル → その部品（RECORD）。"""
    owner: dict[Path, metadata.Distribution] = {}
    for dist in metadata.distributions():
        for rel in dist.files or []:
            try:
                owner[Path(dist.locate_file(rel)).resolve()] = dist
            except (OSError, ValueError):
                continue
    return owner


def license_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    """部品の dist-info の中のライセンス文書（自分の dist-info のものだけ。名前は文書の拡張子に限る）。"""
    texts = []
    for rel in dist.files or []:
        parts = Path(str(rel)).parts
        if not parts or not parts[0].endswith(".dist-info"):
            continue
        name = parts[-1]
        if LICENSE_NAME.match(name) and Path(name).suffix.lower() in DOC_SUFFIXES:
            try:
                text = Path(dist.locate_file(rel)).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # GNU の条文は改ページの記号（\f）で区切ってある。印刷の区切りなので空行にする（条文は変えない）
            texts.append((str(rel), text.replace("\f", "\n")))
    return texts


def project_of(dist: metadata.Distribution) -> str:
    """部品の出どころ（同じプロジェクトの部品を見分ける。PyObjC は 1 つのプロジェクトを部品に分けて配っている）。"""
    meta = dist.metadata
    urls = [meta.get("Home-page") or ""] + [u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return next((u.rstrip("/").lower() for u in urls if "github.com" in u), (urls[0] or "").rstrip("/").lower())


def license_label(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    label = meta.get("License-Expression") or ""
    if not label:
        text = (meta.get("License") or "").strip()
        label = text if text and len(text) < 120 else ""
    if not label:
        classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        label = "、".join(classifiers)
    return label or "（部品の文書を参照）"


def collect_licenses() -> tuple[bool, str, str]:
    """(成否, 文書, 詳細)。**どの部品にも当たらないファイルがあれば止める**（黙って一覧から落とさない）。"""
    sources = toc_sources()
    if not sources:
        return False, "", "PyInstaller の目録が読めない"
    owner = dist_index()
    own_roots = [ROOT.resolve()] + [p.resolve() for p in (find_sibling(n) for n in BUNDLE_PARTS) if p]
    base = Path(sys.base_prefix).resolve()
    site = [Path(p).resolve() for p in sys.path if p.endswith("site-packages")]
    pyinstaller = Path(__import__("PyInstaller").__file__).resolve().parent
    used: dict[str, metadata.Distribution] = {}
    python_files = 0
    unknown = []
    for path in sorted(sources):
        if any(path.is_relative_to(r) for r in own_roots):
            continue
        dist = owner.get(path)
        if dist is not None:
            used[dist.metadata["Name"]] = dist
            continue
        if path.is_relative_to(pyinstaller):
            used["pyinstaller"] = metadata.distribution("pyinstaller")
            continue
        if path.is_relative_to(base) and not any(path.is_relative_to(s) for s in site):
            python_files += 1
            continue
        unknown.append(str(path))
    # ブートローダー（.app の実行ファイル）は目録に載らないことがある。必ず足す
    used.setdefault("pyinstaller", metadata.distribution("pyinstaller"))
    if unknown:
        return False, "", f"持ち主の分からないファイルが {len(unknown)} 件: {unknown[0]}"
    names = sorted(used, key=str.lower)
    lines = [
        f"{APP_NAME} {VERSION} に同梱している部品のライセンス",
        "",
        "この .app には、次の部品が入っています（PyInstaller でまとめたとき、実際に入ったファイルから作った一覧です）。",
        f"部品は {len(names) + 1} 個です。それぞれの条件の全文を、この下に部品ごとに載せます。",
        "",
        f"・Python {sys.version.split()[0]}（Python Software Foundation License。標準ライブラリと、Python に含まれる部品）",
    ]
    for name in names:
        dist = used[name]
        lines.append(f"・{name} {dist.version}（{license_label(dist)}）")
    lines += ["", "このアプリ自身（作者 Takahashi）のライセンスは「ライセンス.txt」にあります。", ""]
    python_license = base / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "LICENSE.txt"
    lines += ["=" * 70, f"Python {sys.version.split()[0]}", "=" * 70, "",
              python_license.read_text(encoding="utf-8", errors="replace").strip(), ""]
    missing, borrowed_texts = [], []
    for name in names:
        dist = used[name]
        texts = license_texts(dist)
        lines += ["=" * 70, f"{name} {dist.version}（{license_label(dist)}）", "=" * 70, ""]
        if name.lower() == "pyinstaller":
            lines += ["PyInstaller のブートローダー（.app の中の実行ファイル）と、起動時に使う部品が入っています。",
                      "PyInstaller の条件は、PyInstaller でまとめたプログラムを、どのような条件でも配ってよいとしています"
                      "（下の文書の「Bootloader Exception」）。", ""]
        if not texts:
            # 部品の配布物に文書が無いとき: 同じプロジェクトの、同じ条件の部品の文書を載せる（MIT は全文を添える条件のため）
            sibling = next((other for other in names if other != name and project_of(used[other]) == project_of(dist)
                            and license_label(used[other]) == license_label(dist) and license_texts(used[other])), None)
            if sibling is None:
                missing.append(name)
                lines += [f"この部品の配布物にはライセンスの文書が入っていません。条件は「{license_label(dist)}」です。",
                          f"部品の案内: {project_of(dist)}", ""]
            else:
                borrowed_texts.append(name)
                texts = license_texts(used[sibling])
                lines += [f"この部品の配布物にはライセンスの文書が入っていないので、同じプロジェクト（{project_of(dist)}）の"
                          f"同じ条件の部品 {sibling} の文書を載せます。", ""]
        for rel, text in texts:
            lines += [f"--- {rel} ---", "", text.strip(), ""]
    detail = f"部品 {len(names) + 1} 個（Python の標準ライブラリなど {python_files} ファイル）"
    if borrowed_texts:
        detail += f"。同じプロジェクトの文書を載せたもの: {'、'.join(borrowed_texts)}"
    if missing:
        return False, "", detail + f"。ライセンスの文書が見つからないもの: {'、'.join(missing)}"
    return True, "\n".join(lines) + "\n", detail


# --------------------------------------------------------------------------
# 7〜9. 並べて zip にし、確かめる
# --------------------------------------------------------------------------

def write_txt(path: Path, text: str) -> None:
    """配る txt は BOM 付き UTF-8・CRLF（古い日本語のエディタで化けない）。"""
    path.write_text(text.replace("\r\n", "\n"), encoding="utf-8-sig", newline="\r\n")


def readme_text(name: str = "readme.txt") -> str:
    text = (DOCS / name).read_text(encoding="utf-8")
    return text.replace("{VERSION}", VERSION)


def stage_files(third_party: str) -> bool:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    folder = STAGE / FOLDER
    folder.mkdir(parents=True)
    copy = run(["ditto", APP, folder / APP.name])
    if copy.returncode != 0:
        return step("7. 並べる", False, last_line(copy))
    write_txt(folder / "はじめにお読みください.txt", readme_text())
    shutil.copy2(DOCS / "manual.html", folder / "マニュアル.html")
    write_txt(folder / "マニュアル.txt", (DOCS / "manual.txt").read_text(encoding="utf-8-sig"))
    write_txt(folder / "README (English).txt", readme_text("readme_en.txt"))
    shutil.copy2(DOCS / "manual_en.html", folder / "Manual (English).html")
    write_txt(folder / "Manual (English).txt", (DOCS / "manual_en.txt").read_text(encoding="utf-8-sig"))
    write_txt(folder / "ライセンス.txt", (ROOT / "LICENSE.txt").read_text(encoding="utf-8"))
    write_txt(folder / "同梱部品のライセンス.txt", third_party)
    names = sorted(p.name for p in folder.iterdir())
    want = sorted(TOP_FILES + TOP_APPS)
    if names != want:
        return step("7. 並べる", False, f"並びが説明と違う: {names}")
    return step("7. 並べる", True, f"ファイル {len(TOP_FILES)} つとアプリ {len(TOP_APPS)} つ")


def make_zip() -> bool:
    DIST.mkdir(exist_ok=True)
    if ZIP.exists():
        ZIP.unlink()
    # --norsrc --noextattr: 拡張属性（隔離の印など）を __MACOSX に入れない。署名は .app の中の CodeResources にある
    done = run(["ditto", "-c", "-k", "--norsrc", "--noextattr", "--keepParent", STAGE / FOLDER, ZIP])
    if done.returncode != 0 or not ZIP.is_file():
        return step("8. zip", False, last_line(done))
    return step("8. zip", True, f"{ZIP.name}（{ZIP.stat().st_size / 1e6:.1f} MB）")


def personal_hits(folder: Path) -> list[str]:
    hits = []
    for path in sorted(folder.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes().lower()
        words = PERSONAL + (PERSONAL_TEXT if path.suffix.lower() in TEXT_SUFFIXES else ())
        for word in words:
            if word in data:
                hits.append(f"{path.relative_to(folder)}（{word.decode()}）")
                break
    return hits


def private_hits(folder: Path) -> list[str]:
    """自分たちで書いた文字のファイルに、ほかのプロジェクトの名前・手元のメモ・関係の無い話が無いか（PRIVATE_WORDS）。"""
    hits = []
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        named = [w for w in PRIVATE_WORDS if w in rel]          # ファイルやフォルダの名前も見る
        if named:
            hits.append(f"{rel}（名前に {'・'.join(named)}）")
            continue
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in PRIVATE_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = [w for w in PRIVATE_WORDS if w in text]
        if found:
            hits.append(f"{path.relative_to(folder)}（{'・'.join(found)}）")
    return hits


def txt_problems(folder: Path) -> list[str]:
    problems = []
    for name in TOP_FILES:
        if not name.endswith(".txt"):
            continue
        data = (folder / name).read_bytes()
        if not data.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{name} に BOM が無い")
        if b"\n" in data.replace(b"\r\n", b""):
            problems.append(f"{name} に CRLF でない改行がある")
        if re.search(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", data):
            problems.append(f"{name} に制御文字がある")
    return problems


def verify_zip() -> bool:
    # **ditto の zip は、日本語の名前に UTF-8 の印（0x800）を付けない。** 名前は UTF-8 なので、そのとおりに読む
    # （Mac の「アーカイブユーティリティ」も UTF-8 として読むので化けない。2026-10-24 に確かめた）
    with zipfile.ZipFile(ZIP, metadata_encoding="utf-8") as zf:
        bad = zf.testzip()
        if bad is not None:
            return step("9. zip の検証", False, f"壊れたファイル: {bad}")
        names = zf.namelist()
        tops = sorted({n.split("/", 2)[1] for n in names if n.count("/") >= 1 and n.split("/", 2)[1]})
        roots = {n.split("/", 1)[0] for n in names}
        unflagged = [i.filename for i in zf.infolist() if not i.filename.isascii() and not i.flag_bits & 0x800]
    problems = []
    if roots != {FOLDER}:
        problems.append(f"最上位のフォルダが 1 つでない: {sorted(roots)}")
    if tops != sorted(TOP_FILES + TOP_APPS):
        problems.append(f"直下の並びが説明と違う: {tops}")
    if any("__MACOSX" in n or n.endswith(".DS_Store") for n in names):
        problems.append("__MACOSX か .DS_Store が入っている")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        done = run(["ditto", "-x", "-k", ZIP, out])
        if done.returncode != 0:
            return step("9. zip の検証", False, "展開できない: " + last_line(done))
        folder = out / FOLDER
        app = folder / f"{APP_NAME}.app"
        problems += signature_problems(app)
        ok, detail = selftest(app)
        if not ok:
            problems.append(f"展開した .app の --selftest: {detail}")
        problems += txt_problems(folder)
        hits = personal_hits(folder)
        if hits:
            problems.append(f"個人の情報が入っている: {'、'.join(hits[:5])}")
        private = private_hits(folder)
        if private:
            problems.append(f"関係の無い名前や手元のメモが入っている: {'、'.join(private[:5])}")
        same = run(["diff", "-rq", STAGE / FOLDER, folder])
        if same.returncode != 0:
            problems.append("展開したものが並べたものと違う: " + last_line(same))
    if problems:
        return step("9. zip の検証", False, "、".join(problems))
    note = f"。日本語の名前に UTF-8 の印が無いもの {len(unflagged)} 件（Mac の展開では化けない）" if unflagged else ""
    digest = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    return step("9. zip の検証", True, f"展開して署名・--selftest・並び・txt・個人の情報・関係の無い名前を確かめた{note}。SHA-256 {digest[:16]}…")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="配布物（zip）を作る")
    ap.add_argument("--dirty", action="store_true", help="書きかけの変更があっても止めない（試し）")
    ap.add_argument("--app-only", action="store_true", help=".app の組み立てと確認だけ")
    args = ap.parse_args(argv)
    print(f"配布物: {FOLDER}（{ROOT}）", flush=True)
    print()
    if args.app_only:
        ok = build_app() and check_app()
        print()
        print(f"{'.app を作りました' if ok else '.app を作れませんでした'}: {APP}")
        return 0 if ok else 1
    ok = check_clean(args.dirty) and run_tests() and check_docs(args.dirty)
    ok = ok and build_app() and check_app()
    third_party = ""
    if ok:
        good, third_party, detail = collect_licenses()
        ok = step("6. 同梱部品のライセンス", good, detail)
    ok = ok and stage_files(third_party) and make_zip() and verify_zip()
    print()
    if not ok:
        failed = [name for name, good, _ in RESULTS if not good]
        print(f"配布物を作れませんでした。通らなかった工程: {'、'.join(failed)}")
        if ZIP.exists() and failed:
            ZIP.unlink()          # 検証に通らなかった zip は残さない
        return 1
    print(f"配布物ができました: {ZIP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
