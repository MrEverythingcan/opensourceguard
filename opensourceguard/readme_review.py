"""README quality review with optional model-powered rewrite."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

_SECTIONS = [
    {"key": "title", "label": "项目标题", "weight": 10, "hint": "用一行一级标题说明项目名称"},
    {"key": "intro", "label": "项目简介", "weight": 20, "hint": "开头用两三句话说清项目解决什么问题、适合谁"},
    {"key": "badges", "label": "状态徽章", "weight": 5, "hint": "可添加许可证、版本、CI 状态徽章提升可信度"},
    {"key": "install", "label": "安装说明", "weight": 20, "hint": "给出可复制的依赖安装命令"},
    {"key": "usage", "label": "使用方法", "weight": 20, "hint": "给出最小可运行示例或快速开始步骤"},
    {"key": "license", "label": "许可证声明", "weight": 10, "hint": "注明许可证名称并链接到 LICENSE 文件"},
    {"key": "contributing", "label": "贡献指南", "weight": 10, "hint": "说明如何提交 Issue 和 Pull Request"},
    {"key": "testing", "label": "测试说明", "weight": 5, "hint": "说明如何运行测试，方便贡献者验证修改"},
]

REWRITE_PROMPT = "你是开源项目文档助手。根据仓库信息和现有 README 摘要，输出一份结构完整、面向初学者的中文 README 改进版（Markdown 格式，包含项目标题、简介、安装、使用方法、许可证、贡献指南等章节）。只输出 README 正文，不要输出额外解释。"


def _check(name: str, readme: str) -> dict[str, bool]:
    lower = readme.lower()
    lines = [l.strip() for l in readme.splitlines() if l.strip()]
    first_heading = next((l for l in lines if l.startswith("#")), "")
    intro_paras = [l for l in lines if not l.startswith(("#", "!", "[", "<", "```", "-")) and len(l) >= 30]
    return {
        "title": bool(first_heading),
        "intro": bool(intro_paras),
        "badges": any(k in lower for k in ("shields.io", "badge", "![")),
        "install": any(k in lower for k in ("install", "安装", "pip install", "npm install", "快速开始", "quickstart")),
        "usage": any(k in lower for k in ("usage", "用法", "使用", "示例", "example", "getting started")),
        "license": any(k in lower for k in ("license", "许可证", "licence")),
        "contributing": any(k in lower for k in ("contributing", "贡献", "pull request", "pr 流程")),
        "testing": any(k in lower for k in ("test", "测试", "pytest", "npm test")),
    }


def review_readme(repo: str | Path, rewrite: bool = False, model_callback: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Score README structure; optionally generate an improved version via model."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    readme = ""
    for name in ("README.md", "README.rst", "README.txt", "readme.md"):
        candidate = root / name
        if candidate.exists():
            readme = candidate.read_text(encoding="utf-8", errors="replace")[:20000]
            break
    if not readme:
        checks = {s["key"]: False for s in _SECTIONS}
    else:
        checks = _check(root.name, readme)
    sections = [{**s, "present": checks[s["key"]]} for s in _SECTIONS]
    score = sum(s["weight"] for s in sections if s["present"])
    missing = [s["label"] for s in sections if not s["present"]]
    result: dict[str, Any] = {
        "repository": str(root),
        "score": score,
        "level": "优秀" if score >= 85 else ("良好" if score >= 70 else ("待改进" if score >= 45 else "缺失")),
        "sections": sections,
        "missing": missing,
        "source": "local-analysis",
    }
    if rewrite:
        if not model_callback:
            raise ValueError("模型未配置，暂时无法生成改进版 README")
        prompt = json.dumps({"repository": root.name, "missing_sections": missing, "current_readme_excerpt": readme[:4000]}, ensure_ascii=False)
        result["improved"] = model_callback(prompt)
        result["source"] = "model+local-context"
    return result
