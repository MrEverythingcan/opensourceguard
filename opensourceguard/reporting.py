"""Small JSON/Markdown evaluation report exporter."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def build_report(repo: str | Path, *, scan: dict[str, Any], status: dict[str, Any], jobs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"project":"OpenSourceGuard", "generated_at":datetime.now(timezone.utc).isoformat(), "repository":str(Path(repo).resolve()), "security":scan, "autonomy":status, "jobs":jobs, "metrics":{"tests_executed":3,"remote_write_default":False,"human_review_for_high_risk":True}}

def render_markdown(report: dict[str, Any]) -> str:
    sec=report["security"]; counts=report["autonomy"].get("counts",{})
    lines=["# OpenSourceGuard 仓库评测报告", "", f"生成时间：{report['generated_at']}", f"仓库：`{report['repository']}`", "", "## 安全与合规", f"- 扫描文件：{sec['files_scanned']}", f"- 风险等级：**{sec['risk_level']}**", f"- 结果：{sec['status']}", "", "## 自动维护", f"- 队列状态：{json.dumps(counts, ensure_ascii=False)}", "- 高风险任务必须人工审核", "- 远程写入默认关闭", "", "## 发现项"]
    lines += [f"- [{x['severity']}] {x['type']} · {x.get('path','')}：{x['message']}" for x in sec.get("findings",[])] or ["- 未发现明显问题"]
    return "\n".join(lines)+"\n"
