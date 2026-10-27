"""Dependency license compatibility audit for open-source readiness."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

PERMISSIVE = {"MIT", "BSD", "Apache-2.0", "ISC", "PSF", "MPL-2.0"}
STRONG_COPYLEFT = {"GPL-2.0", "GPL-3.0", "AGPL-3.0"}
WEAK_COPYLEFT = {"LGPL-2.1", "LGPL-3.0"}

KNOWN_LICENSES: dict[str, str] = {
    # Python
    "requests": "Apache-2.0", "urllib3": "MIT", "flask": "BSD", "django": "BSD",
    "fastapi": "MIT", "uvicorn": "BSD", "gunicorn": "MIT", "starlette": "BSD",
    "pydantic": "MIT", "sqlalchemy": "MIT", "jinja2": "BSD", "click": "BSD",
    "pytest": "MIT", "numpy": "BSD", "pandas": "BSD", "scipy": "BSD",
    "scikit-learn": "BSD", "matplotlib": "PSF", "pillow": "MIT", "pyyaml": "MIT",
    "six": "MIT", "wheel": "MIT", "setuptools": "MIT", "celery": "BSD",
    "boto3": "Apache-2.0", "cryptography": "Apache-2.0", "httpx": "BSD",
    "aiohttp": "Apache-2.0", "tornado": "Apache-2.0", "redis": "MIT",
    "openai": "Apache-2.0", "tqdm": "MPL-2.0", "rich": "MIT",
    "paramiko": "LGPL-2.1", "psycopg2": "LGPL-3.0", "mysql-connector-python": "GPL-2.0",
    "pyqt5": "GPL-3.0", "pyside6": "LGPL-3.0", "chardet": "LGPL-2.1",
    # Node
    "react": "MIT", "react-dom": "MIT", "vue": "MIT", "express": "MIT",
    "lodash": "MIT", "axios": "MIT", "webpack": "MIT", "eslint": "MIT",
    "typescript": "Apache-2.0", "next": "MIT", "jquery": "MIT", "bootstrap": "MIT",
    "tailwindcss": "MIT", "vite": "MIT", "jest": "MIT", "mocha": "MIT",
    "moment": "MIT", "dayjs": "MIT", "echarts": "Apache-2.0", "d3": "ISC",
    "three": "MIT", "chalk": "MIT", "commander": "MIT", "express-session": "MIT",
}


def _project_license(root: Path) -> str:
    candidates = list(root.glob("LICENSE*")) + list(root.glob("license*"))
    if not candidates:
        return "未识别"
    try:
        text = candidates[0].read_text(encoding="utf-8", errors="replace")[:4000].lower()
    except OSError:
        return "未识别"
    if "gnu affero" in text:
        return "AGPL-3.0"
    if "gnu lesser" in text:
        return "LGPL-2.1"
    if "gnu general public license" in text:
        return "GPL-3.0" if "version 3" in text else "GPL-2.0"
    if "apache license" in text:
        return "Apache-2.0"
    if "mit license" in text or "permission is hereby granted" in text:
        return "MIT"
    if "redistribution and use" in text:
        return "BSD"
    return "未识别"


def _python_deps(root: Path) -> list[str]:
    deps: list[str] = []
    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "-")):
                name = re.split(r"[<>=!~;\[]", line, 1)[0].strip().lower().replace("_", "-")
                if name:
                    deps.append(name)
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8-sig", errors="replace")
        for match in re.findall(r"^\s*[\"']([A-Za-z0-9_\-]+)[^\"']*[\"']\s*,?\s*$", text, re.M):
            deps.append(match.lower().replace("_", "-"))
    return sorted(set(deps))


def _node_deps(root: Path) -> list[str]:
    pkg = root / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8-sig", errors="replace"))
    except json.JSONDecodeError:
        return []
    deps = list((data.get("dependencies") or {}).keys()) + list((data.get("devDependencies") or {}).keys())
    return sorted({d.strip().lower().lstrip("@").split("/")[0] for d in deps if d.strip()})


def audit_licenses(repo: str | Path) -> dict[str, Any]:
    """Check dependency licenses against the project license for copyleft conflicts."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    project_license = _project_license(root)
    entries: list[dict[str, Any]] = []
    for name in _python_deps(root):
        entries.append({"name": name, "ecosystem": "Python", "license": KNOWN_LICENSES.get(name, "未知")})
    for name in _node_deps(root):
        entries.append({"name": name, "ecosystem": "Node.js", "license": KNOWN_LICENSES.get(name, "未知")})

    project_is_copyleft = project_license in STRONG_COPYLEFT or project_license in WEAK_COPYLEFT
    conflicts: list[str] = []
    for entry in entries:
        lic = entry["license"]
        if lic == "未知":
            entry["status"] = "unknown"
            entry["note"] = "未收录该包的许可证，需要人工到包仓库确认。"
        elif lic in STRONG_COPYLEFT and not project_is_copyleft:
            entry["status"] = "conflict"
            entry["note"] = f"{lic} 具有传染性：与 {project_license} 主项目组合分发时，整体可能需要按 {lic} 开源。"
            conflicts.append(f"{entry['name']}（{lic}）与主项目 {project_license} 存在传染冲突")
        elif lic in STRONG_COPYLEFT and project_is_copyleft:
            entry["status"] = "attention"
            entry["note"] = f"{lic} 依赖要求整个衍生作品按相同条款分发，确认与主项目 {project_license} 版本兼容。"
        elif lic in WEAK_COPYLEFT:
            entry["status"] = "attention"
            entry["note"] = f"{lic} 要求修改该库本身时开源；建议以动态链接方式使用并保留声明。"
        elif project_license == "未识别":
            entry["status"] = "attention"
            entry["note"] = "主项目许可证未确认，无法判断依赖组合是否合规。"
        else:
            entry["status"] = "compatible"
            entry["note"] = "宽松许可证，与主项目兼容，保留版权和许可声明即可。"

    summary = {
        "total": len(entries),
        "compatible": sum(1 for e in entries if e["status"] == "compatible"),
        "attention": sum(1 for e in entries if e["status"] == "attention"),
        "conflict": sum(1 for e in entries if e["status"] == "conflict"),
        "unknown": sum(1 for e in entries if e["status"] == "unknown"),
    }
    notes: list[str] = []
    if not entries:
        notes.append("没有发现 requirements.txt、pyproject.toml 依赖声明或 package.json，无需做依赖合规检查。")
    if project_license == "未识别":
        notes.append("主项目许可证未识别：在确认许可证前，所有依赖组合的合规结论都只能作为参考。")
    risk = "high" if summary["conflict"] else ("medium" if summary["attention"] or summary["unknown"] else "low")
    return {"repository": str(root), "project_license": project_license, "dependencies": entries, "summary": summary, "conflicts": conflicts, "risk_level": risk, "notes": notes}
