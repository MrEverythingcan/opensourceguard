"""Structured dependency parsing shared by SBOM and OSV scanning."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opensourceguard.dependencies import parse_dependencies, purl_for


def _make_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp


class DependencyParsingTests(unittest.TestCase):
    def test_requirements_versions_and_pins(self):
        tmp = _make_repo({
            "requirements.txt": (
                "# comment\n"
                "flask==3.0.0\n"
                "requests>=2.31\n"
                "numpy[extra]==1.26.4  # trailing\n"
                "pytest ; python_version >= \"3.8\"\n"
                "gitlib @ https://example.com/lib.zip\n"
                "\n"
            ),
        })
        with tmp:
            result = parse_dependencies(tmp.name)
        deps = {d["name"]: d for d in result["dependencies"]}
        self.assertEqual(set(deps), {"flask", "requests", "numpy", "pytest", "gitlib"})
        self.assertEqual(deps["flask"]["version"], "3.0.0")
        self.assertTrue(deps["flask"]["pinned"])
        self.assertEqual(deps["requests"]["version"], "2.31")
        self.assertFalse(deps["requests"]["pinned"])
        self.assertEqual(deps["numpy"]["version"], "1.26.4")
        self.assertIsNone(deps["pytest"]["version"])
        self.assertTrue(deps["gitlib"]["pinned"])  # direct URL counts as pinned
        self.assertEqual(result["summary"]["total"], 5)
        self.assertEqual(result["files"], ["requirements.txt"])
        self.assertTrue(all(d["ecosystem"] == "PyPI" for d in result["dependencies"]))

    def test_pyproject_dependencies(self):
        tmp = _make_repo({
            "pyproject.toml": (
                "[project]\n"
                "name = \"demo\"\n"
                "dependencies = [\"fastapi>=0.110\", \"pydantic==2.6.1\"]\n"
                "[project.optional-dependencies]\n"
                "dev = [\"ruff==0.3.0\"]\n"
            ),
        })
        with tmp:
            result = parse_dependencies(tmp.name)
        deps = {d["name"]: d for d in result["dependencies"]}
        self.assertEqual(deps["pydantic"]["version"], "2.6.1")
        self.assertTrue(deps["pydantic"]["pinned"])
        self.assertFalse(deps["fastapi"]["pinned"])
        self.assertEqual(deps["ruff"]["scope"], "optional")
        self.assertEqual(deps["ruff"]["source_file"], "pyproject.toml")

    def test_package_json_scopes_and_ranges(self):
        tmp = _make_repo({
            "package.json": (
                '{"dependencies": {"react": "^18.2.0", "lodash": "4.17.21"},'
                ' "devDependencies": {"jest": "~29.7.0"}}'
            ),
        })
        with tmp:
            result = parse_dependencies(tmp.name)
        deps = {d["name"]: d for d in result["dependencies"]}
        self.assertEqual(deps["react"]["version"], "18.2.0")
        self.assertFalse(deps["react"]["pinned"])
        self.assertTrue(deps["lodash"]["pinned"])
        self.assertEqual(deps["jest"]["scope"], "optional")
        self.assertTrue(all(d["ecosystem"] == "npm" for d in result["dependencies"]))

    def test_dedup_and_purl(self):
        tmp = _make_repo({
            "requirements.txt": "Flask==3.0.0\nflask==3.0.0\n",
        })
        with tmp:
            result = parse_dependencies(tmp.name)
        self.assertEqual(len(result["dependencies"]), 1)
        dep = result["dependencies"][0]
        self.assertEqual(dep["purl"], "pkg:pypi/flask@3.0.0")
        self.assertEqual(purl_for({"name": "@scope/pkg", "version": "1.0.0", "ecosystem": "npm"}),
                         "pkg:npm/%40scope/pkg@1.0.0")

    def test_missing_directory_rejected(self):
        with self.assertRaises(ValueError):
            parse_dependencies("definitely/not/a/repo")


if __name__ == "__main__":
    unittest.main()
