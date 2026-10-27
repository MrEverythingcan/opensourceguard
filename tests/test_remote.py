import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from opensourceguard import remote


README_SAMPLE = """# Cool Tool

A fast Python CLI for cleaning datasets with LLM agents.

## Features
- Batch processing with PyTorch acceleration for huge files
- One-command docker deployment for teams

## Install
pip install cooltool
docker run -p 8000:8000 cooltool
"""


def _meta(**overrides):
    meta = {
        "description": "Clean datasets fast",
        "default_branch": "main",
        "stargazers_count": 1500,
        "forks_count": 120,
        "open_issues_count": 7,
        "language": "Python",
        "topics": ["data-cleaning", "llm"],
        "pushed_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
        "license": {"spdx_id": "MIT"},
        "archived": False,
    }
    meta.update(overrides)
    return meta


def _fake_fetch(meta=None, readme=README_SAMPLE, license_text=None):
    def _fetch(url, limit=12000):
        if url.endswith("/readme"):
            if readme is None:
                raise OSError("404")
            return json.dumps({"content": base64.b64encode(readme.encode()).decode()})
        if "raw.githubusercontent.com" in url:
            if license_text is None:
                raise OSError("404")
            return license_text
        if "api.github.com/repos/" in url:
            return json.dumps(meta if meta is not None else _meta())
        raise OSError("unexpected url: " + url)
    return _fetch


class InspectTests(unittest.TestCase):
    def test_github_metadata_and_readme_signals(self):
        with mock.patch.object(remote, "_fetch", _fake_fetch()):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool")
        self.assertEqual(result["stars"], 1500)
        self.assertEqual(result["forks"], 120)
        self.assertEqual(result["topics"], ["data-cleaning", "llm"])
        self.assertEqual(result["license"], "MIT")
        self.assertEqual(result["commercial_fit"], "通常可以商用")
        stack = result["analysis"]["stack_signals"]
        self.assertIn("Python", stack)
        self.assertIn("PyTorch", stack)
        self.assertIn("Docker", stack)
        installs = result["analysis"]["install_commands"]
        self.assertIn("pip install cooltool", installs)
        self.assertTrue(any(cmd.startswith("docker run") for cmd in installs))
        self.assertGreaterEqual(len(result["analysis"]["highlights"]), 2)
        self.assertEqual(result["summary_source"], "rules")
        self.assertIn("项目自述", result["summary"])
        self.assertTrue(any("pip install cooltool" in step for step in result["next_steps"]))
        self.assertEqual(result["risks"], [])

    def test_license_text_fallback_when_noassertion(self):
        apache = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/"
        with mock.patch.object(remote, "_fetch", _fake_fetch(meta=_meta(license={"spdx_id": "NOASSERTION"}), license_text=apache)):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool")
        self.assertEqual(result["license"], "Apache-2.0")
        self.assertEqual(result["commercial_fit"], "通常可以商用")

    def test_license_unrecognized_when_no_file(self):
        with mock.patch.object(remote, "_fetch", _fake_fetch(meta=_meta(license=None))):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool")
        self.assertEqual(result["license"], "未识别")
        self.assertTrue(any("许可证" in risk for risk in result["risks"]))

    def test_model_callback_overrides_summary(self):
        with mock.patch.object(remote, "_fetch", _fake_fetch()):
            result = remote.inspect_repository_url(
                "https://github.com/acme/cooltool",
                model_callback=lambda prompt: "一、这是一个数据清洗工具。\n二、亮点是快。",
            )
        self.assertEqual(result["summary_source"], "model")
        self.assertTrue(result["summary"].startswith("一、"))

    def test_model_callback_failure_falls_back(self):
        def _boom(prompt):
            raise RuntimeError("api down")
        with mock.patch.object(remote, "_fetch", _fake_fetch()):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool", model_callback=_boom)
        self.assertEqual(result["summary_source"], "rules")
        self.assertIn("项目自述", result["summary"])
        self.assertTrue(any("AI 详细解读" in note for note in result["notes"]))

    def test_fetch_failure_keeps_fallback_fields(self):
        def _fail(url, limit=12000):
            raise OSError("network down")
        with mock.patch.object(remote, "_fetch", _fail):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool")
        self.assertEqual(result["fetch_error"], "OSError")
        self.assertEqual(result["summary_source"], "rules")
        self.assertTrue(result["summary"])
        self.assertTrue(result["one_sentence"])
        self.assertTrue(any("README" in risk for risk in result["risks"]))
        self.assertTrue(any("许可证" in risk for risk in result["risks"]))
        self.assertTrue(result["next_steps"])

    def test_low_stars_adds_risk(self):
        with mock.patch.object(remote, "_fetch", _fake_fetch(meta=_meta(stargazers_count=3, forks_count=0))):
            result = remote.inspect_repository_url("https://github.com/acme/cooltool")
        self.assertTrue(any("Star" in risk for risk in result["risks"]))

    def test_validate_repo_url_rejects_bad_host(self):
        with self.assertRaises(ValueError):
            remote.validate_repo_url("https://evil.example.com/a/b")


if __name__ == "__main__":
    unittest.main()
