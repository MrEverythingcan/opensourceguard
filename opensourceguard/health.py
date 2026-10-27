"""Open-source health scoring across five beginner-friendly dimensions."""
from __future__ import annotations

import datetime as _dt
import math
from html import escape
from pathlib import Path
from typing import Any

from .security import scan_repository


def _names(root: Path) -> set[str]:
    return {p.name.lower() for p in root.iterdir()} if root.is_dir() else set()


def _read(root: Path, name: str, limit: int = 12000) -> str:
    try:
        return (root / name).read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _dim(key: str, label: str, score: int, findings: list[str], suggestions: list[str]) -> dict[str, Any]:
    score = max(0, min(100, int(score)))
    return {"key": key, "label": label, "score": score, "level": "优秀" if score >= 85 else ("良好" if score >= 70 else ("待改进" if score >= 45 else "缺失")), "findings": findings, "suggestions": suggestions}


def score_health(repo: str | Path) -> dict[str, Any]:
    """Score documentation, license, testing, security and community readiness."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    names = _names(root)
    github = {p.name.lower() for p in (root / ".github").iterdir()} if (root / ".github").is_dir() else set()

    readme_name = next((n for n in ("readme.md", "readme.rst", "readme.txt") if n in names), "")
    readme = _read(root, readme_name) if readme_name else ""
    headings = [line for line in readme.splitlines() if line.lstrip().startswith("#")]
    doc_findings: list[str] = []
    doc_suggestions: list[str] = []
    doc = 0
    if readme:
        doc += 45
        doc_findings.append(f"发现 {readme_name}，共 {len(readme)} 字符")
        doc += min(25, len(readme) // 400)
        if len(headings) >= 3:
            doc += 20
            doc_findings.append(f"包含 {len(headings)} 个章节标题，结构清晰")
        else:
            doc_suggestions.append("为 README 增加安装、用法、贡献等章节标题")
        if any(k in readme.lower() for k in ("install", "安装", "usage", "用法", "快速开始")):
            doc += 10
            doc_findings.append("包含安装或使用说明")
        else:
            doc_suggestions.append("补充安装步骤和使用示例")
    else:
        doc_findings.append("没有发现 README")
        doc_suggestions.append("新增 README，说明用途、安装、运行和贡献方式")

    license_files = [n for n in names if n.startswith("license")]
    lic_findings: list[str] = []
    lic_suggestions: list[str] = []
    lic = 0
    license_name = "未识别"
    if license_files:
        text = _read(root, license_files[0], 4000).lower()
        lic += 60
        lic_findings.append(f"发现许可证文件 {license_files[0]}")
        for name, keywords in [("MIT", ["mit license"]), ("Apache-2.0", ["apache license", "version 2.0"]), ("GPL", ["gnu general public license"]), ("BSD", ["bsd license", "redistribution and use"])]:
            if any(k in text for k in keywords):
                license_name = name
                lic += 40
                lic_findings.append(f"识别为 {name} 许可证")
                break
        else:
            lic_suggestions.append("许可证文本无法自动识别，建议使用标准 SPDX 许可证全文")
    else:
        lic_findings.append("没有发现许可证文件")
        lic_suggestions.append("选择 MIT、Apache-2.0 等许可证，未确认前不要商用或再分发")

    test_findings: list[str] = []
    test_suggestions: list[str] = []
    test = 0
    if any(n in names for n in ("tests", "test", "pytest.ini", "tox.ini")):
        test += 55
        test_findings.append("发现测试目录或测试配置")
    else:
        test_suggestions.append("补充最小可运行测试，发布后才能稳定回归")
    if any(n.startswith("workflows") for n in github) or ".gitlab-ci.yml" in names:
        test += 45
        test_findings.append("发现 CI 工作流配置")
    elif test:
        test += 15
        test_suggestions.append("建议补充 CI，让测试在提交后自动运行")

    scan = scan_repository(root)
    sec_findings: list[str] = []
    sec_suggestions: list[str] = []
    sec = 100
    high = [f for f in scan["findings"] if f["severity"] == "high" and f["type"] != "license-missing"]
    medium = [f for f in scan["findings"] if f["severity"] == "medium"]
    if high:
        sec -= 55
        sec_findings.append(f"发现 {len(high)} 个高风险项（疑似密钥等）")
        sec_suggestions.append("先移除并轮换疑似密钥，再继续开源准备")
    if medium:
        sec -= 15
        sec_findings.append(f"发现 {len(medium)} 个中风险项")
    if ".gitignore" in names:
        sec_findings.append("发现 .gitignore")
    else:
        sec -= 15
        sec_suggestions.append("新增 .gitignore，排除 .env、缓存和构建产物")
    if "security.md" in names:
        sec_findings.append("发现 SECURITY.md 漏洞报告入口")
    else:
        sec -= 15
        sec_suggestions.append("新增 SECURITY.md，提供安全问题报告渠道")

    com_findings: list[str] = []
    com_suggestions: list[str] = []
    com = 0
    if "contributing.md" in names:
        com += 35
        com_findings.append("发现 CONTRIBUTING 贡献指南")
    else:
        com_suggestions.append("新增 CONTRIBUTING.md，说明如何提交 Issue 和 PR")
    if "code_of_conduct.md" in names:
        com += 25
        com_findings.append("发现行为准则")
    else:
        com_suggestions.append("考虑补充 CODE_OF_CONDUCT.md")
    if any("issue_template" in n for n in github) or any("pull_request_template" in n for n in github):
        com += 25
        com_findings.append("发现 Issue/PR 模板")
    else:
        com_suggestions.append("补充 Issue 和 PR 模板，降低协作门槛")
    if ".env.example" in names or ".env.template" in names:
        com += 15
        com_findings.append("发现环境变量模板")
    else:
        com_suggestions.append("新增 .env.example，避免贡献者提交真实密钥")

    dimensions = [
        _dim("documentation", "文档", doc, doc_findings, doc_suggestions),
        _dim("license", "许可证", lic, lic_findings, lic_suggestions),
        _dim("testing", "测试与CI", test, test_findings, test_suggestions),
        _dim("security", "安全", sec, sec_findings, sec_suggestions),
        _dim("community", "社区规范", com, com_findings, com_suggestions),
    ]
    overall = round(sum(d["score"] for d in dimensions) / len(dimensions))
    grade = "优秀" if overall >= 85 else ("良好" if overall >= 70 else ("待改进" if overall >= 45 else "需要补齐"))
    weakest = min(dimensions, key=lambda d: d["score"])
    summary = f"综合健康度 {overall} 分（{grade}）。当前最薄弱环节是「{weakest['label']}」，优先处理它能最快提升开源就绪度。"
    return {"repository": str(root), "overall": overall, "grade": grade, "license": license_name, "dimensions": dimensions, "summary": summary}


def build_readiness_report(repo: str | Path, *, scan_online: bool = True) -> dict[str, Any]:
    """Aggregate health, license, README, security and supply-chain results."""

    from .license_audit import audit_licenses
    from .readme_review import review_readme
    from .supplychain import supplychain_scan

    def offline_fetcher(_payload):
        raise OSError("offline report export")

    health = score_health(repo)
    supply = supplychain_scan(repo, timeout=5) if scan_online else supplychain_scan(repo, fetcher=offline_fetcher)
    return {
        "repository": health["repository"],
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": "0.6.0",
        "health": health,
        "license_audit": audit_licenses(repo),
        "readme": review_readme(repo),
        "security": scan_repository(repo),
        "supplychain": supply,
    }


def _radar_svg(dimensions: list[dict[str, Any]], size: int = 300) -> str:
    cx = cy = size / 2
    radius = size / 2 - 34
    n = len(dimensions)

    def point(i: int, ratio: float) -> tuple[float, float]:
        angle = -math.pi / 2 + i * 2 * math.pi / n
        return cx + radius * ratio * math.cos(angle), cy + radius * ratio * math.sin(angle)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" role="img" aria-label="开源健康度雷达图">']
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, ring) for i in range(n)))
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#3d5470" stroke-width="1"/>')
    for i in range(n):
        x, y = point(i, 1.0)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#3d5470" stroke-width="1"/>')
    value_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (point(i, max(d["score"], 4) / 100) for i, d in enumerate(dimensions)))
    parts.append(f'<polygon points="{value_pts}" fill="rgba(109,231,255,.25)" stroke="#6de7ff" stroke-width="2"/>')
    for i, d in enumerate(dimensions):
        x, y = point(i, max(d["score"], 4) / 100)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#6de7ff"/>')
        lx, ly = point(i, 1.22)
        anchor = "middle" if abs(lx - cx) < 12 else ("start" if lx > cx else "end")
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#c7d6ea" font-size="12" text-anchor="{anchor}">{escape(d["label"])} {d["score"]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def render_report_html(report: dict[str, Any]) -> str:
    """Render the readiness report as a self-contained HTML page."""

    health = report["health"]
    audit = report["license_audit"]
    readme = report["readme"]
    security = report["security"]

    def esc(value: Any) -> str:
        return escape(str(value))

    dim_cards = "".join(
        f'<div class="card"><div class="card-head"><strong>{esc(d["label"])}</strong><span class="score {"good" if d["score"] >= 85 else ("warn" if d["score"] >= 45 else "bad")}">{d["score"]}</span></div>'
        + (f'<ul>{"".join(f"<li>{esc(f)}</li>" for f in d["findings"])}</ul>' if d["findings"] else "")
        + (f'<ul class="todo">{"".join(f"<li>{esc(s)}</li>" for s in d["suggestions"])}</ul>' if d["suggestions"] else "")
        + "</div>"
        for d in health["dimensions"]
    )
    status_label = {"compatible": ("兼容", "good"), "attention": ("注意", "warn"), "conflict": ("冲突", "bad"), "unknown": ("待确认", "warn")}
    dep_rows = "".join(
        f'<tr><td>{esc(d["name"])}</td><td>{esc(d["ecosystem"])}</td><td>{esc(d["license"])}</td>'
        f'<td><span class="pill {status_label.get(d["status"], ("", "warn"))[1]}">{status_label.get(d["status"], (d["status"], ""))[0]}</span></td><td class="muted">{esc(d["note"])}</td></tr>'
        for d in audit["dependencies"]
    ) or '<tr><td colspan="5" class="muted">没有发现依赖清单文件</td></tr>'
    readme_items = "".join(
        f'<span class="pill {"good" if s["present"] else "warn"}">{"✓" if s["present"] else "○"} {esc(s["label"])}</span>'
        for s in readme["sections"]
    )
    conflict_list = "".join(f"<li>{esc(c)}</li>" for c in audit["conflicts"])
    findings_list = "".join(f'<li>{esc(f["path"])}：{esc(f["message"])}</li>' for f in security["findings"]) or "<li>暂未发现明显风险项</li>"
    supply = report.get("supplychain") or {}
    counts = supply.get("counts") or {}
    dep_summary = supply.get("dependency_summary") or {}
    baseline = supply.get("baseline") or {}
    sev_label = {"critical": ("严重", "bad"), "high": ("高危", "bad"), "medium": ("中危", "warn"), "low": ("低危", "warn"), "unknown": ("待定", "warn")}
    vuln_rows = "".join(
        f'<tr><td>{esc(v["package"])}@{esc(v.get("version") or "?")}</td><td><a href="{esc(v["link"])}" style="color:#6de7ff">{esc(v["id"])}</a></td>'
        f'<td><span class="pill {sev_label.get(v["severity"], ("", "warn"))[1]}">{sev_label.get(v["severity"], (v["severity"], ""))[0]}</span></td>'
        f'<td>{esc(v.get("fixed_version") or "暂无")}</td><td class="muted">{esc(v["summary"][:80])}</td></tr>'
        for v in (supply.get("vulnerabilities") or [])[:10]
    ) or ('<tr><td colspan="5" class="muted">未匹配到已知漏洞</td></tr>' if supply.get("queried") else '<tr><td colspan="5" class="muted">离线状态，未查询漏洞库</td></tr>')
    base_rows = "".join(
        f'<tr><td>{"✓" if c["status"] == "passed" else ("—" if c["status"] == "skipped" else "✗")}</td><td>{esc(c["title"])}</td>'
        f'<td class="muted">{esc(c["domain"])}</td><td class="muted">{esc(c["detail"])}</td></tr>'
        for c in (baseline.get("checks") or [])
    )
    summary = audit["summary"]
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>开源就绪度报告 · {esc(Path(health["repository"]).name)}</title>
<style>
body{{margin:0;padding:40px 20px;background:#0d1b2d;color:#eff6ff;font:14px/1.7 system-ui,"Segoe UI",sans-serif}}
.report{{max-width:920px;margin:auto;background:#13223a;border:1px solid rgba(255,255,255,.13);border-radius:20px;padding:36px 40px}}
h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:17px;margin:30px 0 12px;border-left:3px solid #6de7ff;padding-left:10px}}
.muted{{color:#9cafc7}}.overall{{display:flex;gap:24px;align-items:center;flex-wrap:wrap;margin:18px 0}}
.big{{font-size:52px;font-weight:800;color:#6de7ff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
.card{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:14px 16px}}
.card-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}}
.score{{font-size:20px;font-weight:800}}.good{{color:#66e0ad}}.warn{{color:#ffd082}}.bad{{color:#ff8f9d}}
ul{{margin:6px 0;padding-left:18px}}ul.todo li{{color:#ffd082}}
.pill{{display:inline-block;padding:3px 9px;border-radius:99px;background:rgba(255,255,255,.08);margin:3px 4px 3px 0;font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}td,th{{border-bottom:1px solid rgba(255,255,255,.08);padding:8px 10px;text-align:left;vertical-align:top}}
footer{{margin-top:34px;color:#7589a3;font-size:12px;text-align:center}}
@media print{{body{{background:#fff}}.report{{border:none}}}}
</style></head><body><div class="report">
<h1>开源就绪度报告</h1>
<p class="muted">仓库：{esc(health["repository"])} · 生成时间：{esc(report["generated_at"])} · OpenSourceGuard {esc(report["version"])}</p>
<div class="overall"><div class="big">{health["overall"]}</div><div><strong>综合评级：{esc(health["grade"])}</strong><p class="muted">{esc(health["summary"])}</p><p class="muted">项目许可证：{esc(health["license"])}</p></div><div>{_radar_svg(health["dimensions"])}</div></div>
<h2>五维健康度详情</h2><div class="grid">{dim_cards}</div>
<h2>许可证合规分析</h2>
<p>主项目许可证：<strong>{esc(audit["project_license"])}</strong> · 依赖共 {summary["total"]} 个：兼容 {summary["compatible"]} · 注意 {summary["attention"]} · 冲突 {summary["conflict"]} · 待确认 {summary["unknown"]}</p>
{f'<ul class="todo"><li>{conflict_list}</li></ul>' if conflict_list else ""}
<table><tr><th>依赖包</th><th>生态</th><th>许可证</th><th>状态</th><th>说明</th></tr>{dep_rows}</table>
<h2>README 质量评估</h2>
<p>结构得分：<strong>{readme["score"]}</strong>（{esc(readme["level"])}）</p><div>{readme_items}</div>
{f'<p class="muted">缺失：{esc("、".join(readme["missing"]))}</p>' if readme["missing"] else ""}
<h2>安全扫描摘要</h2>
<p>扫描文件 {security["files_scanned"]} 个，风险级别：<strong>{esc(security["risk_level"])}</strong></p><ul>{findings_list}</ul>
<h2>供应链安全（OSV 漏洞 · SBOM · OSPS Baseline）</h2>
<p>依赖 {dep_summary.get("total", 0)} 个（锁定版本 {dep_summary.get("pinned", 0)} 个）· 已知漏洞 <strong>{counts.get("total", 0)}</strong>（严重 {counts.get("critical", 0)} · 高危 {counts.get("high", 0)} · 中危 {counts.get("medium", 0)}）· SBOM 已随报告生成（CycloneDX 1.5，组件 {dep_summary.get("total", 0)} 个）</p>
<p class="muted">{esc(supply.get("advice") or "")}</p>
<table><tr><th>依赖</th><th>漏洞</th><th>严重度</th><th>修复版本</th><th>摘要</th></tr>{vuln_rows}</table>
<p>安全基线：<strong>{baseline.get("passed", 0)}/{baseline.get("total", 0)}</strong> 项通过（{baseline.get("score", 0)} 分）· 对照 {esc(baseline.get("baseline") or "OSPS Baseline")}</p>
<table><tr><th></th><th>检查项</th><th>基线域</th><th>说明</th></tr>{base_rows}</table>
<footer>由 OpenSourceGuard 本地生成 · 报告不包含任何密钥或隐私数据 · 许可证结论仅供参考，不构成法律意见</footer>
</div></body></html>"""
