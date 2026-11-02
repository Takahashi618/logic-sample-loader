# -*- mode: python ; coding: utf-8 -*-
# Logicサンプルローダー（配布版）— PyInstaller の設定
#
# ★直接は使わない。tools/build_release.py が、組み立て・確認・ライセンスの収集・zip まで行う。
#   python3.14 -X utf8 tools/build_release.py
#
# 出力: build/release/dist/Logicサンプルローダー.app（windowed / onedir / arm64）
# - 中の実行ファイルは ASCII 名（LogicSampleLoader）。.app の名前は読み上げで分かるように日本語
# - 署名は ad-hoc（PyInstaller の既定）。**手元の証明書では署名しない**（証明書の名前が配布物に入る）
# - 借りる部品（隣のプロジェクトのファイル）は .app の中の siblings/ に、隣と同じ並びで置く（フォルダの名前は
#   中立の名前。借り先のプロジェクトの名前を配布物に出さない。core/sibling.py の BUNDLE_PARTS と sibling_dirs.json）
#   （core/sibling.py の BUNDLE_PARTS）。補助（tools/qs_sample_menu.py）も、ファイルのまま置く
#   （アプリも Keyboard Maestro のマクロも、ファイルの場所を指定して読む作りのため）。どちらも**コメントと説明文を
#   除いた写し**を入れる（平文で残るので、開発の経過や関係の無い名前を出さない。2026-11-02）
# - ファイルのまま置いたものは PyInstaller が中を調べないので、それらが import するもの（標準ライブラリや
#   pyobjc）をここで拾って hiddenimports に足す（拾い忘れると、配布版でだけその機能が黙って止まる）
import ast
import importlib.util
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
sys.path.insert(0, str(ROOT))

from core.sibling import BUNDLE_PARTS, find_sibling  # noqa: E402
from core.version import VERSION  # noqa: E402

# このプロジェクトと借り先の中の名前（hiddenimports には入れない）
LOCAL_TOPS = {"core", "ui", "integration", "tools", "ptcore", "audio", "app", "tests"}


def imports_of(path: Path) -> set:
    """ファイルが import する名前（関数の中のものも）。相対 import は除く。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name.split(".")[0]) is not None
    except (ImportError, ValueError):
        return False


sys.path.insert(0, str(ROOT / "tools"))
from build_release import clean_part, clean_source  # noqa: E402

# ファイルのまま置く .py は、**コメントと説明文を除いた写し**を入れる（.app の中に平文で残るため。clean_source）
CLEAN = ROOT / "build" / "release" / "clean"


def cleaned(path: Path, dest: str, rel: str = "") -> tuple:
    out = CLEAN / dest / path.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(clean_part(rel, path.read_text(encoding="utf-8")), encoding="utf-8")
    return str(out), dest


datas = [cleaned(ROOT / "tools" / "qs_sample_menu.py", "tools")]
# 日本語と英語の名前・許可の説明（Contents/Resources/*.lproj。Mac の言語で Finder の名前が変わる）
LOCALIZATIONS = ("ja", "en")
datas += [(str(ROOT / "resources" / f"{lang}.lproj" / "InfoPlist.strings"), f"{lang}.lproj") for lang in LOCALIZATIONS]
loose = [ROOT / "tools" / "qs_sample_menu.py"]
for project, patterns in BUNDLE_PARTS.items():
    base = find_sibling(project)
    if base is None:
        raise SystemExit(f"借り先が見つかりません: {project}")
    for pattern in patterns:
        found = sorted(base.glob(pattern))
        if not found:
            raise SystemExit(f"借りる部品が見つかりません: {project}/{pattern}")
        for path in found:
            rel = path.relative_to(base)
            datas.append(cleaned(path, str(Path("siblings") / project / rel.parent), rel.as_posix()))
            loose.append(path)

# 試聴の just_playback の _ma_playback（cffi で作った拡張）は、読み込まれるときに C の側から _cffi_backend を
# import する。PyInstaller には見えないので足す（無いと配布版でだけ試聴が使えない。2026-10-24 に踏んだ）
hidden = {"_cffi_backend"}
for path in loose:
    for name in imports_of(path):
        if name.split(".")[0] in LOCAL_TOPS or not importable(name):
            continue        # 自分たちの部品と、フラットに読むときだけの名前（audiofile など）
        hidden.add(name)

# 自分たちのコード（app.py・core・ui・integration）も、**説明文とコメントを除いた写し**から組み立てる。
# コンパイルしたコード（.app の中の PYZ）にも説明文は文字列として残り、取り出せば読めるため（2026-11-02）
SRC = CLEAN / "src"
for path in [ROOT / "app.py"] + [p for d in ("core", "ui", "integration") for p in sorted((ROOT / d).glob("*.py"))]:
    out = SRC / path.relative_to(ROOT)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(clean_source(path.read_text(encoding="utf-8")), encoding="utf-8")

a = Analysis(
    [str(SRC / "app.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    # 関数の中で遅れて import するもの（integration.km_helper など）を取りこぼさないよう、丸ごと入れる
    hiddenimports=sorted(hidden) + collect_submodules("core") + collect_submodules("ui")
    + collect_submodules("integration"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "numpy", "PyInstaller", "pytest", "IPython"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LogicSampleLoader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LogicSampleLoader",
)
app = BUNDLE(
    coll,
    name="Logicサンプルローダー.app",
    icon=None,
    bundle_identifier="local.takahashi.logicsampleloader",
    version=VERSION,
    info_plist={
        "CFBundleName": "Logicサンプルローダー",
        "CFBundleDisplayName": "Logicサンプルローダー",
        "CFBundleDevelopmentRegion": "ja",
        "CFBundleLocalizations": list(LOCALIZATIONS),
        "LSHasLocalizedDisplayName": True,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "15.0",
        "LSArchitecturePriority": ["arm64"],
        "LSRequiresNativeExecution": True,
        # バージョン情報で読み上げる（tools/build_release.py が環境変数で渡す）
        "LLBuildDate": os.environ.get("LL_BUILD_DATE", ""),
        "LLGitCommit": os.environ.get("LL_GIT_COMMIT", ""),
        "LLFlavor": "配布版",
        "NSAccessibilityUsageDescription": (
            "Logic Pro の Quick Sampler・Drum Machine Designer・オーディオトラックで、VoiceOver から届かない部品を押して"
            "素材を読み込むために使います。"),
        "NSAppleEventsUsageDescription": (
            "アプリ自身から読み上げを頼めないときだけ、予備の方法として System Events で VoiceOver に読み上げを頼みます。"),
        "NSDesktopFolderUsageDescription": "デスクトップに置いたサンプルを読み込むために使います。",
        "NSDocumentsFolderUsageDescription": "書類フォルダに置いたサンプルを読み込むために使います。",
        "NSDownloadsFolderUsageDescription": "ダウンロードしたサンプルを読み込むために使います。",
        "NSRemovableVolumesUsageDescription": "外付けドライブに置いたサンプル（Splice のフォルダなど）を読み込むために使います。",
    },
)
