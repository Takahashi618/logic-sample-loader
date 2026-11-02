"""配布版（PyInstaller で固めた .app）に向けた確認。**組み立ては tools/build_release.py**（ここでは何も組み立てない）。"""

from __future__ import annotations

import ast
import fnmatch
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LOGICLOADER_SILENT", "1")

import app  # noqa: E402
from core import sibling  # noqa: E402
from ui import borrowed  # noqa: E402


def _bundled(project: str, rel: str) -> bool:
    return any(fnmatch.fnmatch(rel, pattern) for pattern in sibling.BUNDLE_PARTS.get(project, ()))


class BundlePartsTest(unittest.TestCase):
    def test_every_borrowed_part_is_bundled(self):
        """借りている部品は全部、配布版に同梱する（足し忘れると、配布版だけ読み上げや試聴が黙って消える）。"""
        for label, module in borrowed._BORROWED.items():
            if module is None:
                self.skipTest(f"借り先が無い: {label}")
            path = Path(module.__file__).resolve()
            roots = {name: sibling.find_sibling(name).resolve() for name in sibling.BUNDLE_PARTS}
            project = next(name for name, root in roots.items() if path.is_relative_to(root))
            rel = path.relative_to(roots[project]).as_posix()
            with self.subTest(part=label):
                self.assertTrue(_bundled(project, rel), f"{project}/{rel}")

    def test_ptcore_modules_in_use_are_bundled(self):
        for name in ("library", "splicename", "audiofile", "prep", "dsp"):
            module = sibling.load_ptcore(name)
            if module is None:
                self.skipTest("ptcore が無い")
            rel = Path(module.__file__).resolve().relative_to(sibling.find_sibling("sample_index").resolve())
            with self.subTest(module=name):
                self.assertTrue(_bundled("sample_index", rel.as_posix()))


class CleanBundleTest(unittest.TestCase):
    """配布物の .app に平文で入る .py を、関係の無い名前や開発の経過を出さない形にする（2026-11-02）。"""

    def test_clean_source_drops_comments_and_docstrings_only(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_release
        src = ('"""別のアプリと合わせる。"""\n'
               "# 手元のノートを見る\n"
               "def f(x):\n"
               '    """別のアプリの写し。"""\n'
               "    return x + 1  # 同期の話\n"
               "class C:\n"
               '    """説明だけ。"""\n'
               "MESSAGE = 'これは残す'\n")
        out = build_release.clean_source(src)
        for word in ("別のアプリ", "手元のノート", "同期の話", "説明だけ"):
            self.assertNotIn(word, out)
        self.assertIn("これは残す", out)
        space: dict = {}
        exec(compile(out, "<clean>", "exec"), space)
        self.assertEqual(space["f"](1), 2)
        self.assertTrue(isinstance(space["C"], type))

    def test_bundled_ptcore_reads_only_bundled_modules(self):
        """ptcore は使う部品だけを同梱する。**同梱した部品が相対 import で読む部品も同梱してある**こと。"""
        base = sibling.find_sibling("sample_index")
        if base is None:
            self.skipTest("ptcore が無い")
        bundled = {p for p in sibling.BUNDLE_PARTS["sample_index"]}
        self.assertNotIn("ptcore/__init__.py", bundled)       # 関係の無いモジュールまで読むので入れない
        for rel in bundled:
            tree = ast.parse((base / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                    with self.subTest(part=rel, imports=node.module):
                        self.assertIn(f"ptcore/{node.module}.py", bundled)

    def test_borrowed_parts_use_neutral_names(self):
        """コードの中では借り物を中立の名前で呼び、手元の本当のフォルダ名は sibling_dirs.json（配布物に入れない）にだけ書く。"""
        import json
        sys.path.insert(0, str(ROOT / "tools"))
        import build_release
        if not sibling.DEV_DIRS_FILE.is_file():
            self.skipTest("手元の設定が無い（公開しているソース）")
        dev = json.loads(sibling.DEV_DIRS_FILE.read_text(encoding="utf-8"))
        self.assertEqual(set(dev), set(sibling.BUNDLE_PARTS))
        for name in sibling.BUNDLE_PARTS:
            with self.subTest(part=name):
                self.assertFalse(any(word in name for word in build_release.PRIVATE_WORDS))
        for real in dev.values():
            with self.subTest(folder=real):
                self.assertIn(real, build_release.PRIVATE_WORDS)     # 配布物の検査が、本当の名前を見つけられる


class DocumentsTest(unittest.TestCase):
    """配布物の文書（2026-10-24 の利用者の指示: ライセンス名は Takahashi、マニュアルなどに AI で作った旨）。"""

    def test_license_and_ai_notice(self):
        license_text = (ROOT / "LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("Copyright (c) 2026 Takahashi", license_text)
        for path in (ROOT / "LICENSE.txt", ROOT / "docs" / "readme.txt", ROOT / "docs" / "manual.html"):
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("Takahashi", text)
                self.assertIn("AI（Anthropic 社の Claude）", text)

    def test_readme_names_everything_in_the_folder(self):
        sys.path.insert(0, str(ROOT / "tools"))
        import build_release

        readme = (ROOT / "docs" / "readme.txt").read_text(encoding="utf-8")
        for name in build_release.TOP_FILES + build_release.TOP_APPS:
            self.assertIn(name, readme)
        self.assertIn(f"ファイルが {len(build_release.TOP_FILES)} つと、アプリが {len(build_release.TOP_APPS)} つ", readme)

    def test_english_documents(self):
        """英語の文書にも、ライセンス名と AI で作った旨がある。英語のはじめにお読みくださいも、フォルダの中身を全部挙げる。"""
        sys.path.insert(0, str(ROOT / "tools"))
        import build_release

        for path in (ROOT / "docs" / "readme_en.txt", ROOT / "docs" / "manual_en.html"):
            with self.subTest(document=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("Takahashi", text)
                self.assertIn("AI (Anthropic's Claude)", text)
        readme = (ROOT / "docs" / "readme_en.txt").read_text(encoding="utf-8")
        for name in build_release.TOP_FILES + build_release.TOP_APPS:
            self.assertIn(name, readme)
        self.assertIn(f"contains {len(build_release.TOP_FILES)} files and {len(build_release.TOP_APPS)} app", readme)

    def test_manual_matches_the_code(self):
        """マニュアルのうちコードから作る部分（キー・設定・タブの並び）が古くない（作り直し忘れを落とす）。"""
        done = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tools" / "doc_parts.py"), "--check"],
                              capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, LOGICLOADER_SILENT="1", SM_SILENT="1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class StartModesTest(unittest.TestCase):
    def test_helper_mode_runs_the_helper_without_a_window(self):
        """`--helper` は補助だけを動かす（配布版の Keyboard Maestro のマクロが使う）。Logic には触らない試し方で確かめる。"""
        import contextlib
        import io

        from integration import quick_sampler

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = app.main(["--helper", "--load", str(ROOT / "no-such-file.wav"), "--quiet"])
        self.assertEqual(code, quick_sampler.engine().EXIT_NO_FILE)   # ファイルを確かめた時点で止まる
        self.assertIn("読み込むファイルが見つかりません", out.getvalue())
        self.assertNotIn("wx", {m.split(".")[0] for m in sys.modules} - self._already)

    def setUp(self):
        self._already = {m.split(".")[0] for m in sys.modules}

    def test_selftest_builds_the_window_and_finds_every_part(self):
        done = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "app.py"), "--selftest"],
                              capture_output=True, text=True, timeout=120,
                              env=dict(os.environ, LOGICLOADER_SILENT="1", SM_SILENT="1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('"ok": true', done.stdout)
        self.assertIn('"problems": []', done.stdout)


if __name__ == "__main__":
    unittest.main()
