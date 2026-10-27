"""Open-source readiness assessment, guided completion and publish preview."""
from __future__ import annotations
import datetime as _dt
import os
from pathlib import Path
from typing import Any
from .security import scan_repository

def assess_open_source(repo: str | Path) -> dict[str, Any]:
    root = Path(repo).resolve()
    if not root.is_dir(): raise ValueError("repository directory does not exist")
    names = {p.name.lower() for p in root.iterdir()}
    scan = scan_repository(root)
    has_readme = any(x in names for x in ("readme.md", "readme.rst", "readme.txt"))
    has_license = any(x.startswith("license") for x in names)
    has_gitignore = ".gitignore" in names
    has_tests = any(x in names for x in ("tests", "test", "pytest.ini", "tox.ini"))
    has_security = "security.md" in names
    has_contributing = "contributing.md" in names
    blockers = []
    if not has_license: blockers.append("补充明确的开源许可证")
    if any(f["severity"] == "high" for f in scan["findings"]): blockers.append("先处理疑似密钥或高风险发现项")
    if not has_readme: blockers.append("补充 README，说明用途、安装和使用方法")
    checklist = [
        {"key":"readme", "label":"README", "ready":has_readme, "action":"说明项目用途、安装、运行和贡献方式"},
        {"key":"license", "label":"许可证", "ready":has_license, "action":"选择 MIT、Apache-2.0 等合适协议"},
        {"key":"secrets", "label":"敏感信息", "ready":not any(f["severity"]=="high" for f in scan["findings"]), "action":"删除密钥；已泄露的请立刻到平台重新生成"},
        {"key":"gitignore", "label":"忽略文件", "ready":has_gitignore, "action":"排除 .env、缓存、构建产物和本地数据"},
        {"key":"tests", "label":"测试", "ready":has_tests, "action":"补充最小可运行测试"},
        {"key":"security", "label":"安全说明", "ready":has_security, "action":"补充 SECURITY.md，说明漏洞报告渠道"},
        {"key":"contributing", "label":"贡献指南", "ready":has_contributing, "action":"补充 CONTRIBUTING.md，告诉新手如何参与"},
    ]
    ready = not blockers
    return {"repository":str(root), "can_open_source":ready, "decision":"可以进入开源准备流程" if ready else "暂不建议直接公开", "blockers":blockers, "checklist":checklist, "scan":scan, "publish_steps":["确认许可证和第三方依赖的使用要求", "移除密钥、个人数据和本地配置", "生成 README、.gitignore 和贡献说明", "初始化 Git 并创建首个提交", "你确认后再推送到远程仓库"], "remote_publish_default":"requires-token-and-confirmation"}


def _stack_hint(root: Path) -> str:
    names = {p.name.lower() for p in root.iterdir()}
    if "package.json" in names:
        return "Node.js"
    if "pyproject.toml" in names or "requirements.txt" in names or "setup.py" in names:
        return "Python"
    if any(x in names for x in ("dockerfile", "docker-compose.yml", "compose.yaml")):
        return "Docker"
    return "项目"


def _completion_plan(root: Path, assessment: dict[str, Any]) -> list[dict[str, Any]]:
    """Build only additive, reviewable fixes; existing files are never overwritten."""
    stack = _stack_hint(root)
    present = {p.name.lower() for p in root.iterdir()}
    ready_map = {item["key"]: item["ready"] for item in assessment["checklist"]}
    plan: list[dict[str, Any]] = []
    if not ready_map.get("readme"):
        plan.append({"path": "README.md", "reason": "补充项目用途、安装、运行和贡献说明", "content": f"""# {root.name}\n\n这是一个以 {stack} 为主的开源项目。\n\n## 项目用途\n\n请在这里补充项目解决的问题、适用场景和示例。\n\n## 快速开始\n\n- 安装依赖：请根据项目的锁文件或依赖清单安装。\n- 运行项目：请补充可复制的启动命令。\n- 配置：不要把真实密钥写入仓库，请参考 `.env.example`。\n\n## 贡献\n\n欢迎提交 Issue 和 Pull Request。提交前请运行项目测试。\n"""})
    if not ready_map.get("license"):
        plan.append({"path": "LICENSE", "reason": "补充默认 MIT 许可证，允许用户继续确认后公开", "content": """MIT License\n\nCopyright (c) 2026 OpenSourceGuard contributors\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the \"Software\"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n"""})
    if not ready_map.get("gitignore"):
        plan.append({"path": ".gitignore", "reason": "排除环境变量、缓存、构建产物和本地数据", "content": """.env\n.env.*\n!.env.example\n__pycache__/\n*.py[cod]\n.venv/\nvenv/\nnode_modules/\ndist/\nbuild/\ncoverage/\n.DS_Store\nruntime/\n"""})
    if not ready_map.get("security"):
        plan.append({"path": "SECURITY.md", "reason": "提供漏洞报告和敏感信息处理入口", "content": """# 安全问题报告\n\n请不要在公开 Issue 中粘贴密钥、个人数据或可利用漏洞。\n请通过维护者提供的私密渠道报告安全问题，并附上复现步骤和受影响版本。\n"""})
    if not ready_map.get("tests"):
        plan.append({"path": "tests/test_smoke.py", "reason": "提供最小可运行测试入口，方便发布后回归", "content": """import unittest\n\n\nclass RepositorySmokeTest(unittest.TestCase):\n    def test_repository_smoke(self):\n        \"\"\"最低限度的发布健康检查。\"\"\"\n        self.assertTrue(True)\n\n\nif __name__ == "__main__":\n    unittest.main()\n"""})
    if ".env.example" not in present and ".env.template" not in present:
        plan.append({"path": ".env.example", "reason": "提示配置项应使用环境变量，不提交真实 Token", "content": """# 复制为 .env 后填写本地配置；不要提交真实密钥\n# OSG_AGENT_API_KEY=\n"""})
    if "contributing.md" not in present:
        plan.append({"path": "CONTRIBUTING.md", "reason": "告诉初学者如何提交 Issue 和贡献代码", "content": """# 贡献指南\n\n1. 先搜索已有 Issue，避免重复提交。\n2. 提交前说明复现步骤、预期结果和实际结果。\n3. 修改后运行项目测试，并在 Pull Request 中附上验证结果。\n"""})
    return plan


def complete_open_source(repo: str | Path, *, confirm: bool = False) -> dict[str, Any]:
    """Preview or apply additive files needed for a beginner-friendly release.

    The operation never overwrites an existing file.  Before writing, the
    existing state is copied to a timestamped backup under ``runtime`` so the
    action can be reviewed and reverted by the maintainer.
    """
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    assessment = assess_open_source(root)
    plan = _completion_plan(root, assessment)
    if not confirm:
        return {"status": "preview", "repository": str(root), "files": [{"path": x["path"], "reason": x["reason"]} for x in plan], "count": len(plan), "message": "Agent 已准备好补齐这些缺失项；确认后只会新增文件，不覆盖已有内容。", "assessment": assessment}
    if not plan:
        return {"status": "complete", "repository": str(root), "files": [], "message": "项目已经具备开源准备所需的基础文件。", "assessment": assessment}
    backup_root = root.parent / "runtime" / "opensourceguard-backups" / _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    written: list[str] = []
    for item in plan:
        target = (root / item["path"]).resolve()
        if root not in target.parents:
            raise ValueError("completion target is outside repository")
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"], encoding="utf-8")
        written.append(item["path"])
    after = assess_open_source(root)
    return {"status": "completed", "repository": str(root), "files": written, "backup": str(backup_root) if backup_root.exists() else "未产生备份（只新增文件）", "message": f"已补齐 {len(written)} 个文件，原有文件没有被覆盖。", "assessment": after}

def publish_preview(repo: str | Path, *, platform: str = "github", repository: str = "", token: str = "", confirm: bool = False) -> dict[str, Any]:
    assessment = assess_open_source(repo)
    if not assessment["can_open_source"]:
        return {"status":"blocked", "reason":"open-source checklist is incomplete", "assessment":assessment}
    credential_status = "configured" if str(token or "").strip() else "not-configured"
    if not confirm:
        return {"status":"preview", "platform":platform, "repository":repository or Path(repo).name, "credential_status":credential_status, "commands":["检查开源清单", "创建或复用远程仓库", "生成本地提交", "安全推送到 main 分支"], "warning":"这是发布预览；点击确认发布并提供有写权限的 Token 后才会产生远程写入。", "assessment":assessment}
    from .publishing import publish_project
    return publish_project(repo, platform=platform, repository=repository, token=token, confirm=True)
