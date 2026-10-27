import tempfile
import unittest
from pathlib import Path

from opensourceguard.beginner import BeginnerAgent, LocalDeploymentManager, recommend_projects, topics_for_interests


class BeginnerTests(unittest.TestCase):
    def test_explains_license_and_stack(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Demo\nA small Python service.")
            (root / "LICENSE").write_text("MIT License\nPermission is hereby granted")
            (root / "pyproject.toml").write_text("[project]\nname='demo'")
            data = BeginnerAgent().explain(root)
            self.assertEqual(data["license"], "MIT")
            self.assertIn("Python", data["deployment"]["stack"])
            self.assertIn("商用", data["commercial_fit"])

    def test_deployment_is_preview_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "pyproject.toml").write_text("[project]\nname='demo'")
            data = LocalDeploymentManager().execute(root, confirm=False)
            self.assertEqual(data["status"], "preview")
            self.assertTrue(data["plan"]["requires_confirmation"])

    def test_beginner_can_answer_follow_up(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "README.md").write_text("# Demo\nA Python service.")
            (root / "LICENSE").write_text("MIT License\nPermission is hereby granted")
            (root / "pyproject.toml").write_text("[project]\nname='demo'")
            answer = BeginnerAgent().ask(root, "这个项目适合商用吗？")
            self.assertIn("许可证", answer["answer"])
            self.assertEqual(answer["source"], "local-analysis")
            purpose = BeginnerAgent().ask(root, "这个仓库是做什么的？")
            self.assertIn("简单说", purpose["answer"])


class RecommendTests(unittest.TestCase):
    ITEMS = [
        {"name": "a/web-starter", "url": "https://github.com/a/web-starter", "platform": "github", "stars": 900, "description": "A tiny starter site.", "tag": "web", "language": "JavaScript"},
        {"name": "b/llm-large", "url": "https://github.com/b/llm-large", "platform": "github", "stars": 50000, "description": "Large language model.", "tag": "llm", "language": "Python"},
        {"name": "c/web-components", "url": "https://github.com/c/web-components", "platform": "github", "stars": 3000, "description": "Reusable web UI parts.", "tag": "web", "language": "TypeScript"},
    ]

    def test_topics_mapping(self):
        topics = topics_for_interests("我对人工智能和大模型感兴趣")
        self.assertIn("machine-learning", topics)
        self.assertIn("llm", topics)
        self.assertEqual(topics_for_interests("随便看看"), ())

    def test_deterministic_ranking_and_fields(self):
        result = recommend_projects("网页开发", self.ITEMS)
        projects = result["projects"]
        self.assertEqual([p["name"] for p in projects], ["c/web-components", "a/web-starter", "b/llm-large"])
        self.assertTrue(projects[0]["matched"])
        self.assertFalse(projects[2]["matched"])
        self.assertIn("星", projects[0]["why"])
        self.assertEqual(result["source"], "trending-cache")
        self.assertIn("网页开发", result["answer"])
        path = result["learning_path"]
        self.assertEqual(len(path), 5)
        self.assertEqual([s["step"] for s in path], [1, 2, 3, 4, 5])
        self.assertIn("c/web-components", path[1]["detail"])

    def test_model_polish_and_fallback(self):
        polished = recommend_projects("网页开发", self.ITEMS, model_callback=lambda prompt: "这是为你定制的入门建议")
        self.assertEqual(polished["answer"], "这是为你定制的入门建议")
        self.assertEqual(polished["source"], "model+trending")

        def broken(prompt):
            raise RuntimeError("model offline")

        fallback = recommend_projects("网页开发", self.ITEMS, model_callback=broken)
        self.assertEqual(fallback["source"], "trending-cache")
        self.assertIn("网页开发", fallback["answer"])

    def test_empty_interests_rejected(self):
        with self.assertRaises(ValueError):
            recommend_projects("  ", self.ITEMS)


if __name__ == "__main__":
    unittest.main()
