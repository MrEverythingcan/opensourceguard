"""Supply-chain security: OSV vulnerability scan, CycloneDX SBOM and an
OSPS-Baseline-aligned scorecard, all built on the shared dependency parser.

Network access is a single POST to the public OSV.dev API (no key required).
Everything degrades gracefully offline: parsing, SBOM and the scorecard keep
working, and the vulnerability section is marked ``osv-unavailable``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import request
from urllib.parse import quote

from .dependencies import parse_dependencies
from .security import scan_repository

OSV_QUERYBATCH = "https://api.osv.dev/v1/querybatch"
OSV_API_VULN = "https://api.osv.dev/v1/vulns/"
OSV_VULN_URL = "https://osv.dev/vulnerability/"
TOOL_VERSION = "0.6.0"
MAX_DETAIL_FETCH = 25

# OSPS Baseline control domains (full names, aligned with the OpenSSF
# Open Source Project Security Baseline v2026 catalog structure).
DOMAIN_LEGAL = "Legal"
DOMAIN_VM = "Vulnerability Management"
DOMAIN_BR = "Build & Release"
DOMAIN_QUALITY = "Quality"
DOMAIN_SA = "Security Assessment"
DOMAIN_DOCS = "Documentation"
DOMAIN_GOV = "Governance"

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}


def _default_fetcher(timeout: float) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def fetch(payload: dict[str, Any]) -> dict[str, Any]:
        req = request.Request(
            OSV_QUERYBATCH,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": f"OpenSourceGuard/{TOOL_VERSION}"},
        )
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return fetch


def _default_getter(timeout: float) -> Callable[[str], dict[str, Any]]:
    def get(vuln_id: str) -> dict[str, Any]:
        req = request.Request(
            OSV_API_VULN + quote(vuln_id, safe=""),
            headers={"User-Agent": f"OpenSourceGuard/{TOOL_VERSION}"},
        )
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    return get


def _severity_of(vuln: dict[str, Any]) -> str:
    for affected in vuln.get("affected") or []:
        severity = ((affected.get("database_specific") or {}).get("severity") or "").lower()
        if severity in _SEVERITY_ORDER:
            return "medium" if severity == "moderate" else severity
    severity = ((vuln.get("database_specific") or {}).get("severity") or "").lower()
    if severity in _SEVERITY_ORDER:
        return "medium" if severity == "moderate" else severity
    return "unknown"


def _fixed_version_of(vuln: dict[str, Any], package: str) -> str | None:
    for affected in vuln.get("affected") or []:
        if ((affected.get("package") or {}).get("name") or "").lower() != package:
            continue
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    return str(event["fixed"])
    for affected in vuln.get("affected") or []:  # fall back to any package entry
        for rng in affected.get("ranges") or []:
            for event in rng.get("events") or []:
                if event.get("fixed"):
                    return str(event["fixed"])
    return None


def query_osv(dependencies: list[dict[str, Any]], *, fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None, getter: Callable[[str], dict[str, Any]] | None = None, timeout: float = 8) -> tuple[list[dict[str, Any]], str | None]:
    """Query OSV.dev for the pinned/versioned subset. Returns (vulns, error).

    The querybatch endpoint may return stub entries that only carry an id; those
    are enriched one-by-one via ``GET /v1/vulns/{id}`` (deduplicated, capped).
    """

    queries: list[dict[str, Any]] = []
    subjects: list[dict[str, Any]] = []
    for dep in dependencies:
        if not dep.get("version"):
            continue  # without a version the whole advisory list would match
        queries.append({"package": {"name": dep["name"], "ecosystem": dep["ecosystem"]}, "version": dep["version"]})
        subjects.append(dep)
    if not queries:
        return [], None
    fetch = fetcher or _default_fetcher(timeout)
    try:
        response = fetch({"queries": queries})
    except Exception as exc:  # noqa: BLE001 - offline demo must not crash
        return [], f"{type(exc).__name__}: {exc}"
    raw: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for dep, result in zip(subjects, response.get("results") or []):
        for vuln in (result or {}).get("vulns") or []:
            raw.append((dep, vuln))
    stub_ids = [v.get("id") for _, v in raw if set(v.keys()) <= {"id", "modified"} and v.get("id")]
    details: dict[str, dict[str, Any]] = {}
    if stub_ids:
        get = getter or _default_getter(timeout)
        for vuln_id in list(dict.fromkeys(stub_ids))[:MAX_DETAIL_FETCH]:
            try:
                details[vuln_id] = get(vuln_id)
            except Exception:  # noqa: BLE001 - keep the stub, degrade gracefully
                continue
    vulns: list[dict[str, Any]] = []
    for dep, stub in raw:
        vuln = details.get(stub.get("id", "")) or stub
        vulns.append({
            "package": dep["name"],
            "ecosystem": dep["ecosystem"],
            "version": dep["version"],
            "id": stub.get("id", ""),
            "aliases": [a for a in (vuln.get("aliases") or []) if str(a).startswith("CVE")][:3],
            "summary": (vuln.get("summary") or "").strip() or "无摘要，点击查看详情。",
            "severity": _severity_of(vuln),
            "fixed_version": _fixed_version_of(vuln, dep["name"]),
            "link": OSV_VULN_URL + str(stub.get("id", "")),
        })
    vulns.sort(key=lambda v: (_SEVERITY_ORDER.get(v["severity"], 4), v["package"]))
    return vulns, None


def scan_vulnerabilities(repo: str | Path, *, fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None, getter: Callable[[str], dict[str, Any]] | None = None, model_callback: Callable[[str], str] | None = None, timeout: float = 8) -> dict[str, Any]:
    """Parse dependencies and match them against the OSV vulnerability database."""

    parsed = parse_dependencies(repo)
    deps = parsed["dependencies"]
    vulns, error = query_osv(deps, fetcher=fetcher, getter=getter, timeout=timeout)
    counts = {
        "total": len(vulns),
        "critical": sum(1 for v in vulns if v["severity"] == "critical"),
        "high": sum(1 for v in vulns if v["severity"] == "high"),
        "medium": sum(1 for v in vulns if v["severity"] == "medium"),
        "low": sum(1 for v in vulns if v["severity"] == "low"),
        "unknown": sum(1 for v in vulns if v["severity"] == "unknown"),
        "packages_affected": len({v["package"] for v in vulns}),
    }
    suggestions: list[str] = []
    if error:
        suggestions.append("当前无法连接 OSV 漏洞库，解析结果已保留；联网后重新扫描即可。")
    elif not deps:
        suggestions.append("没有发现依赖清单，发布前建议补一份 requirements.txt 或 package.json。")
    elif not vulns:
        suggestions.append("已知漏洞库中未发现匹配项，发布前保持依赖为最新稳定版即可。")
    else:
        fixed = [v for v in vulns if v.get("fixed_version")]
        if fixed:
            top = ", ".join(f"{v['package']}→{v['fixed_version']}" for v in fixed[:3])
            suggestions.append(f"优先升级带修复版本的依赖：{top}。")
        if counts["critical"] or counts["high"]:
            suggestions.append("存在高危漏洞，修复前不建议发布或对外提供下载。")
    if deps and not error:
        unpinned = [d["name"] for d in deps if not d["pinned"]][:5]
        if unpinned:
            suggestions.append(f"以下依赖未锁定版本（{', '.join(unpinned)}），建议固定版本让构建可复现。")
    advice, source = _scan_advice(counts, vulns, error, model_callback)
    return {
        "repository": parsed["repository"],
        "dependency_files": parsed["files"],
        "dependencies": deps,
        "dependency_summary": parsed["summary"],
        "vulnerabilities": vulns,
        "counts": counts,
        "queried": not error and bool(deps),
        "error": error,
        "suggestions": suggestions,
        "advice": advice,
        "source": source,
        "database": "OSV.dev",
    }


def _scan_advice(counts: dict[str, Any], vulns: list[dict[str, Any]], error: str | None, model_callback: Callable[[str], str] | None) -> tuple[str, str]:
    if error:
        template = "依赖清单已解析，但漏洞库暂时连不上；这不阻塞本地检查，联网后补扫一次即可。"
    elif counts["total"]:
        template = f"共发现 {counts['total']} 个已知漏洞，涉及 {counts['packages_affected']} 个依赖；先升级有修复版本的包，再考虑固定所有依赖版本。"
    else:
        template = "依赖未发现已知漏洞，供应链状态良好；建议保持锁版本并在每次发布前复扫。"
    if model_callback is None:
        return template, "osv+local" if not error else "local-parse"
    prompt = (
        "你是开源供应链安全助手。根据扫描结果，用 3 句以内中文给出修复优先级建议，纯文本、不用 Markdown。"
        f"漏洞总数 {counts['total']}（严重 {counts['critical']}、高危 {counts['high']}、中危 {counts['medium']}），"
        f"涉及 {counts['packages_affected']} 个依赖包。"
        + ("前三个漏洞：" + "；".join(f"{v['package']} {v['version']} {v['id']} 修复版本 {v.get('fixed_version') or '未知'}" for v in vulns[:3]) if vulns else "")
    )
    try:
        text = (model_callback(prompt) or "").strip()
    except Exception:  # noqa: BLE001 - model polish is best-effort
        text = ""
    if text:
        return text, "model+osv"
    return template, "osv+local" if not error else "local-parse"


def generate_sbom(repo: str | Path, *, project_name: str | None = None) -> dict[str, Any]:
    """Generate a CycloneDX 1.5 SBOM (JSON) from parsed dependency files."""

    parsed = parse_dependencies(repo)
    root = Path(parsed["repository"])
    name = project_name or root.name or "project"
    components = [
        {
            "type": "library",
            "bom-ref": dep["purl"],
            "name": dep["name"],
            "version": dep.get("version") or "unspecified",
            "purl": dep["purl"],
            "scope": dep["scope"],
            "properties": [{"name": "osg:source_file", "value": dep["source_file"]},
                           {"name": "osg:pinned", "value": "true" if dep["pinned"] else "false"}],
        }
        for dep in parsed["dependencies"]
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tools": [{"vendor": "OpenSourceGuard", "name": "supplychain", "version": TOOL_VERSION}],
            "component": {"type": "application", "bom-ref": f"pkg:generic/{name}", "name": name},
        },
        "components": components,
    }


def _has_ci(root: Path) -> bool:
    workflows = root / ".github" / "workflows"
    if workflows.is_dir() and any(workflows.glob("*.y*ml")):
        return True
    return any((root / name).exists() for name in (".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml", "azure-pipelines.yml"))


def baseline_check(repo: str | Path, *, vulnerabilities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Lightweight local scorecard; each check maps to an OSPS Baseline domain."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    names = {p.name.lower() for p in root.iterdir()}
    parsed = parse_dependencies(root)

    checks: list[dict[str, Any]] = []

    def add(check_id: str, title: str, domain: str, passed: bool | None, detail: str, suggestion: str = "") -> None:
        checks.append({"id": check_id, "title": title, "domain": domain,
                       "status": "skipped" if passed is None else ("passed" if passed else "failed"),
                       "detail": detail, "suggestion": suggestion})

    license_files = [n for n in names if n.startswith("license")]
    add("license-file", "许可证文件", DOMAIN_LEGAL, bool(license_files),
        f"发现 {license_files[0]}" if license_files else "没有发现 LICENSE 文件",
        "补充 OSI 批准的许可证全文，否则他人无法合法使用。")
    add("security-policy", "安全披露政策", DOMAIN_VM, "security.md" in names,
        "发现 SECURITY.md" if "security.md" in names else "没有 SECURITY.md 漏洞报告入口",
        "新增 SECURITY.md，说明漏洞报告渠道和响应时限。")
    if vulnerabilities is None:
        add("vulnerability-free", "已知漏洞", DOMAIN_VM, None, "未执行漏洞扫描，跳过。")
    else:
        bad = [v for v in vulnerabilities if v["severity"] in ("critical", "high")]
        add("vulnerability-free", "已知漏洞", DOMAIN_VM, not bad,
            "扫描通过，无高危漏洞。" if not bad else f"存在 {len(bad)} 个高危/严重漏洞。",
            "先升级带修复版本的依赖再发布。" if bad else "")
    ratio = parsed["summary"]["pinned_ratio"]
    total = parsed["summary"]["total"]
    add("dependency-pinning", "依赖版本锁定", DOMAIN_BR,
        total == 0 or ratio >= 0.8,
        f"{total} 个依赖中 {parsed['summary']['pinned']} 个锁定版本（{int(ratio * 100)}%）" if total else "无外部依赖",
        "用 == 或 lock 文件固定版本，保证构建可复现。" if total and ratio < 0.8 else "")
    add("sbom-ready", "SBOM 可生成", DOMAIN_BR, bool(parsed["files"]),
        f"发现依赖清单：{', '.join(parsed['files'])}" if parsed["files"] else "没有依赖清单，无法生成 SBOM",
        "补一份 requirements.txt / pyproject.toml / package.json，即可一键生成 CycloneDX SBOM。")
    add("ci-config", "CI 自动化", DOMAIN_BR, _has_ci(root),
        "发现 CI 工作流配置" if _has_ci(root) else "没有发现 CI 配置",
        "加一个最简 CI：安装依赖并运行测试，防止回归。")
    has_tests = any(n in names for n in ("tests", "test")) or (root / "pytest.ini").exists()
    add("tests-present", "自动化测试", DOMAIN_QUALITY, has_tests,
        "发现测试目录" if has_tests else "没有发现测试目录",
        "至少为核心逻辑补一组可运行测试。")
    secrets = [f for f in scan_repository(root)["findings"] if f["severity"] == "high"]
    add("secrets-clean", "敏感信息", DOMAIN_SA, not secrets,
        "未发现疑似密钥。" if not secrets else f"发现 {len(secrets)} 处疑似密钥。",
        "移除并轮换疑似密钥，把示例值挪进 .env.example。" if secrets else "")
    add("readme-present", "项目文档", DOMAIN_DOCS, any(n in names for n in ("readme.md", "readme.rst", "readme.txt")),
        "发现 README" if any(n in names for n in ("readme.md", "readme.rst", "readme.txt")) else "没有 README",
        "README 至少包含用途、安装、用法和许可证说明。")
    gov = [n for n in ("contributing.md", "code_of_conduct.md") if n in names]
    add("governance", "治理文件", DOMAIN_GOV, bool(gov),
        f"发现 {', '.join(gov)}" if gov else "没有 CONTRIBUTING / CODE_OF_CONDUCT",
        "补充贡献指南与行为准则，降低协作门槛。")

    scored = [c for c in checks if c["status"] != "skipped"]
    passed_count = sum(1 for c in scored if c["status"] == "passed")
    score = round(100 * passed_count / len(scored)) if scored else 0
    domains: dict[str, dict[str, int]] = {}
    for check in scored:
        bucket = domains.setdefault(check["domain"], {"passed": 0, "total": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if check["status"] == "passed" else 0
    return {
        "repository": str(root),
        "score": score,
        "passed": passed_count,
        "total": len(scored),
        "checks": checks,
        "domains": domains,
        "baseline": "OSPS Baseline 域映射（OpenSSF 开源项目安全基线）",
        "summary": f"安全基线 {passed_count}/{len(scored)} 项通过（{score} 分）。",
    }


def supplychain_scan(repo: str | Path, *, fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None, getter: Callable[[str], dict[str, Any]] | None = None, model_callback: Callable[[str], str] | None = None, timeout: float = 8, include_sbom: bool = True) -> dict[str, Any]:
    """One-call aggregate: vulnerabilities + SBOM + baseline scorecard."""

    vulns = scan_vulnerabilities(repo, fetcher=fetcher, getter=getter, model_callback=model_callback, timeout=timeout)
    result: dict[str, Any] = {
        "repository": vulns["repository"],
        "dependency_files": vulns["dependency_files"],
        "dependency_summary": vulns["dependency_summary"],
        "vulnerabilities": vulns["vulnerabilities"],
        "counts": vulns["counts"],
        "queried": vulns["queried"],
        "error": vulns["error"],
        "suggestions": vulns["suggestions"],
        "advice": vulns["advice"],
        "source": vulns["source"],
        "database": vulns["database"],
        "baseline": baseline_check(repo, vulnerabilities=vulns["vulnerabilities"] if vulns["queried"] else None),
    }
    if include_sbom:
        result["sbom"] = generate_sbom(repo)
    return result
