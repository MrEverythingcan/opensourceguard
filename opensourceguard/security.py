"""Dependency-free repository security, license and compliance checks."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any

SECRET_PATTERNS = {
    "private-key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "api-key": re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
    "password": re.compile(r"(?i)(password|passwd)\s*[:=]\s*['\"][^'\"]{6,}"),
}
IGNORE = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}

def scan_repository(repo: str | Path, max_files: int = 1500) -> dict[str, Any]:
    root = Path(repo).resolve()
    if not root.is_dir(): raise ValueError("repository directory does not exist")
    findings: list[dict[str, Any]] = []; files = 0
    for path in root.rglob("*"):
        if not path.is_file() or any(part in IGNORE for part in path.parts) or files >= max_files: continue
        files += 1
        try: text = path.read_text(encoding="utf-8", errors="ignore")[:400_000]
        except OSError: continue
        rel = str(path.relative_to(root))
        for name, pattern in SECRET_PATTERNS.items():
            match = pattern.search(text)
            if match and rel not in {".env.example", ".env.template"}:
                findings.append({"type": name, "severity": "high", "path": rel, "message": "可能包含敏感凭据，请移除并轮换。"})
        if path.name.lower().startswith("license"):
            lower = text.lower()
            if not any(x in lower for x in ("mit license", "apache license", "gnu general public license", "bsd license")):
                findings.append({"type": "license", "severity": "medium", "path": rel, "message": "许可证文本无法自动识别，商用前需要人工确认。"})
    license_files = list(root.glob("LICENSE*")) + list(root.glob("license*"))
    dependency_files = [p.name for p in (root / "requirements.txt", root / "pyproject.toml", root / "package.json", root / "poetry.lock", root / "package-lock.json") if p.exists()]
    if not license_files: findings.append({"type":"license-missing","severity":"high","path":"","message":"未发现许可证文件。"})
    return {"repository": str(root), "files_scanned": files, "dependency_files": dependency_files, "findings": findings, "risk_level": "high" if any(x["severity"]=="high" for x in findings) else ("medium" if findings else "low"), "status": "review-required" if findings else "no-obvious-findings"}
