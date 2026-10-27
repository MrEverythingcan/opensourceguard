"""Tests for the project decluttering module."""

import tempfile
import unittest
from pathlib import Path

from opensourceguard.organize import analyze_project, apply_organization, plan_organization


def _make_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp


MESSY = {
    "main.py": "print('hi')\n",
    "utils.py": "def f():\n    return 1\n",
    "helper.py": "x = 1\n",
    "最终版.py": "x = 2\n",
    "新建文本文档.txt": "hello\n",
    "backup.bak": "old\n",
    "Thumbs.db": "bin",
    "打包.zip": "zip",
    "screenshot.png": "png",
    ".env": "SECRET=1\n",
    "README.md": "# demo\n",
    ".git/config": "git",
}


class AnalyzeTests(unittest.TestCase):
    def test_classifies_and_flags_files(self):
        tmp = _make_repo(MESSY)
        with tmp:
            inv = analyze_project(tmp.name)
        paths = {f["path"]: f for f in inv["files"]}
        self.assertEqual(paths["main.py"]["category"], "code")
        self.assertEqual(paths["screenshot.png"]["category"], "images")
        self.assertEqual(paths["打包.zip"]["category"], "archives")
        self.assertTrue(paths["最终版.py"]["junk"])
        self.assertTrue(paths["新建文本文档.txt"]["junk"])
        self.assertTrue(paths["Thumbs.db"]["junk"])
        self.assertTrue(paths[".env"]["sensitive"])
        self.assertNotIn(".git/config", paths)  # VCS internals are ignored

    def test_missing_repo_raises(self):
        with self.assertRaises(ValueError):
            analyze_project("g:/definitely/not/here")


class PlanTests(unittest.TestCase):
    def test_plan_separates_auto_and_manual(self):
        tmp = _make_repo(MESSY)
        with tmp:
            plan = plan_organization(tmp.name)
        auto_paths = {a["from"] for a in plan["auto_actions"]}
        self.assertIn("最终版.py", auto_paths)
        self.assertIn("新建文本文档.txt", auto_paths)
        self.assertIn("backup.bak", auto_paths)
        self.assertIn("打包.zip", auto_paths)
        self.assertNotIn(".env", auto_paths)  # sensitive files are never moved
        self.assertNotIn("main.py", auto_paths)  # code is never moved automatically
        manual_text = "\n".join(plan["manual_suggestions"])
        self.assertIn("src/", manual_text)  # scattered code suggestion
        self.assertIn(".env", manual_text)  # sensitive warning
        self.assertIn(".osg_archive/", plan["gitignore_suggestions"])
        self.assertTrue(plan["advice"])

    def test_llm_advice_overrides_template(self):
        tmp = _make_repo(MESSY)
        with tmp:
            plan = plan_organization(tmp.name, model_callback=lambda prompt: "先删掉临时文件。")
        self.assertEqual(plan["advice"], "先删掉临时文件。")
        self.assertEqual(plan["source"], "model+local")

    def test_llm_failure_falls_back(self):
        def broken(prompt):
            raise RuntimeError("offline")

        tmp = _make_repo(MESSY)
        with tmp:
            plan = plan_organization(tmp.name, model_callback=broken)
        self.assertEqual(plan["source"], "local-rules")
        self.assertTrue(plan["advice"])


class ApplyTests(unittest.TestCase):
    def test_apply_moves_junk_and_extends_gitignore(self):
        tmp = _make_repo(MESSY)
        with tmp:
            root = Path(tmp.name)
            result = apply_organization(root)
            moved = {m["from"] for m in result["moved"]}
            self.assertIn("最终版.py", moved)
            self.assertIn("打包.zip", moved)
            self.assertFalse((root / "最终版.py").exists())
            self.assertTrue((root / ".osg_archive" / "最终版.py").exists())  # reversible
            self.assertTrue((root / "main.py").exists())  # code untouched
            self.assertTrue((root / ".env").exists())  # sensitive untouched
            gitignore = (root / ".gitignore").read_text(encoding="utf-8")
            self.assertIn(".osg_archive/", gitignore)
            self.assertIn(".env", gitignore)

    def test_apply_is_idempotent(self):
        tmp = _make_repo(MESSY)
        with tmp:
            first = apply_organization(tmp.name)
            second = apply_organization(tmp.name)
        self.assertGreater(len(first["moved"]), 0)
        self.assertEqual(second["moved"], [])  # nothing left to archive

    def test_gitignore_append_keeps_existing_content(self):
        tmp = _make_repo({**MESSY, ".gitignore": "__pycache__/\n"})
        with tmp:
            apply_organization(tmp.name)
            content = (Path(tmp.name) / ".gitignore").read_text(encoding="utf-8")
        self.assertTrue(content.startswith("__pycache__/"))
        self.assertEqual(content.count(".osg_archive/"), 1)


if __name__ == "__main__":
    unittest.main()
