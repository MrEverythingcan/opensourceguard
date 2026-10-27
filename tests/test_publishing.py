import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opensourceguard.open_source import complete_open_source
from opensourceguard.publishing import PlatformError, publish_project
from opensourceguard.tooling import ToolRegistry
from opensourceguard.beginner import BeginnerAgent


class PublishingTests(unittest.TestCase):
    def test_completion_is_preview_then_additive(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.py").write_text("print('ok')", encoding="utf-8")
            preview = complete_open_source(root)
            self.assertEqual(preview["status"], "preview")
            self.assertIn("README.md", {x["path"] for x in preview["files"]})
            done = complete_open_source(root, confirm=True)
            self.assertEqual(done["status"], "completed")
            self.assertTrue((root / "README.md").is_file())
            self.assertTrue((root / "LICENSE").is_file())

    def test_publish_plan_never_writes_without_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("demo", encoding="utf-8")
            (root / "LICENSE").write_text("MIT License\nPermission is hereby granted", encoding="utf-8")
            (root / ".gitignore").write_text(".env\n", encoding="utf-8")
            (root / "tests").mkdir()
            account = {"user": {"login": "alice"}, "token": "alic••••ken", "repositories": [], "status": "connected"}
            with patch("opensourceguard.publishing._github_connect", return_value=account):
                plan = publish_project(root, platform="github", repository="demo", token="github-test-token", confirm=False)
            self.assertEqual(plan["status"], "preview")
            self.assertFalse((root / ".git").exists())

    def test_write_tool_requires_explicit_confirmation(self):
        registry = ToolRegistry(BeginnerAgent())
        with self.assertRaises(ValueError):
            registry.call("repository.open_source_complete", {"repo": "."})

    def test_short_token_is_rejected_before_network(self):
        with self.assertRaises(PlatformError):
            publish_project(".", platform="github", token="short", confirm=False)


if __name__ == "__main__":
    unittest.main()
