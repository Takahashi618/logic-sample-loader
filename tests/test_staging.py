"""core/staging.py の単体テスト（一時フォルダだけを使う。Splice にも ~/Music にも触らない）。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import staging  # noqa: E402


class StagingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.splice = self.tmp / "Splice" / "sounds" / "packs"
        self.root = self.tmp / "Music" / "LogicSampleLoader"
        self.src = self.splice / "Afro House Collection" / "Sub" / "Top_Loops" / "Loop_120.wav"
        self.src.parent.mkdir(parents=True)
        self.src.write_bytes(b"RIFF....WAVE")

    def test_folder_is_the_pack_under_splice(self):
        self.assertEqual(staging.folder_name(self.src, "", self.splice), "Afro House Collection")

    def test_index_pack_name_wins(self):
        self.assertEqual(staging.folder_name(self.src, "Pack From Index", self.splice), "Pack From Index")

    def test_outside_splice_uses_parent_folder(self):
        other = self.tmp / "Mine" / "Kicks" / "Kick.wav"
        self.assertEqual(staging.folder_name(other, "", self.splice), "Kicks")

    def test_unsafe_names_are_cleaned(self):
        self.assertEqual(staging.folder_name(self.src, "a/b:c", self.splice), "a_b_c")
        self.assertEqual(staging.folder_name(self.src, ".hidden", self.splice), "hidden")
        self.assertEqual(staging.folder_name(Path("/x.wav"), "", self.splice), staging.FALLBACK_FOLDER)

    def test_copies_once_and_keeps_the_name(self):
        first = staging.stage(self.src, "", self.root, self.splice)
        self.assertTrue(first.copied)
        # パックの中のフォルダの並びも写す（同じ名前の別の素材と重ならないように）
        self.assertEqual(first.path, self.root / "Afro House Collection" / "Sub" / "Top_Loops" / "Loop_120.wav")
        self.assertEqual(first.path.read_bytes(), b"RIFF....WAVE")
        # Logic が書き足したつもり。**2 回目は上書きしない**
        first.path.write_bytes(b"RIFF....WAVE+LGWV")
        second = staging.stage(self.src, "", self.root, self.splice)
        self.assertFalse(second.copied)
        self.assertEqual(second.path.read_bytes(), b"RIFF....WAVE+LGWV")
        # 元のファイルは変わらない
        self.assertEqual(self.src.read_bytes(), b"RIFF....WAVE")

    def test_staged_file_is_used_as_is(self):
        inside = self.root / "P" / "x.wav"
        inside.parent.mkdir(parents=True)
        inside.write_bytes(b"x")
        res = staging.stage(inside, "", self.root, self.splice)
        self.assertEqual(res.path, inside)
        self.assertFalse(res.copied)

    def test_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            staging.stage(self.tmp / "nope.wav", "", self.root, self.splice)

    def test_no_temporary_file_left(self):
        staged = staging.stage(self.src, "", self.root, self.splice).path
        leftovers = [p.name for p in staged.parent.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])


    def test_fitted_path_keeps_the_pack_folder(self):
        got = staging.fitted_path(self.src, "120bpm_+3_Am", "", self.root, self.splice)
        inner = self.root / "Afro House Collection" / "Sub" / "Top_Loops"
        self.assertEqual(got, inner / "Loop_120__120bpm_+3_Am.wav")
        self.assertFalse(got.exists())                     # 組み立てるだけ
        rb = staging.fitted_path(self.src, "120bpm_+3_Am", "", self.root, self.splice, "rubberband")
        self.assertEqual(rb, inner / "rubberband" / "Loop_120__120bpm_+3_Am.wav")

    def test_same_name_in_other_folders_does_not_collide(self):
        # 同じパックの別のフォルダに同じ名前の素材 → 複製も合わせた音も別の場所（取り違えない）
        other = self.splice / "Afro House Collection" / "Sub" / "Bass_Loops" / "Loop_120.wav"
        other.parent.mkdir(parents=True)
        other.write_bytes(b"RIFF....BASS")
        self.assertNotEqual(staging.staged_path(self.src, "", self.root, self.splice),
                            staging.staged_path(other, "", self.root, self.splice))
        self.assertNotEqual(staging.fitted_path(self.src, "120bpm", "", self.root, self.splice),
                            staging.fitted_path(other, "120bpm", "", self.root, self.splice))
        self.assertEqual(staging.stage(other, "", self.root, self.splice).path.read_bytes(), b"RIFF....BASS")
        # Splice の外の素材は、これまでどおり親フォルダの名前だけ
        outside = self.tmp / "Mine" / "Kicks" / "Kick.wav"
        self.assertEqual(staging.pack_subfolders(outside, self.splice), Path())
        self.assertEqual(staging.pack_subfolders(self.splice / "P" / "x.wav", self.splice), Path())

    def test_copy_into_place_and_clear_previews(self):
        preview = self.tmp / "Caches" / "preview"
        made = staging.fitted_path(self.src, "x", "", preview, self.splice)
        staging.copy_into_place(self.src, made)
        self.assertEqual(made.read_bytes(), b"RIFF....WAVE")
        self.assertEqual([p.name for p in made.parent.iterdir()], [made.name])
        self.assertEqual(staging.clear_previews(preview), 1)
        self.assertEqual(list(preview.rglob("*")), [])
        self.assertEqual(staging.clear_previews(self.tmp / "none"), 0)
        self.assertTrue(self.src.exists())                 # 試聴の置き場所の外は触らない

if __name__ == "__main__":
    unittest.main()
