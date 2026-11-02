"""素材を作業用フォルダへ複製する。**Splice の元のファイルを Logic に書き換えさせない。**

Logic は、読み込んだ素材のファイルそのものに情報を書き足す（2026-09-24 実測）。
Quick Sampler に Original で読み込むと `LGWV`・`ResU`・`LGBM`・`EAPD`・`EAFP` などが
約 12 KB 足され、サンプルレートが違えば変換した複製（`名前-44k.wav`）を**元の素材の隣に**
書き出す。

Splice のフォルダをそのまま読ませると、Splice の素材が書き換わる。
追加チャンク入りの WAV を読めないアプリもある（Battery 4 で実際にあった）ので、
ほかのアプリの使い勝手まで変わりかねない。そこで**作業用フォルダへ複製してから読み込む。**

    ~/Music/LogicSampleLoader/<パック名>/<パックの中のフォルダ>/<元のファイル名>

- **ファイル名は変えない。** Quick Sampler の欄に出る名前は、利用者が耳で聞く名前なので
- パックごとにフォルダを分ける。別のパックに同じ名前の素材があっても重ならない
- **パックの中のフォルダの並びもそのまま写す**（Splice の素材のとき）。同じパックの別のフォルダに
  同じ名前の素材があると、パックのフォルダだけでは重なり、もう一方の複製や合わせた音を使い回してしまう
  （2026-09-26 のレビューで指摘。それまでに作った複製はパックのフォルダの直下に残る。消さない）
- **複製済みならそれを使う。** Logic が書き足したあとの複製を、元で上書きしない
  （書き足した情報はテンポの解析などで、捨てる理由が無い）
- **消さない。** 読み込んだプロジェクトは、この複製を参照している

OS 非依存（パスを組み立ててコピーするだけ）。
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "STAGE_ROOT",
    "PREVIEW_ROOT",
    "SPLICE_PACKS",
    "FALLBACK_FOLDER",
    "StageResult",
    "folder_name",
    "staged_path",
    "pack_subfolders",
    "fitted_path",
    "stage",
    "copy_into_place",
    "clear_previews",
]

STAGE_ROOT = Path.home() / "Music" / "LogicSampleLoader"
# テンポとキーを合わせて試聴した音の置き場所。**貼ったものは STAGE_ROOT に置き直す**（プロジェクトが参照するため）。
# こちらは試聴のためだけなので、アプリを閉じるときに消す
PREVIEW_ROOT = Path.home() / "Library" / "Caches" / "LogicSampleLoader" / "preview"
SPLICE_PACKS = Path.home() / "Splice" / "sounds" / "packs"
FALLBACK_FOLDER = "その他"

# フォルダ名に使えない・使うと紛らわしい文字
_BAD_CHARS = str.maketrans({"/": "_", ":": "_", "\\": "_", "\0": "_"})


@dataclass(frozen=True)
class StageResult:
    path: Path          # 読み込ませるファイル（作業用フォルダの中）
    copied: bool        # 今回複製したか（False なら前に複製したものを使った）


def folder_name(src: Path, pack: str = "", splice_root: Path = SPLICE_PACKS) -> str:
    """作業用フォルダの中の、パックのフォルダ名。

    1. 索引にパック名があればそれ
    2. Splice のフォルダの中なら、その下の最初のフォルダ名（= パックのフォルダ）
    3. どちらでもなければ、素材のある親フォルダの名前
    """
    name = (pack or "").strip()
    if not name:
        try:
            rel = src.resolve().relative_to(splice_root.resolve())
            name = rel.parts[0] if len(rel.parts) > 1 else ""
        except (ValueError, OSError):
            name = ""
    if not name:
        name = src.parent.name
    name = name.translate(_BAD_CHARS).strip().lstrip(".").strip()
    return name or FALLBACK_FOLDER


def pack_subfolders(src: Path, splice_root: Path = SPLICE_PACKS) -> Path:
    """Splice のパックの中で、素材が入っているフォルダの並び（パックの直下なら空）。Splice の外なら空。"""
    try:
        rel = src.resolve().relative_to(splice_root.resolve())
    except (ValueError, OSError):
        return Path()
    inner = [part.translate(_BAD_CHARS).strip().lstrip(".").strip() or FALLBACK_FOLDER for part in rel.parts[1:-1]]
    return Path(*inner) if inner else Path()


def staged_path(src: Path, pack: str = "", root: Path = STAGE_ROOT,
                splice_root: Path = SPLICE_PACKS) -> Path:
    """複製先のパス。**作らない**（組み立てるだけ）。"""
    return root / folder_name(src, pack, splice_root) / pack_subfolders(src, splice_root) / src.name


def fitted_path(src: Path, tag: str, pack: str = "", root: Path = STAGE_ROOT,
                splice_root: Path = SPLICE_PACKS, engine: str = "") -> Path:
    """テンポとキーを合わせた音の置き場所（「元の名前__120bpm_+3_Am.wav」）。**作らない。**

    Logic はリージョンにファイル名を付けるので、名前を聞けば何に合わせたかが分かる。
    同じ素材・同じ条件なら同じ名前になる（作り直さずに使い回せる）。

    engine（"rubberband" など）を渡すと、その名前のフォルダに分ける。**ffmpeg で作った前のファイルと
    取り違えない**ため（名前が同じだと使い回してしまい、道具を入れ替えても前の音のまま貼られる）。
    リージョンの名前はファイル名だけなので、フォルダを分けても聞こえ方は変わらない。
    """
    folder = root / folder_name(src, pack, splice_root) / pack_subfolders(src, splice_root)
    if engine:
        folder = folder / engine
    return folder / f"{src.stem}__{tag}.wav"


def copy_into_place(src: Path, dest: Path) -> None:
    """src を dest へ複製する。**一時ファイルに書いてから名前を差し替える**（半端なファイルを残さない）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.copying")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def clear_previews(root: Path = PREVIEW_ROOT) -> int:
    """試聴用に作った音を消す。消した数を返す。**PREVIEW_ROOT の中だけ**を触る。"""
    count = 0
    if not root.is_dir():
        return 0
    for path in sorted(root.rglob("*.wav"), reverse=True):
        try:
            path.unlink()
            count += 1
        except OSError:
            pass
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return count


def stage(src: Path | str, pack: str = "", root: Path = STAGE_ROOT,
          splice_root: Path = SPLICE_PACKS) -> StageResult:
    """素材を作業用フォルダへ複製して、読み込ませるパスを返す。

    - 素材がすでに作業用フォルダの中にあれば、そのまま使う（複製の複製を作らない）
    - 複製済みなら、それを使う（Logic が書き足したものを上書きしない）
    - 複製は**同じフォルダの一時ファイルに書いてから名前を差し替える。**
      途中で止まっても、半端なファイルが本物の名前で残らない

    素材が無ければ FileNotFoundError。
    """
    src = Path(src).expanduser()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    try:
        src.resolve().relative_to(root.resolve())
        return StageResult(src, copied=False)
    except (ValueError, OSError):
        pass
    dest = staged_path(src, pack, root, splice_root)
    if dest.is_file():
        return StageResult(dest, copied=False)
    copy_into_place(src, dest)
    return StageResult(dest, copied=True)
