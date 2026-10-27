"""Structured dependency parsing with versions (shared by SBOM and OSV scan).

Unlike license_audit (which only needs package names), the supply-chain
features need exact versions, pin status and purls, so parsing lives here.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import quote

_DEP_FILES = ("requirements.txt", "pyproject.toml", "package.json")


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _parse_requirement_line(line: str) -> tuple[str, str | None, bool] | None:
    """Parse one PEP 508-ish line into (name, version, pinned)."""

    line = line.strip()
    if not line or line.startswith(("#", "-")):
        return None
    line = line.split("#", 1)[0].strip()  # trailing comment
    line = line.split(";", 1)[0].strip()  # environment marker
    name = re.split(r"[<>=!~;\[\s@]", line, 1)[0].strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_.\-]+", name):
        return None
    exact = re.search(r"==\s*([A-Za-z0-9_.*+!\-]+)", line)
    if exact:
        return name, exact.group(1), True
    if "@" in line:  # direct URL reference is effectively pinned
        return name, None, True
    lower = re.search(r"(?:>=|~=)\s*([A-Za-z0-9_.*+!\-]+)", line)
    if lower:
        return name, lower.group(1), False
    return name, None, False


def _python_dependencies(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add(name: str, version: str | None, pinned: bool, source: str, scope: str) -> None:
        entries.append({"name": _normalize(name), "version": version, "ecosystem": "PyPI",
                        "pinned": pinned, "source_file": source, "scope": scope})

    req = root / "requirements.txt"
    if req.exists():
        for line in req.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            parsed = _parse_requirement_line(line)
            if parsed:
                add(parsed[0], parsed[1], parsed[2], "requirements.txt", "required")

    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8-sig", errors="replace"))
        except tomllib.TOMLDecodeError:
            data = {}
        project = data.get("project") or {}
        for dep in project.get("dependencies") or []:
            parsed = _parse_requirement_line(str(dep))
            if parsed:
                add(parsed[0], parsed[1], parsed[2], "pyproject.toml", "required")
        for group in (project.get("optional-dependencies") or {}).values():
            for dep in group or []:
                parsed = _parse_requirement_line(str(dep))
                if parsed:
                    add(parsed[0], parsed[1], parsed[2], "pyproject.toml", "optional")
    return entries


def _node_dependencies(root: Path) -> list[dict[str, Any]]:
    pkg = root / "package.json"
    if not pkg.exists():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8-sig", errors="replace"))
    except json.JSONDecodeError:
        return []
    entries: list[dict[str, Any]] = []
    for key, scope in (("dependencies", "required"), ("devDependencies", "optional")):
        for raw_name, spec in (data.get(key) or {}).items():
            name = str(raw_name).strip().lower()
            if not name:
                continue
            spec = str(spec or "").strip()
            version: str | None = None
            pinned = False
            match = re.search(r"(\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-]*)?)", spec)
            if match and not spec.startswith(("file:", "workspace:", "git", "http")):
                version = match.group(1)
                pinned = not spec.startswith(("^", "~", ">", "<", "*"))
            entries.append({"name": name, "version": version, "ecosystem": "npm",
                            "pinned": pinned, "source_file": "package.json", "scope": scope})
    return entries


def purl_for(entry: dict[str, Any]) -> str:
    """Build a Package URL for one dependency entry."""

    eco = "pypi" if entry["ecosystem"] == "PyPI" else "npm"
    name = entry["name"]
    if eco == "npm" and name.startswith("@"):
        name = "%40" + name[1:]  # scoped npm package
    base = f"pkg:{eco}/{quote(name, safe='/@._-%')}"
    return f"{base}@{entry['version']}" if entry.get("version") else base


def parse_dependencies(repo: str | Path) -> dict[str, Any]:
    """Collect versioned dependencies from requirements/pyproject/package.json."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    entries = _python_dependencies(root) + _node_dependencies(root)
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[dict[str, Any]] = []
    for entry in entries:
        key = (entry["ecosystem"], entry["name"], entry["version"])
        if key in seen:
            continue
        seen.add(key)
        entry = dict(entry)
        entry["purl"] = purl_for(entry)
        unique.append(entry)
    unique.sort(key=lambda e: (e["ecosystem"], e["name"]))
    pinned = sum(1 for e in unique if e["pinned"])
    return {
        "repository": str(root),
        "files": [name for name in _DEP_FILES if (root / name).exists()],
        "dependencies": unique,
        "summary": {
            "total": len(unique),
            "pinned": pinned,
            "unpinned": len(unique) - pinned,
            "pinned_ratio": round(pinned / len(unique), 2) if unique else 1.0,
            "ecosystems": sorted({e["ecosystem"] for e in unique}),
        },
    }
