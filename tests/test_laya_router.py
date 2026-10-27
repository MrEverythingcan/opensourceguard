# -*- coding: utf-8 -*-
"""Unit tests for the optional Laya typed-decision router (laya_router.py).

The tests cover deterministic keyword routing and the safe fallback contract.
Laya model execution is NOT required here (keyword rules and fallbacks are
pure Python); the model path is exercised by the live integration script
``scripts/verify_laya.py`` when the local venv is available.
"""
import unittest
from unittest.mock import patch

from opensourceguard.laya_router import LayaRouter, _rule_route


class RuleRoutingTests(unittest.TestCase):
    def test_deploy(self):
        self.assertEqual(_rule_route("帮我部署这个项目"), "deploy")

    def test_complete(self):
        self.assertEqual(_rule_route("项目缺 README 帮我补齐"), "complete")

    def test_verify(self):
        self.assertEqual(_rule_route("发布前帮我跑一遍测试"), "verify")

    def test_secure(self):
        self.assertEqual(_rule_route("看看 flask 有没有漏洞"), "secure")

    def test_organize(self):
        self.assertEqual(_rule_route("项目太乱了帮我整理"), "organize")

    def test_publish(self):
        self.assertEqual(_rule_route("推送到 GitHub"), "publish")

    def test_maintain(self):
        self.assertEqual(_rule_route("这个 issue 怎么修"), "maintain")

    def test_explain(self):
        self.assertEqual(_rule_route("这个仓库是干什么的"), "explain")

    def test_health(self):
        self.assertEqual(_rule_route("给我项目打个健康度分"), "health")

    def test_no_keyword_hit(self):
        self.assertIsNone(_rule_route("我想做游戏"))


class FallbackContractTests(unittest.TestCase):
    def test_empty_input(self):
        r = LayaRouter(venv_py=False)
        d = r.decide("")
        self.assertEqual(d["source"], "empty-input")
        self.assertEqual(d["confidence"], 0.0)

    def test_no_venv_falls_back_safely(self):
        r = LayaRouter(venv_py=False)
        d = r.decide("帮我优化一下项目")
        self.assertEqual(d["page"], "explain")
        self.assertEqual(d["source"], "fallback")
        self.assertTrue(d["needs_human"])

    def test_health_reports_unavailable_without_venv(self):
        r = LayaRouter(venv_py=False)
        h = r.health()
        self.assertFalse(h["available"])
        self.assertEqual(h["mode"], "rule-only")

    def test_keyword_rule_wins_even_without_venv(self):
        r = LayaRouter(venv_py=False)
        d = r.decide("项目缺 README 帮我补齐")
        self.assertEqual(d["page"], "complete")
        self.assertEqual(d["source"], "keyword-rule")
        self.assertEqual(d["confidence"], 1.0)

    @patch("opensourceguard.laya_router.LayaRouter.available", return_value=False)
    def test_laya_error_never_raises(self, _avail):
        # Simulate subprocess failure: decide must degrade, not raise.
        r = LayaRouter(venv_py=False)
        d = r.decide("我想做游戏")
        self.assertIn(d["source"], ("fallback", "laya-error", "low-confidence"))


if __name__ == "__main__":
    unittest.main()
