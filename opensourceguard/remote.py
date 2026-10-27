"""Public repository URL inspection and safe local deployment planning."""
from __future__ import annotations
import base64
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ALLOWED_HOSTS = {"github.com", "www.github.com", "gitlab.com", "www.gitlab.com", "gitee.com", "www.gitee.com", "modelscope.cn", "www.modelscope.cn", "modelscope.ai"}

def validate_repo_url(url: str) -> tuple[str, str, str]:
    raw = str(url or "").strip()
    parsed = urllib.parse.urlparse(raw)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ValueError("目前只支持 GitHub、GitLab、Gitee 和 ModelScope 的公开仓库链接")
    path = parsed.path.strip("/")
    if not path or path.count("/") < 1 or any(x in path for x in ("..", "\\")):
        raise ValueError("仓库链接格式不正确")
    if host in {"modelscope.cn", "www.modelscope.cn", "modelscope.ai"}:
        parts = path.split("/")
        if parts and parts[0] in {"models", "datasets", "spaces"}:
            path = "/".join(parts[1:])
        if path.count("/") < 1:
            raise ValueError("ModelScope 仓库链接格式不正确")
    return raw, host, path.rstrip("/")

def _fetch(url: str, limit: int = 12000) -> str:
    req = urllib.request.Request(url, headers={"User-Agent":"OpenSourceGuard/0.5"})
    with urllib.request.urlopen(req, timeout=12) as response:
        return response.read(limit).decode("utf-8", "replace")

_LICENSE_TEXT_HINTS = [
    ("Apache-2.0", ("apache license", "version 2.0")),
    ("MIT", ("mit license", "permission is hereby granted")),
    ("GPL-3.0", ("gnu general public license", "version 3")),
    ("GPL-2.0", ("gnu general public license", "version 2")),
    ("LGPL", ("gnu lesser general public license",)),
    ("AGPL", ("affero general public license",)),
    ("BSD", ("redistribution and use in source and binary forms",)),
]

_PERMISSIVE = ("MIT", "APACHE", "BSD", "ISC", "0BSD", "ZLIB", "UNLICENSE")
_COPYLEFT = ("GPL", "LGPL", "AGPL", "MPL", "EPL", "CC-BY-SA", "OSL")

def _recognize_license_text(text: str) -> str:
    lower = text.lower()
    for name, keywords in _LICENSE_TEXT_HINTS:
        if all(keyword in lower for keyword in keywords):
            return name
    return ""

def _license_info(name: str) -> tuple[str, str]:
    upper = str(name or "").upper()
    if not upper or upper in {"未识别", "未读取", "需要下载后本地检查", "NOASSERTION", "NONE"}:
        return "暂不建议直接商用", "没有确认许可证前，复制、修改、商用和再分发的边界都不清晰，应先联系作者确认，或下载到本地核实 LICENSE 文件。"
    if upper.startswith(_PERMISSIVE):
        return "通常可以商用", f"{name} 属于宽松许可证：一般允许商用、修改和再分发，注意保留版权和许可声明即可。"
    if upper.startswith(_COPYLEFT):
        return "可商用但有开源义务", f"{name} 属于著佐权（Copyleft）许可证：可以商用，但修改后再分发通常需要按相同条款公开源码。"
    return "商用前需人工确认条款", f"检测到许可证 {name}，不属于最常见的几类，商用或再分发前建议人工通读条款。"

_INSTALL_RE = re.compile(r"(pip3? install [^\s`]+|uv pip install [^\s`]+|pipx install [^\s`]+|npm (?:install|i)(?:\s+[^\s`]+)?|pnpm (?:add|install)(?:\s+[^\s`]+)?|yarn add [^\s`]+|docker(?:-compose)? (?:run|build|up|compose)[^\n`|]*|git clone [^\n`]+|cargo install [^\s`]+|go install \S+|conda install [^\n`|]+)")
_CODEISH_RE = re.compile(r"^(?:import|from|require|package|func|def|class|const|let|var)\s|^\w+\s*=|^\w+\.\w+")

_STACK_PATTERNS = [(re.compile(p, re.I), label) for p, label in [
    (r"\bpython\b", "Python"), (r"\bpytorch\b", "PyTorch"), (r"\btransformers\b", "Transformers"),
    (r"\btypescript\b", "TypeScript"), (r"\bnode\.?js\b|\bnpm\b", "Node.js"),
    (r"\breact\b", "React"), (r"\bvue\b", "Vue"), (r"\bfastapi\b", "FastAPI"),
    (r"\bflask\b", "Flask"), (r"\bdjango\b", "Django"), (r"\bdocker\b", "Docker"),
    (r"\bkubernetes\b|\bk8s\b", "Kubernetes"), (r"\brust\b", "Rust"),
    (r"\bgolang\b|\bgo install\b", "Go"), (r"\bjava\b(?!script)", "Java"),
    (r"c\+\+", "C++"), (r"\bllm\b|大语言模型", "大语言模型"), (r"\bmcp\b", "MCP 协议"),
    (r"\bagents?\b", "AI Agent"),
]]

def _readme_signals(readme: str) -> dict[str, Any]:
    highlights: list[str] = []
    for raw in readme.splitlines():
        line = raw.strip().lstrip("#>*+- ").replace("**", "").replace("__", "").strip()
        if len(line) < 12 or line.startswith(("!", "<", "[", "http", "'", '"', "{", "}")) or _INSTALL_RE.search(line) or _CODEISH_RE.search(line):
            continue
        if line not in highlights:
            highlights.append(line[:160])
        if len(highlights) >= 4:
            break
    installs: list[str] = []
    for match in _INSTALL_RE.finditer(readme):
        cmd = " ".join(match.group(0).split())[:120]
        if cmd not in installs:
            installs.append(cmd)
        if len(installs) >= 4:
            break
    stack: list[str] = []
    for pattern, label in _STACK_PATTERNS:
        if pattern.search(readme) and label not in stack:
            stack.append(label)
    return {"highlights": highlights, "install_commands": installs, "stack_signals": stack[:6]}

def _fetch_license_text(owner: str, repo: str, branch: str) -> str:
    for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE"):
        try:
            return _fetch(f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{name}", 6000)
        except Exception:
            continue
    return ""

def inspect_repository_url(url: str, model_callback: Callable[[str], str] | None = None) -> dict[str, Any]:
    raw, host, path = validate_repo_url(url)
    owner, repo = path.split("/", 1)[:2]
    repo = repo.removesuffix(".git")
    if host in {"modelscope.cn", "www.modelscope.cn", "modelscope.ai"}:
        platform = "modelscope"
    elif host in {"github.com", "www.github.com"}:
        platform = "github"
    elif host in {"gitlab.com", "www.gitlab.com"}:
        platform = "gitlab"
    else:
        platform = "gitee"
    clone_url = raw
    if platform == "modelscope":
        clone_url = f"https://www.modelscope.cn/models/{owner}/{repo}.git"
    result = {"url":raw, "platform":platform, "owner":owner, "repository":repo, "public":True, "source":"url-inspection", "readme":"", "license":"未读取", "notes":[], "deployment":{"status":"preview-only", "command":["git", "clone", clone_url]}, "risks":[]}
    try:
        if "github.com" in host:
            api = json.loads(_fetch(f"https://api.github.com/repos/{owner}/{repo}", 30000))
            result["description"] = api.get("description") or "仓库没有公开简介"
            result["default_branch"] = api.get("default_branch", "main")
            result["stars"] = api.get("stargazers_count", 0)
            result["forks"] = api.get("forks_count", 0)
            result["open_issues"] = api.get("open_issues_count", 0)
            result["language"] = api.get("language") or ""
            result["topics"] = [str(t) for t in (api.get("topics") or [])][:8]
            result["pushed_at"] = api.get("pushed_at") or ""
            readme = json.loads(_fetch(f"https://api.github.com/repos/{owner}/{repo}/readme", 30000))
            result["readme"] = base64.b64decode(readme.get("content", "")).decode("utf-8", "replace")[:12000]
            spdx = str((api.get("license") or {}).get("spdx_id") or "")
            if spdx and spdx != "NOASSERTION":
                result["license"] = spdx
            else:
                recognized = _recognize_license_text(_fetch_license_text(owner, repo, result["default_branch"]))
                result["license"] = recognized or "未识别"
            if api.get("archived"):
                result["notes"].append("仓库已被作者归档（只读），大概率不再维护。")
                result["risks"].append("仓库已归档，不再接收更新，生产环境慎用")
        elif platform == "modelscope":
            result["description"] = f"ModelScope 公开模型仓库：{owner}/{repo}"
            result["notes"].append("ModelScope 详情读取需要 Token 或下载后本地检查。")
            result["license"] = "需要下载后本地检查"
        else:
            result["description"] = f"公开仓库：{owner}/{repo}"
            result["readme"] = _fetch(raw.rstrip("/") + "/raw/main/README.md", 12000)
            result["license"] = "需要下载后本地检查"
    except Exception as exc:
        result["notes"].append("远程详情读取不完整，建议先下载到本地后再检查。")
        result["fetch_error"] = type(exc).__name__
    readme_text = result.get("readme") or ""
    signals = _readme_signals(readme_text) if readme_text else {"highlights": [], "install_commands": [], "stack_signals": []}
    if result.get("language") and result["language"] not in signals["stack_signals"]:
        signals["stack_signals"].insert(0, result["language"])
    result["analysis"] = signals
    fit, note = _license_info(result.get("license", ""))
    result["commercial_fit"] = fit
    result["license_note"] = note
    if not readme_text: result["risks"].append("没有读取到 README，无法完整判断用途和部署方式")
    if result.get("license") in {"未读取", "未识别", "需要下载后本地检查"}: result["risks"].append("许可证尚未确认，不能直接用于商用或再分发")
    if "stars" in result and result["stars"] < 50: result["risks"].append(f"Star 只有 {result['stars']}，社区验证较少，商用前建议人工检查代码质量")
    pushed = result.get("pushed_at") or ""
    if pushed:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(pushed.replace("Z", "+00:00")) > timedelta(days=545):
                result["risks"].append(f"仓库超过 18 个月没有更新（最后提交 {pushed[:10]}），依赖安全性和问题响应可能滞后")
        except ValueError:
            pass
    description = result.get("description") or ""
    if description and description != "仓库没有公开简介":
        result["one_sentence"] = description
    elif signals["highlights"]:
        result["one_sentence"] = signals["highlights"][0]
    else:
        result["one_sentence"] = f"这是 {owner}/{repo} 的公开开源仓库。"
    summary_parts = []
    if description and description != "仓库没有公开简介":
        summary_parts.append(f"项目自述：{description}")
    if signals["highlights"]:
        summary_parts.append("README 要点：" + "；".join(signals["highlights"][:2]) + "。")
    if signals["stack_signals"]:
        summary_parts.append("技术信号：" + "、".join(signals["stack_signals"]) + "。")
    if "stars" in result:
        summary_parts.append(f"社区规模：{result['stars']} 个 Star、{result.get('forks', 0)} 个 Fork。")
    summary_parts.append(f"许可证：{result['license']}（{fit}）。")
    result["summary"] = "".join(summary_parts)
    result["summary_source"] = "rules"
    if model_callback is not None:
        brief = {"name": f"{owner}/{repo}", "platform": platform, "description": description, "license": result["license"], "commercial_fit": fit, "stars": result.get("stars"), "language": result.get("language"), "topics": result.get("topics"), "highlights": signals["highlights"], "install_commands": signals["install_commands"], "readme_excerpt": readme_text[:3000]}
        try:
            text = str(model_callback(json.dumps(brief, ensure_ascii=False))).strip()
            if text:
                result["summary"] = text
                result["summary_source"] = "model"
        except Exception as exc:
            result["notes"].append(f"AI 详细解读生成失败（{type(exc).__name__}），已回退为本地规则解读。")
    steps = ["先看上面的「Agent 详细解读」和 README 亮点，确认项目是否符合需求"]
    steps.append(f"确认许可证（{result['license']}）：{fit}")
    if signals["install_commands"]:
        steps.append("按 README 的安装命令在隔离环境试跑：" + signals["install_commands"][0])
    else:
        steps.append("README 中没找到明确安装命令，部署前先通读文档")
    steps.append("需要本地试运行时点「本地部署预览」，确认命令后再下载")
    result["next_steps"] = steps
    return result

def deployment_plan(url: str, target_root: str | Path = "runtime/remote-repos") -> dict[str, Any]:
    inspected = inspect_repository_url(url)
    target = (Path(target_root).resolve() / inspected["repository"]).resolve()
    root = Path(target_root).resolve()
    if root not in target.parents: raise ValueError("deployment target is outside allowed root")
    return {"status":"preview", "repository":inspected["repository"], "source_url":inspected["url"], "target":str(target), "commands":["git", "clone", inspected["url"], str(target)], "warnings":["首次只生成预览，不会下载或执行项目。", "下载后仍需检查许可证、依赖、端口和环境变量。"], "inspection":inspected}
