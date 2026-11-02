"""隣のプロジェクトを探して借りる。**探し方をここ 1 か所に集める。**

このアプリは、読み上げや素材の索引などの部品を、作者の別のプロジェクトから借りている。部品には中立の名前を付けて呼ぶ。

    speech_and_preview   読み上げ・試聴・効果音
    sample_kinds         種別とパックの並び順
    sample_index         Splice の索引とファイル名の解析

探す順番:

1. このプロジェクトの `siblings/<名前>/`（公開しているソースと配布版。部品の写しが入っている）
2. 作者の手元では、同じ親フォルダに並んだ別のプロジェクト（本当のフォルダ名は `sibling_dirs.json`。公開しない）

**見つからなくても落ちない。** 借り物が無ければ、その機能だけが使えなくなる
（読み上げ名が付かない、検索が使えない）。アプリ自体は起動する。

### 配布版（PyInstaller で固めた .app。2026-10-24 から）

借りる部品を `.app` の中の `siblings/` に同梱する（`BUNDLE_PARTS`。`tools/build_release.py` が詰める）。読み込み方は同じ。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

__all__ = [
    "FROZEN",
    "PROJECT_ROOT",
    "BUNDLE_SIBLINGS",
    "DEV_DIRS_FILE",
    "BUNDLE_PARTS",
    "find_sibling",
    "load_file_module",
    "load_ptcore",
]

# PyInstaller で固めた配布版として動いているか
FROZEN = bool(getattr(sys, "frozen", False))

# このプロジェクトの根。`core/` の 1 つ上（配布版では、.app の中の部品の置き場所）
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[1]

# 配布版で、借りる部品を置く場所（隣のプロジェクトと同じ並びにする）
BUNDLE_SIBLINGS = PROJECT_ROOT / "siblings"

# 借り物は**中立の名前**で呼ぶ（speech_and_preview・sample_kinds・sample_index）。配布版の siblings/ の中のフォルダも
# この名前にする。**手元の隣のフォルダの本当の名前は DEV_DIRS_FILE（配布物に入れない）にだけ書く**（配布物は
# そのソフトと関係の無い名前を載せない決まり。2026-11-02。コンパイルしたコードの中の文字列にも残さない）
DEV_DIRS_FILE = PROJECT_ROOT / "sibling_dirs.json"

# 配布版に同梱する借り物（中立の名前 → その中のファイル）。**ui/borrowed.py と load_ptcore が読むもの全部。**
# 足したら tests/test_release.py が気づく（借りているのに同梱していない部品・それらが読む部品があれば落ちる）
# **ptcore は使う 5 つと、それらが読むものだけ。`__init__.py` は入れない**（中で関係の無いモジュールまで読む。
# 無ければ名前空間パッケージとして読めて、相対 import も通る）。2026-11-02、配布物から関係の無いものを除くため
BUNDLE_PARTS: dict[str, tuple[str, ...]] = {
    "speech_and_preview": ("ui/ax_label.py", "ui/announce.py", "ui/sounds.py", "ui/wx_keys.py", "audio/player.py"),
    "sample_kinds": ("core/kinds.py", "core/notes.py"),
    "sample_index": ("ptcore/library.py", "ptcore/splicename.py", "ptcore/audiofile.py", "ptcore/prep.py",
                      "ptcore/dsp.py"),
}

# プロジェクトの根から上へ何段目を見るか（`parents` の添字）。0 段目が本命。
_DEPTHS = (0, 1)

_MISSING = object()
_ptcore_cache: dict[str, Any] = {}


def _sibling_roots() -> list[Path]:
    return [PROJECT_ROOT.parents[depth] for depth in _DEPTHS if depth < len(PROJECT_ROOT.parents)]


def _dev_dirs() -> dict[str, str]:
    """手元の隣のフォルダの本当の名前（中立の名前 → フォルダ名）。無い・読めなければ空（名前のまま探す）。"""
    try:
        import json
        data = json.loads(DEV_DIRS_FILE.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def find_sibling(name: str, marker: str = "") -> Path | None:
    """借りる部品のフォルダを探す。見つからなければ None。

    marker は中にあるはずのファイルの相対パス。**本物かどうかを確かめるため**に使う
    （同名の空フォルダを掴んで、あとで分かりにくい ImportError になるのを防ぐ）。
    """
    candidates = [BUNDLE_SIBLINGS / name]           # 配布版と、公開しているソース（部品の写しが siblings/ にある）
    if not FROZEN:
        candidates += [root / _dev_dirs().get(name, name) for root in _sibling_roots()]
    for base in candidates:
        if not base.is_dir():
            continue
        if marker and not (base / marker).exists():
            continue
        return base
    return None


def load_file_module(path: Path, alias: str) -> ModuleType | None:
    """ファイルの場所を指定して読み込む。**sys.path を汚さない。** 読めなければ None。

    借り先のパッケージ名（`ui` や `core`）は、このプロジェクトの同名のパッケージと
    衝突する。`sys.path` に足す方式は使えないので、ファイルを直接読む
    （別のプロジェクトの部品を借りるときは importlib でファイルを指定する）。
    """
    if alias in sys.modules:
        return sys.modules[alias]
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # 借り物が壊れていてもアプリは起動させる
        sys.modules.pop(alias, None)
        return None
    return module


def load_ptcore(name: str) -> Any | None:
    """`sample_index` の `ptcore/<name>.py` を読み込む。**一度だけ試す。**

    ptcore は相対 import を使うパッケージなので、ファイル単体ではなく
    パッケージとして読む（借り先を sys.path に足す）。名前は `ptcore` で、
    このプロジェクトの `core` とは衝突しない。
    """
    cached = _ptcore_cache.get(name, _MISSING)
    if cached is not _MISSING:
        return cached
    module: Any | None = None
    root = find_sibling("sample_index", "ptcore/splicename.py")
    if root is not None:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        try:
            module = __import__(f"ptcore.{name}", fromlist=[name])
        except ImportError:
            module = None
    _ptcore_cache[name] = module
    return module
