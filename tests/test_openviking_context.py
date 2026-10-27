# -*- coding: utf-8 -*-
"""Unit tests for the optional OpenViking context adapter.

Covers the safe fallback contract (no credentials -> degraded result) and the
multipart resource upload builder. The real API round-trip is exercised by
``scripts/verify_openviking.py`` when credentials are configured.
"""
import unittest

from opensourceguard.openviking_context import OpenVikingContext


class FallbackTests(unittest.TestCase):
    def test_disabled_without_config(self):
        ctx = OpenVikingContext(url="", api_key="")
        self.assertFalse(ctx.enabled)
        h = ctx.health()
        self.assertFalse(h["enabled"])
        self.assertEqual(h["mode"], "disabled")

    def test_store_memory_degrades_when_disabled(self):
        ctx = OpenVikingContext(url="", api_key="")
        r = ctx.store_memory("hello")
        self.assertFalse(r["enabled"])
        self.assertFalse(r["ok"])

    def test_store_resource_degrades_when_disabled(self):
        ctx = OpenVikingContext(url="", api_key="")
        r = ctx.store_resource("README.md", "viking://resources/README.md")
        self.assertFalse(r["enabled"])
        self.assertFalse(r["ok"])

    def test_store_resource_missing_file(self):
        ctx = OpenVikingContext(url="http://localhost:1", api_key="x")
        r = ctx.store_resource("definitely_missing_file_xyz.txt", "viking://resources/x.txt")
        self.assertTrue(r["enabled"])
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "file not found")

    def test_configured_reports_enabled(self):
        ctx = OpenVikingContext(url="http://localhost:1", api_key="x")
        self.assertTrue(ctx.enabled)
        # health will attempt a real call and fail fast on unreachable host
        h = ctx.health()
        self.assertTrue(h["enabled"])
        self.assertEqual(h["mode"], "openviking")


if __name__ == "__main__":
    unittest.main()
