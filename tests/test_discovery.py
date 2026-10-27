import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from opensourceguard import discovery


class ParseTests(unittest.TestCase):
    def test_github_parsing_and_dedup(self):
        payload = {"items": [
            {"full_name": "a/b", "html_url": "https://github.com/a/b", "description": "demo", "stargazers_count": 1200, "language": "Python"},
            {"full_name": "a/b", "description": "dup"},
            {"full_name": "c/d", "description": None, "stargazers_count": 0, "language": ""},
        ]}
        with mock.patch.object(discovery, "_http_json", return_value=payload):
            items = discovery.fetch_github_hot(topics=("llm",), per_topic=4)
        self.assertEqual([x["name"] for x in items], ["a/b", "c/d"])
        self.assertEqual(items[0]["platform"], "github")
        self.assertEqual(items[0]["stars"], 1200)
        self.assertEqual(items[1]["description"], "这个仓库还没有写简介")

    def test_modelscope_parsing(self):
        payload = {"Data": {"Model": {"Models": [
            {"Path": "AI-ModelScope", "Name": "demo", "ChineseName": "演示模型", "Stars": 88, "Downloads": 9999, "Tasks": [{"Name": "文本生成"}]},
            {"Path": "", "Name": "skip-me"},
        ]}}}
        with mock.patch.object(discovery, "_http_json", return_value=payload):
            items = discovery.fetch_modelscope_hot()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "AI-ModelScope/demo")
        self.assertEqual(items[0]["url"], "https://modelscope.cn/models/AI-ModelScope/demo")
        self.assertEqual(items[0]["tag"], "文本生成")
        self.assertEqual(items[0]["downloads"], 9999)


class FeedTests(unittest.TestCase):
    def _feed(self, td, fetcher, ttl_hours=6.0):
        return discovery.DiscoveryFeed(Path(td) / "cache.json", ttl_hours=ttl_hours, fetcher=fetcher)

    def test_fresh_cache_skips_fetch(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            def fetcher():
                calls.append(1)
                return [{"platform": "github", "name": "a/b"}], []
            feed = self._feed(td, fetcher)
            first = feed.get()
            second = feed.get()
            self.assertEqual(len(calls), 1)
            self.assertFalse(first["stale"])
            self.assertEqual(second["items"][0]["name"], "a/b")
            self.assertTrue(second["updated_at_text"])

    def test_expired_cache_refetches(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            cache.write_text(json.dumps({"fetched_at": time.time() - 99999, "items": [{"name": "old"}]}), encoding="utf-8")
            feed = self._feed(td, lambda: ([{"name": "new"}], []))
            data = feed.get()
            self.assertEqual(data["items"][0]["name"], "new")

    def test_fetch_failure_falls_back_to_stale_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = Path(td) / "cache.json"
            cache.write_text(json.dumps({"fetched_at": time.time() - 99999, "items": [{"name": "old"}]}), encoding="utf-8")
            feed = self._feed(td, lambda: (_ for _ in ()).throw(RuntimeError("网络炸了")))
            data = feed.get()
            self.assertTrue(data["stale"])
            self.assertEqual(data["items"][0]["name"], "old")
            self.assertIn("上一次缓存", data["warning"])

    def test_fetch_failure_without_cache_returns_empty(self):
        with tempfile.TemporaryDirectory() as td:
            feed = self._feed(td, lambda: (_ for _ in ()).throw(RuntimeError("网络炸了")))
            data = feed.get()
            self.assertTrue(data["stale"])
            self.assertEqual(data["items"], [])
            self.assertIn("暂时抓不到推荐", data["warning"])

    def test_partial_failure_warns_but_serves_items(self):
        with tempfile.TemporaryDirectory() as td:
            feed = self._feed(td, lambda: ([{"name": "a/b"}], ["魔搭 抓取失败：超时"]))
            data = feed.get()
            self.assertFalse(data["stale"])
            self.assertIn("本次缺席", data["warning"])

    def test_force_refresh_bypasses_fresh_cache(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            def fetcher():
                calls.append(1)
                return [{"name": f"run-{len(calls)}"}], []
            feed = self._feed(td, fetcher)
            feed.get()
            data = feed.get(force=True)
            self.assertEqual(len(calls), 2)
            self.assertEqual(data["items"][0]["name"], "run-2")


if __name__ == "__main__":
    unittest.main()
