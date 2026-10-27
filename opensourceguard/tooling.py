"""Safe tool registry exposed to the Agent and compatible model adapters."""
from __future__ import annotations
from typing import Any, Callable
from .beginner import BeginnerAgent
from .security import scan_repository
from .remote import inspect_repository_url, deployment_plan
from .open_source import assess_open_source, complete_open_source, publish_preview
from .publishing import connect_platform, publish_project

class ToolRegistry:
    def __init__(self, beginner: BeginnerAgent):
        self.beginner = beginner
        self._tools: dict[str, tuple[str, str, Callable[..., Any]]] = {
            "repository.inspect_url": ("读取公开仓库链接并解释项目", "read", inspect_repository_url),
            "repository.explain_local": ("解释本地仓库用途、技术栈和许可证", "read", beginner.explain),
            "repository.ask": ("结合仓库上下文回答用户问题", "read", beginner.ask),
            "repository.security_scan": ("扫描许可证、依赖和疑似敏感信息", "read", scan_repository),
            "repository.deploy_preview": ("生成本地部署预览", "preview", deployment_plan),
            "repository.open_source_assess": ("判断项目是否适合开源", "read", assess_open_source),
            "repository.open_source_complete": ("补齐 README、许可证、忽略文件和最小测试", "write", complete_open_source),
            "repository.open_source_publish_preview": ("生成开源发布方案", "preview", publish_preview),
            "platform.connect": ("验证平台 Token 并同步账号仓库列表", "read", connect_platform),
            "platform.publish": ("把经过检查的本地项目发布到 GitHub 或 ModelScope", "write", publish_project),
        }

    def describe(self) -> list[dict[str, str]]:
        return [{"name": name, "description": desc, "permission": permission} for name, (desc, permission, _) in self._tools.items()]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools: raise ValueError("unknown agent tool")
        desc, permission, fn = self._tools[name]
        if permission == "write" and not bool(arguments.get("confirm")):
            raise ValueError("写入型 Agent 工具必须由用户确认后调用")
        if permission not in {"read", "preview", "write"}: raise ValueError("tool permission is not allowed")
        target = arguments.get("url") or arguments.get("repo_url") or arguments.get("repo")
        if name == "repository.inspect_url": return {"tool": name, "permission": permission, "result": fn(target)}
        if name == "repository.ask": return {"tool": name, "permission": permission, "result": fn(target, arguments.get("question", ""), context=arguments.get("context") or {})}
        if name == "platform.connect": return {"tool": name, "permission": permission, "result": fn(arguments.get("platform", "github"), arguments.get("token", ""))}
        return {"tool": name, "permission": permission, "result": fn(target, **{k:v for k,v in arguments.items() if k not in {"url","repo_url","repo"}})}
