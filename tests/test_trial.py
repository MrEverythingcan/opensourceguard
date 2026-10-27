"""Trial-run: allowlisted checks on an isolated copy plus fix suggestions."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opensourceguard.runtime import trial_run


def _make_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp


class TrialRunTests(unittest.TestCase):
    def test_passing_project(self):
        tmp = _make_repo({
            "requirements.txt": "",
            "calc.py": "def add(a, b):\n    return a + b\n",
            "tests/__init__.py": "",
            "tests/test_calc.py": "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n",
        })
        with tmp:
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "passed")
        self.assertTrue(result["ready_to_publish"])
        self.assertEqual([c["name"] for c in result["checks"]], ["语法检查", "运行测试"])
        self.assertTrue(all(c["passed"] for c in result["checks"]))
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["source"], "local-run")
        self.assertIn("临时副本", result["isolation"])

    def test_syntax_error_is_located(self):
        tmp = _make_repo({"broken.py": "def broken(:\n    pass\n"})
        with tmp:
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "failed")
        self.assertFalse(result["ready_to_publish"])
        syntax = [f for f in result["failures"] if f["kind"] == "语法错误"]
        self.assertTrue(syntax)
        self.assertIn("broken.py", syntax[0]["title"])
        self.assertTrue(any("语法错误" in s for s in result["suggestions"]))

    def test_missing_dependency_is_named(self):
        tmp = _make_repo({
            "requirements.txt": "",
            "tests/__init__.py": "",
            "tests/test_dep.py": "import totally_missing_pkg_xyz\n",
        })
        with tmp:
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "failed")
        dep = [f for f in result["failures"] if f["kind"] == "缺少依赖"]
        self.assertTrue(dep)
        self.assertEqual(dep[0]["module"], "totally_missing_pkg_xyz")
        self.assertTrue(any("pip install totally_missing_pkg_xyz" in s for s in result["suggestions"]))

    def test_no_runnable_checks(self):
        tmp = _make_repo({"README.md": "# just docs\n"})
        with tmp:
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "no-checks")
        self.assertFalse(result["ready_to_publish"])
        self.assertTrue(result["suggestions"])

    def test_zero_discovered_tests_is_not_a_pass(self):
        tmp = _make_repo({
            "requirements.txt": "",
            "tests/test_pytest_style.py": "def test_smoke():\n    assert True\n",
        })
        with tmp:
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "failed")
        self.assertFalse(result["ready_to_publish"])
        check = next(c for c in result["checks"] if c["name"] == "运行测试")
        self.assertIn("Ran 0 tests", check["output"])
        self.assertTrue(check["no_tests"])
        self.assertFalse(check["passed"])
        self.assertEqual(result["failures"][0]["kind"], "未找到测试")
        self.assertTrue(any("一键补齐" in s for s in result["suggestions"]))

    def test_generated_smoke_test_really_runs(self):
        from opensourceguard.open_source import complete_open_source

        tmp = _make_repo({"main.py": "print('ok')\n"})
        with tmp:
            complete_open_source(tmp.name, confirm=True)
            result = trial_run(tmp.name)
        self.assertEqual(result["verdict"], "passed")
        check = next(c for c in result["checks"] if c["name"] == "运行测试")
        self.assertTrue(check["passed"])
        self.assertFalse(check["no_tests"])
        self.assertIn("Ran 1 test", check["output"])

    def test_model_polish_and_fallback(self):
        tmp = _make_repo({"broken.py": "def broken(:\n    pass\n"})
        with tmp:
            polished = trial_run(tmp.name, model_callback=lambda prompt: "先修语法，再跑测试。")
        self.assertEqual(polished["advice"], "先修语法，再跑测试。")
        self.assertEqual(polished["source"], "model+local-run")

        def broken_model(prompt):
            raise RuntimeError("offline")

        tmp2 = _make_repo({"broken.py": "def broken(:\n    pass\n"})
        with tmp2:
            fallback = trial_run(tmp2.name, model_callback=broken_model)
        self.assertEqual(fallback["source"], "local-run")
        self.assertIn("试跑发现", fallback["advice"])

    def test_missing_directory_rejected(self):
        with self.assertRaises(ValueError):
            trial_run("definitely/not/a/repo")


if __name__ == "__main__":
    unittest.main()
