import tempfile
import unittest
from pathlib import Path

from opensourceguard.security import scan_repository
from opensourceguard.integrations import build_provider_plan
from opensourceguard.iteration import iterate_repair
from opensourceguard.runtime import runtime_plan
from opensourceguard.reporting import build_report, render_markdown
from opensourceguard.open_source import assess_open_source, publish_preview
from opensourceguard.tooling import ToolRegistry
from opensourceguard.beginner import BeginnerAgent
from opensourceguard.remote import validate_repo_url

class RecoveryFeatureTests(unittest.TestCase):
    def test_security_scan_and_runtime_plan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "README.md").write_text("demo"); (root / "pyproject.toml").write_text("[project]\nname='x'")
            result = scan_repository(root)
            self.assertEqual(result["risk_level"], "high")
            self.assertIn("Python", runtime_plan(root)["stack"])

    def test_runtime_detects_python_source_without_dependency_file(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
            plan = runtime_plan(root)
            self.assertIn("Python", plan["stack"])
            self.assertTrue(plan["steps"])

    def test_provider_and_iteration_are_dry_run(self):
        plan = build_provider_plan("gitlab", "org/repo", use_fork=True)
        self.assertFalse(plan["remote_write"]); self.assertIn("Fork", " ".join(plan["steps"]))
        result = iterate_repair("empty input", "IndexError: list index out of range")
        self.assertIn("边界条件/空输入", result["failure_signals"])

    def test_report_export(self):
        report = build_report(".", scan={"files_scanned": 1, "risk_level": "low", "status": "ok", "findings": []}, status={"counts": {}}, jobs=[])
        self.assertIn("OpenSourceGuard", render_markdown(report))

    def test_open_source_assessment_is_safe_preview(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "README.md").write_text("demo"); (root / "LICENSE").write_text("MIT License")
            (root / ".gitignore").write_text(".env\n"); (root / "tests").mkdir()
            result = assess_open_source(root)
            self.assertTrue(result["can_open_source"])
            self.assertEqual(publish_preview(root)["status"], "preview")

    def test_agent_tool_registry_exposes_safe_tools(self):
        registry = ToolRegistry(BeginnerAgent())
        names = {x["name"] for x in registry.describe()}
        self.assertIn("repository.security_scan", names)
        self.assertIn("repository.deploy_preview", names)

    def test_modelscope_public_url_is_understood(self):
        raw, host, path = validate_repo_url("https://modelscope.cn/models/demo/project")
        self.assertEqual(host, "modelscope.cn")
        self.assertEqual(path, "demo/project")

if __name__ == "__main__": unittest.main()
