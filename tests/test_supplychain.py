"""Supply-chain: OSV vulnerability matching, SBOM shape and baseline scorecard."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from opensourceguard.supplychain import baseline_check, generate_sbom, scan_vulnerabilities, supplychain_scan


def _make_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp


VULN = {
    "id": "GHSA-jfmj-6v7g-cx8m",
    "aliases": ["CVE-2024-99999"],
    "summary": "Flask session fixation via crafted cookie.",
    "affected": [{
        "package": {"name": "flask", "ecosystem": "PyPI"},
        "database_specific": {"severity": "HIGH"},
        "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": "3.0.1"}]}],
    }],
}


def fake_fetcher(payload):
    results = []
    for query in payload["queries"]:
        if query["package"]["name"] == "flask" and query.get("version") == "3.0.0":
            results.append({"vulns": [VULN]})
        else:
            results.append({"vulns": []})
    return {"results": results}


class VulnerabilityScanTests(unittest.TestCase):
    def test_vulnerability_fields_and_counts(self):
        tmp = _make_repo({"requirements.txt": "flask==3.0.0\nrequests==2.31.0\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=fake_fetcher)
        self.assertTrue(result["queried"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(result["counts"]["high"], 1)
        self.assertEqual(result["counts"]["packages_affected"], 1)
        vuln = result["vulnerabilities"][0]
        self.assertEqual(vuln["package"], "flask")
        self.assertEqual(vuln["version"], "3.0.0")
        self.assertEqual(vuln["id"], "GHSA-jfmj-6v7g-cx8m")
        self.assertEqual(vuln["aliases"], ["CVE-2024-99999"])
        self.assertEqual(vuln["severity"], "high")
        self.assertEqual(vuln["fixed_version"], "3.0.1")
        self.assertIn("osv.dev/vulnerability/", vuln["link"])
        self.assertTrue(any("flask→3.0.1" in s for s in result["suggestions"]))
        self.assertEqual(result["source"], "osv+local")

    def test_stub_entries_are_enriched_via_detail_getter(self):
        def stub_fetcher(payload):
            return {"results": [{"vulns": [{"id": "GHSA-jfmj-6v7g-cx8m", "modified": "2024-01-01"}]}
                                for _ in payload["queries"]]}

        def getter(vuln_id):
            self.assertEqual(vuln_id, "GHSA-jfmj-6v7g-cx8m")
            return VULN

        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=stub_fetcher, getter=getter)
        self.assertEqual(result["counts"]["total"], 1)
        vuln = result["vulnerabilities"][0]
        self.assertEqual(vuln["severity"], "high")
        self.assertEqual(vuln["fixed_version"], "3.0.1")
        self.assertEqual(vuln["summary"], VULN["summary"])

    def test_stub_entries_survive_when_detail_fetch_fails(self):
        def stub_fetcher(payload):
            return {"results": [{"vulns": [{"id": "GHSA-x", "modified": "2024-01-01"}]}
                                for _ in payload["queries"]]}

        def broken_getter(vuln_id):
            raise OSError("offline")

        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=stub_fetcher, getter=broken_getter)
        self.assertEqual(result["counts"]["total"], 1)
        vuln = result["vulnerabilities"][0]
        self.assertEqual(vuln["id"], "GHSA-x")
        self.assertEqual(vuln["severity"], "unknown")
        self.assertIn("osv.dev/vulnerability/GHSA-x", vuln["link"])

    def test_offline_degrades_without_raising(self):
        def offline(payload):
            raise OSError("network unreachable")

        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=offline)
        self.assertFalse(result["queried"])
        self.assertIn("network unreachable", result["error"])
        self.assertEqual(result["vulnerabilities"], [])
        self.assertTrue(any("OSV" in s for s in result["suggestions"]))
        self.assertEqual(result["source"], "local-parse")

    def test_clean_project(self):
        tmp = _make_repo({"requirements.txt": "requests==2.31.0\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=fake_fetcher)
        self.assertEqual(result["counts"]["total"], 0)
        self.assertTrue(any("未发现" in s for s in result["suggestions"]))

    def test_unpinned_dependencies_are_skipped_but_reported(self):
        tmp = _make_repo({"requirements.txt": "flask\n"})
        with tmp:
            result = scan_vulnerabilities(tmp.name, fetcher=fake_fetcher)
        self.assertEqual(result["vulnerabilities"], [])
        self.assertTrue(result["queried"])  # parse succeeded even with nothing to query
        self.assertTrue(any("未锁定版本" in s for s in result["suggestions"]))

    def test_model_polish_and_fallback(self):
        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n"})
        with tmp:
            polished = scan_vulnerabilities(tmp.name, fetcher=fake_fetcher, model_callback=lambda p: "先升 flask，再锁版本。")
        self.assertEqual(polished["advice"], "先升 flask，再锁版本。")
        self.assertEqual(polished["source"], "model+osv")

        def broken(prompt):
            raise RuntimeError("offline")

        tmp2 = _make_repo({"requirements.txt": "flask==3.0.0\n"})
        with tmp2:
            fallback = scan_vulnerabilities(tmp2.name, fetcher=fake_fetcher, model_callback=broken)
        self.assertEqual(fallback["source"], "osv+local")
        self.assertIn("漏洞", fallback["advice"])


class SbomTests(unittest.TestCase):
    def test_cyclonedx_shape(self):
        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n", "package.json": '{"dependencies": {"react": "^18.2.0"}}'})
        with tmp:
            sbom = generate_sbom(tmp.name, project_name="demo-app")
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertTrue(sbom["serialNumber"].startswith("urn:uuid:"))
        self.assertEqual(sbom["metadata"]["component"]["name"], "demo-app")
        self.assertEqual(sbom["metadata"]["tools"][0]["name"], "supplychain")
        comps = {c["name"]: c for c in sbom["components"]}
        self.assertEqual(comps["flask"]["purl"], "pkg:pypi/flask@3.0.0")
        self.assertEqual(comps["flask"]["scope"], "required")
        self.assertEqual(comps["react"]["version"], "18.2.0")
        self.assertEqual(comps["react"]["purl"], "pkg:npm/react@18.2.0")


class BaselineTests(unittest.TestCase):
    GOOD = {
        "LICENSE": "MIT License\n\nPermission is hereby granted",
        "README.md": "# demo\n\nusage\n",
        "SECURITY.md": "report to security@example.com",
        "CONTRIBUTING.md": "how to contribute",
        "requirements.txt": "flask==3.0.0\n",
        ".github/workflows/ci.yml": "name: ci\n",
        "tests/test_demo.py": "def test_ok():\n    assert True\n",
    }

    def test_good_project_scores_high(self):
        tmp = _make_repo(self.GOOD)
        with tmp:
            result = baseline_check(tmp.name, vulnerabilities=[])
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["passed"], result["total"])
        self.assertEqual(result["domains"]["Legal"], {"passed": 1, "total": 1})
        self.assertEqual(result["domains"]["Vulnerability Management"]["passed"], 2)
        ids = {c["id"] for c in result["checks"]}
        self.assertIn("vulnerability-free", ids)
        self.assertIn("dependency-pinning", ids)

    def test_rough_project_fails_with_suggestions(self):
        tmp = _make_repo({"app.py": "print('hi')\n", "requirements.txt": "flask\n"})
        with tmp:
            result = baseline_check(tmp.name, vulnerabilities=[{"package": "flask", "version": None, "id": "X", "aliases": [], "summary": "", "severity": "high", "fixed_version": "3.0.1", "link": ""}])
        self.assertLess(result["score"], 40)
        failed = {c["id"] for c in result["checks"] if c["status"] == "failed"}
        self.assertIn("license-file", failed)
        self.assertIn("security-policy", failed)
        self.assertIn("vulnerability-free", failed)
        self.assertIn("dependency-pinning", failed)
        self.assertTrue(all(c["suggestion"] for c in result["checks"] if c["status"] == "failed"))

    def test_skipped_when_vulnerabilities_not_scanned(self):
        tmp = _make_repo(self.GOOD)
        with tmp:
            result = baseline_check(tmp.name, vulnerabilities=None)
        skipped = [c for c in result["checks"] if c["id"] == "vulnerability-free"]
        self.assertEqual(skipped[0]["status"], "skipped")
        self.assertEqual(result["total"], len(result["checks"]) - 1)

    def test_supplychain_scan_aggregates(self):
        tmp = _make_repo({"requirements.txt": "flask==3.0.0\n", "LICENSE": "MIT License\n\nPermission is hereby granted"})
        with tmp:
            result = supplychain_scan(tmp.name, fetcher=fake_fetcher)
        self.assertIn("sbom", result)
        self.assertEqual(result["sbom"]["bomFormat"], "CycloneDX")
        self.assertEqual(result["counts"]["total"], 1)
        self.assertEqual(result["baseline"]["domains"]["Vulnerability Management"]["total"], 2)
        self.assertTrue(result["baseline"]["summary"])


if __name__ == "__main__":
    unittest.main()
