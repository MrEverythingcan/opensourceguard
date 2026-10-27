"""Safe dry-run adapters for GitHub, Gitee, GitLab and fork workflows."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Mapping

SUPPORTED = {"github", "gitee", "gitlab"}

@dataclass
class ProviderPlan:
    platform: str
    repository: str
    operation: str
    remote_write: bool
    steps: list[str]
    warnings: list[str]
    status: str = "dry-run"
    def to_dict(self) -> dict[str, Any]: return asdict(self)

def build_provider_plan(platform: str, repository: str, *, use_fork: bool = False, remote_write: bool = False) -> dict[str, Any]:
    platform = str(platform or "github").lower()
    if platform not in SUPPORTED: raise ValueError(f"unsupported platform: {platform}")
    target = f"{platform}:{repository or '未填写仓库'}"
    steps = ["读取问题（Issue）和仓库信息", "检查权限、许可证和风险标签", "在临时副本中生成并验证补丁"]
    if use_fork: steps += ["先把仓库复制（Fork）到你的账号下", "推送修复分支，生成给原作者审核的修改草稿（Draft PR）"]
    else: steps += ["生成修改草稿（Draft PR）预览，等待仓库维护者审核"]
    warnings = ["当前只是方案预览，不会真正访问或修改远程平台。", "真正提交时需要对应平台的 Token，并由你人工确认。"]
    return ProviderPlan(platform, target, "fork-draft-pr" if use_fork else "same-repository-draft-pr", remote_write, steps, warnings).to_dict()

def provider_capabilities() -> dict[str, Any]:
    return {"providers": [{"name": p, "read": True, "draft_pr": True, "remote_write": False} for p in sorted(SUPPORTED)], "fork_supported": True, "default_mode": "dry-run"}
