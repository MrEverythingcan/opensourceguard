"""Project decluttering: inventory, classify, and safely archive messy files.

Beginners often keep everything in the project root: code next to screenshots,
zips, and "final_v2" copies. This module takes an inventory, proposes a plan,
and only performs *reversible* actions (archive, never delete) when asked.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

TOOL_VERSION = "0.6.0"
ARCHIVE_DIR = ".osg_archive"
MAX_FILES = 500
LARGE_FILE_MB = 10

CATEGORY_EXT: dict[str, set[str]] = {
    "code": {".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".java", ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".sh", ".bat", ".ps1", ".vue"},
    "docs": {".md", ".rst", ".txt", ".docx", ".doc", ".pdf"},
    "images": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp"},
    "data": {".csv", ".json", ".db", ".sqlite", ".sqlite3", ".xlsx", ".xls", ".tsv"},
    "config": {".yml", ".yaml", ".toml", ".ini", ".cfg", ".lock"},
    "archives": {".zip", ".tar", ".gz", ".rar", ".7z"},
    "audio_video": {".mp3", ".mp4", ".wav", ".mov", ".avi"},
}
CATEGORY_LABELS = {"code": "代码", "docs": "文档", "images": "图片", "data": "数据", "config": "配置", "archives": "压缩包", "audio_video": "音视频", "other": "其他"}

JUNK_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
JUNK_SUFFIXES = {".tmp", ".bak", ".old", ".orig", ".swp"}
JUNK_NAME_HINTS = ("副本", "新建", "未命名", "最终版", "拷贝")
SENSITIVE_NAMES = {".env", "id_rsa", "id_dsa", "id_ed25519"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
SENSITIVE_HINTS = ("password", "secret", "credential", "密码", "密钥")
IGNORE_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", ARCHIVE_DIR, "dist", "build", ".idea", ".vscode"}


def _category_of(path: Path) -> str:
    suffix = path.suffix.lower()
    for category, suffixes in CATEGORY_EXT.items():
        if suffix in suffixes:
            return category
    return "other"


def _is_junk(path: Path) -> bool:
    name = path.name.lower()
    if name in JUNK_NAMES or path.suffix.lower() in JUNK_SUFFIXES:
        return True
    return any(hint in path.stem for hint in JUNK_NAME_HINTS)


def _is_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    return any(hint in name for hint in SENSITIVE_HINTS)


def analyze_project(repo: str | Path) -> dict[str, Any]:
    """Walk the repository and bucket every file into a category."""

    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    files: list[dict[str, Any]] = []
    truncated = False
    for path in sorted(root.rglob("*")):
        if len(files) >= MAX_FILES:
            truncated = True
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part in IGNORE_DIRS for part in rel.parts):
            continue
        size = path.stat().st_size
        files.append({
            "path": rel.as_posix(),
            "name": path.name,
            "category": _category_of(path),
            "size_bytes": size,
            "in_root": len(rel.parts) == 1,
            "junk": _is_junk(path),
            "sensitive": _is_sensitive(path),
            "large": size > LARGE_FILE_MB * 1024 * 1024,
        })
    categories: dict[str, int] = {}
    total_size = 0
    for entry in files:
        categories[entry["category"]] = categories.get(entry["category"], 0) + 1
        total_size += entry["size_bytes"]
    return {
        "repo": str(root),
        "total_files": len(files),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "truncated": truncated,
        "categories": categories,
        "files": files,
    }


def _fallback_advice(summary: dict[str, Any]) -> str:
    bits = []
    if summary["junk"]:
        bits.append(f"先清掉 {summary['junk']} 个临时/重复文件，开源页面对访客的观感会立刻好起来")
    if summary["sensitive"]:
        bits.append(f"发现 {summary['sensitive']} 个疑似敏感文件，务必确认不要上传")
    if summary["root_scatter"]:
        bits.append(f"根目录散落 {summary['root_scatter']} 个文件，建议按用途归入 src/ docs/ assets/")
    return "；".join(bits) + "。" if bits else "目录已经很整齐，可以直接进入一键补齐环节。"


def plan_organization(repo: str | Path, *, model_callback: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Build a declutter plan: safe auto actions + manual suggestions."""

    inventory = analyze_project(repo)
    files = inventory["files"]
    root = Path(inventory["repo"])

    auto_actions: list[dict[str, str]] = []
    manual: list[str] = []
    gitignore: set[str] = set()

    junk = [f for f in files if f["junk"]]
    sensitive = [f for f in files if f["sensitive"]]
    large = [f for f in files if f["large"] and not f["junk"]]
    root_archives = [f for f in files if f["category"] == "archives" and f["in_root"] and not f["junk"]]
    root_code = [f for f in files if f["category"] == "code" and f["in_root"] and not f["sensitive"] and not f["junk"]]
    root_assets = [f for f in files if f["category"] in {"images", "data", "audio_video"} and f["in_root"] and not f["junk"]]
    root_docs = [f for f in files if f["category"] == "docs" and f["in_root"] and not f["junk"] and f["name"].lower() not in {"readme.md", "license", "license.md", "contributing.md", "security.md"}]

    for f in junk + root_archives:
        reason = "系统/编辑器临时文件，没有开源价值" if f["junk"] else "压缩包不适合进版本库，已为你保留本地归档"
        auto_actions.append({"action": "archive", "from": f["path"], "to": f"{ARCHIVE_DIR}/{f['path']}", "reason": reason})

    if sensitive:
        names = "、".join(f["name"] for f in sensitive[:5])
        manual.append(f"敏感文件（{names}）：不要开源它们，确认后从待发布范围排除")
        gitignore.update(f["name"] for f in sensitive if f["in_root"])
    if len(root_code) >= 3 and not (root / "src").is_dir():
        names = "、".join(f["name"] for f in root_code[:5])
        manual.append(f"{len(root_code)} 个代码文件散落在根目录（{names}），建议手动移入 src/ —— 移动代码会影响 import 路径，Agent 不代劳")
    if root_assets:
        manual.append(f"{len(root_assets)} 个图片/数据/音视频文件在根目录，建议手动归入 assets/ 或 docs/")
    if root_docs:
        names = "、".join(f["name"] for f in root_docs[:4])
        manual.append(f"文档文件（{names}）建议移入 docs/，README/LICENSE 保留在根目录")
    if large:
        names = "、".join(f["name"] for f in large[:3])
        manual.append(f"大文件（{names}，超过 {LARGE_FILE_MB}MB）不适合直接提交 Git，考虑网盘或 Git LFS")
    if junk:
        gitignore.update(["*.tmp", "*.bak", "Thumbs.db", ".DS_Store"])
    gitignore.add(f"{ARCHIVE_DIR}/")

    summary = {"junk": len(junk), "sensitive": len(sensitive), "large": len(large), "root_scatter": len(root_code) + len(root_assets) + len(root_docs) + len(root_archives)}
    source = "local-rules"
    advice = _fallback_advice(summary)
    if model_callback:
        prompt = (
            "用亲切的中文三句话以内给一个编程新手总结这份项目整理盘点，告诉他先做什么、为什么："
            f"文件总数 {inventory['total_files']}，垃圾文件 {summary['junk']} 个，敏感文件 {summary['sensitive']} 个，"
            f"根目录散落 {summary['root_scatter']} 个，分类统计 {inventory['categories']}。只输出建议文字。"
        )
        try:
            text = (model_callback(prompt) or "").strip()
            if text:
                advice = text
                source = "model+local"
        except Exception:  # noqa: BLE001 - fall back to the template advice
            pass

    return {
        "repo": inventory["repo"],
        "inventory": {k: v for k, v in inventory.items() if k != "files"},
        "files": files,
        "summary": summary,
        "auto_actions": auto_actions,
        "manual_suggestions": manual,
        "gitignore_suggestions": sorted(gitignore),
        "advice": advice,
        "source": source,
    }


def apply_organization(repo: str | Path, *, append_gitignore: bool = True) -> dict[str, Any]:
    """Execute the safe actions only: archive junk/zips, optionally extend .gitignore.

    Everything lands under .osg_archive/ with the original relative path kept,
    so the operation is fully reversible.
    """

    plan = plan_organization(repo)
    root = Path(plan["repo"])
    moved: list[dict[str, str]] = []
    skipped: list[str] = []
    for action in plan["auto_actions"]:
        src = root / action["from"]
        dst = root / action["to"]
        if not src.exists():
            skipped.append(action["from"])
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            skipped.append(action["from"])
            continue
        shutil.move(str(src), str(dst))
        moved.append({"from": action["from"], "to": action["to"]})

    gitignore_added: list[str] = []
    if append_gitignore and plan["gitignore_suggestions"]:
        gi = root / ".gitignore"
        existing = set(gi.read_text(encoding="utf-8").splitlines()) if gi.exists() else set()
        for line in plan["gitignore_suggestions"]:
            if line not in existing:
                gitignore_added.append(line)
        if gitignore_added:
            needs_newline = gi.exists() and gi.stat().st_size > 0
            with gi.open("a", encoding="utf-8") as fh:
                if needs_newline:
                    fh.write("\n")
                fh.write("\n".join(gitignore_added) + "\n")

    return {
        "repo": plan["repo"],
        "moved": moved,
        "skipped": skipped,
        "gitignore_added": gitignore_added,
        "archive_dir": ARCHIVE_DIR,
        "reversible": True,
        "manual_suggestions": plan["manual_suggestions"],
        "summary": plan["summary"],
    }
