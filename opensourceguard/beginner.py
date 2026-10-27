"""Beginner-facing repository explainer and safe local deployment planner."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

_SYSTEM_PROMPT = "用中文面向初学者解释仓库信息，不改变许可证或安全判断。只能输出纯文本：不要使用任何 Markdown 标记（包括 **、__、# 标题、反引号、列表符号），用自然段落和中文分点表达。"


def _plain_text(text: str) -> str:
    """Remove markdown emphasis markers so answers render cleanly as plain text."""

    return text.replace("**", "").replace("__", "")


def _remote_brief(inspection: Mapping[str, Any]) -> dict[str, Any]:
    """Trim a remote inspection down to what the model needs to answer well."""

    owner = str(inspection.get("owner") or "")
    repo = str(inspection.get("repository") or inspection.get("name") or "")
    name = f"{owner}/{repo}" if owner and repo else (repo or owner or "远程仓库")
    one_sentence = str(inspection.get("one_sentence") or inspection.get("description") or f"这是 {name} 的公开开源仓库").strip().rstrip("。")
    return {
        "name": name,
        "platform": str(inspection.get("platform") or ""),
        "one_sentence": one_sentence,
        "license": str(inspection.get("license") or "未识别"),
        "risks": [str(x) for x in (inspection.get("risks") or [])],
        "next_steps": [str(x) for x in (inspection.get("next_steps") or [])],
        "readme_excerpt": str(inspection.get("readme") or "")[:3000],
    }


@dataclass
class RepositoryExplanation:
    name: str
    one_sentence: str
    what_it_does: list[str]
    suitable_for: list[str]
    commercial_fit: str
    commercial_reasons: list[str]
    license: str
    license_note: str
    deployment: dict[str, Any]
    risks: list[str]
    first_steps: list[str]
    confidence: str = "local-analysis"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _license(root: Path) -> tuple[str, str]:
    candidates = list(root.glob("LICENSE*")) + list(root.glob("license*"))
    text = _read(candidates[0], 4000).lower() if candidates else ""
    for name, keywords, note in [
        ("MIT", ["mit license", "permission is hereby granted"], "通常允许商用、修改和再分发，但要保留版权和许可声明。"),
        ("Apache-2.0", ["apache license", "version 2.0"], "通常允许商用并包含专利授权条款，但要保留 NOTICE 和许可声明。"),
        ("GPL-family", ["gnu general public license", "gpl"], "商用并非自动禁止，但分发衍生作品通常需要遵守相应开源义务。"),
    ]:
        if any(k in text for k in keywords):
            return name, note
    return ("未识别", "没有发现可确认的许可证，不能直接判断是否可以商用；应先联系权利人或补充许可证。")


def _detect_stack(root: Path) -> tuple[str, list[str], list[str]]:
    files = {p.name for p in root.iterdir()} if root.exists() else set()
    stack: list[str] = []
    steps: list[str] = []
    commands: list[str] = []
    if "pyproject.toml" in files or "requirements.txt" in files or (root / "setup.py").exists():
        stack.append("Python")
        steps.append("创建虚拟环境并安装 Python 依赖")
        commands = [sys.executable, "-m", "pip", "install", "-e", "."]
    if "package.json" in files:
        stack.append("Node.js")
        steps.append("安装前端依赖并运行 package.json 中的安全脚本")
        commands = ["npm", "install"]
    if "docker-compose.yml" in files or "compose.yaml" in files or "Dockerfile" in files:
        stack.append("Docker")
        steps.append("检查镜像来源、端口和环境变量后再启动容器")
    if not stack:
        stack.append("未知或自定义项目")
        steps.append("先阅读 README 和部署文档，再由维护者确认运行命令")
    return ", ".join(stack), steps, commands


class BeginnerAgent:
    """Explain a repository locally; optional model callback can polish prose."""

    def __init__(self, model_callback: Callable[[str], str] | None = None, model_stream: Callable[[str], Iterator[dict[str, str]]] | None = None):
        self.model_callback = model_callback
        self.model_stream = model_stream

    def explain(self, repo: str | Path) -> dict[str, Any]:
        root = Path(repo).resolve()
        if not root.is_dir():
            raise ValueError("repository directory does not exist")
        name = root.name
        readme = _read(root / "README.md") or _read(root / "README.rst") or _read(root / "README.txt")
        license_name, license_note = _license(root)
        stack, deployment_steps, _ = _detect_stack(root)
        has_tests = any((root / name).exists() for name in ("tests", "test", "pytest.ini", "tox.ini"))
        has_env = (root / ".env.example").exists() or (root / ".env.template").exists()
        readme_lines = [line.strip(" #-\t") for line in readme.splitlines() if line.strip() and not line.startswith("!")]
        one_sentence = readme_lines[0][:180] if readme_lines else f"这是一个以 {stack} 为主的开源项目，当前缺少足够的简介。"
        what = [one_sentence]
        if readme_lines:
            what.extend(readme_lines[1:4])
        reasons = []
        risks = []
        if license_name == "未识别":
            commercial = "暂不建议直接商用"
            reasons.append("没有识别到明确许可证，复制、修改和再分发边界不清晰。")
        else:
            commercial = "可以评估商用，但需要完成依赖和数据合规审查"
            reasons.append(f"项目许可证为 {license_name}：{license_note}")
        if not has_tests:
            risks.append("没有发现明显的测试目录，商用前需要补充回归测试。")
        if not has_env:
            risks.append("没有发现环境变量模板，部署前需要人工核对密钥和配置。")
        if not readme:
            risks.append("README 缺失或过短，项目用途和部署方法需要维护者补充。")
        deployment = {"stack": stack, "steps": deployment_steps, "preview_available": True, "execution_default": "disabled"}
        explanation = RepositoryExplanation(name, one_sentence, what[:5], ["希望快速了解项目的初学者", "需要评估部署成本的维护者"], commercial, reasons, license_name, license_note, deployment, risks, ["先查看许可证和 README", "运行测试或健康检查", "检查环境变量、端口和外部服务", "先用本地预览，再决定是否部署"], "local-analysis")
        result = explanation.to_dict()
        if self.model_callback:
            prompt = json.dumps(result, ensure_ascii=False)
            result["beginner_summary"] = self.model_callback(prompt)
            result["confidence"] = "local-analysis+model-polish"
        return result

    def ask(self, repo: str | Path, question: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Answer a beginner question using the repository explanation as context."""
        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        explanation = self.explain(repo)
        context = dict(context or {})
        current_view = str(context.get("view_title") or context.get("view") or "").strip()
        context_hint = f"用户当前正在查看“{current_view}”页面。" if current_view else ""
        if self.model_callback:
            prompt = json.dumps({"question": question, "repository": explanation}, ensure_ascii=False)
            return {"answer": self.model_callback(prompt), "source": "model+local-context", "repository": explanation["name"], "context": context}
        q = question.lower().replace("？", "?")
        if any(word in q for word in ("商用", "商业", "能不能用来赚钱", "生产", "可以卖", "能不能赚钱")):
            answer = f"{context_hint}这个项目目前的判断是：{explanation['commercial_fit']}。许可证是 {explanation['license']}；{explanation['license_note']} 还要检查依赖、数据来源、隐私和服务条款。"
        elif any(word in q for word in ("部署", "安装", "运行", "启动", "怎么用", "如何用", "怎么跑", "本地")):
            plan = explanation["deployment"]
            answer = f"{context_hint}它的技术栈是 {plan['stack']}。建议先按这些步骤做：{'；'.join(plan['steps'])}。我可以先生成部署预览，默认不会执行命令。"
        elif any(word in q for word in ("许可证", "开源协议", "license", "协议", "授权")):
            answer = f"{context_hint}检测到的许可证是 {explanation['license']}。{explanation['license_note']} 如果显示‘未识别’，不要直接商用或再分发。"
        elif any(word in q for word in ("是什么", "做什么", "干什么", "用途", "作用", "介绍", "看不懂", "能干嘛")):
            answer = f"{context_hint}简单说：{explanation['one_sentence']} 适合先查看 README、许可证和测试，再决定是否部署。"
        elif any(word in q for word in ("安全", "风险", "密钥", "漏洞", "隐私")):
            answer = f"{context_hint}目前需要重点关注：{'；'.join(explanation['risks']) or '暂未发现明显风险'}。正式使用前还应检查依赖来源、外部服务和环境变量。"
        elif any(word in q for word in ("测试", "稳定", "可靠吗", "质量")):
            answer = f"{context_hint}我检测到的项目测试情况需要结合仓库实际文件确认。建议先运行测试或健康检查，再决定是否部署；当前项目的测试风险提示是：{'；'.join(explanation['risks']) or '暂未发现明显风险'}。"
        else:
            answer = f"{context_hint}我先帮你抓住重点：{explanation['one_sentence']} 当前商用判断是“{explanation['commercial_fit']}”，部署技术栈为 {explanation['deployment']['stack']}。你可以继续问我许可证、部署、依赖或商用风险。"
        return {"answer": answer, "source": "local-analysis", "repository": explanation["name"], "context": context}

    def ask_stream(self, repo: str | Path, question: str, context: Mapping[str, Any] | None = None) -> Iterator[dict[str, str]]:
        """Yield answer/thinking events; falls back to one full answer when no streaming model is configured."""

        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        if not self.model_stream:
            yield {"delta": self.ask(repo, question, context)["answer"]}
            return
        explanation = self.explain(repo)
        prompt = json.dumps({"question": question, "repository": explanation}, ensure_ascii=False)
        yield from self.model_stream(prompt)

    def ask_remote(self, inspection: Mapping[str, Any], question: str, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Answer a beginner question about a remote repository inspection."""

        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        context = dict(context or {})
        brief = _remote_brief(inspection)
        if self.model_callback:
            prompt = json.dumps({"question": question, "repository": brief}, ensure_ascii=False)
            return {"answer": self.model_callback(prompt), "source": "model+remote-context", "repository": brief["name"], "context": context}
        q = question.lower().replace("？", "?")
        license_name = brief["license"]
        risks = "；".join(brief["risks"]) or "暂未发现明显风险"
        steps = "；".join(brief["next_steps"])
        if any(word in q for word in ("商用", "商业", "生产", "可以卖", "能不能赚钱")):
            answer = f"{brief['name']} 的许可证检测结果是：{license_name}。许可证未确认前不建议直接商用或再分发。风险提醒：{risks}。"
        elif any(word in q for word in ("部署", "安装", "运行", "启动", "下载", "怎么用", "如何用")):
            answer = f"{brief['name']} 的部署建议是：先 git clone 到本地隔离目录，确认许可证、依赖和环境变量后再运行。建议步骤：{steps}。"
        elif any(word in q for word in ("许可证", "开源协议", "license", "协议", "授权")):
            answer = f"{brief['name']} 的许可证检测结果是：{license_name}。{risks}。"
        else:
            answer = f"{brief['one_sentence']}。平台是 {brief['platform']}，许可证检测结果是 {license_name}。风险提醒：{risks}。建议下一步：{steps}。"
        return {"answer": answer, "source": "remote-repository-context", "repository": brief["name"], "context": context}

    def ask_remote_stream(self, inspection: Mapping[str, Any], question: str, context: Mapping[str, Any] | None = None) -> Iterator[dict[str, str]]:
        """Yield answer/thinking events for a remote repository; falls back to one full answer."""

        question = str(question or "").strip()
        if not question:
            raise ValueError("question is required")
        if not self.model_stream:
            yield {"delta": self.ask_remote(inspection, question, context)["answer"]}
            return
        prompt = json.dumps({"question": question, "repository": _remote_brief(inspection)}, ensure_ascii=False)
        yield from self.model_stream(prompt)


INTEREST_TOPIC_MAP: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("人工智能", "ai", "大模型", "机器学习", "深度学习", "llm", "智能体", "agent"), ("machine-learning", "llm", "ai-agents")),
    (("网页", "前端", "网站", "web", "frontend", "页面"), ("web", "frontend", "react")),
    (("游戏", "game"), ("game-engine", "game", "godot")),
    (("数据", "可视化", "分析", "报表"), ("data-science", "data-visualization")),
    (("爬虫", "自动化", "工具", "脚本", "效率"), ("automation", "cli", "web-scraping")),
    (("app", "移动", "手机", "安卓", "ios"), ("flutter", "android")),
)


def topics_for_interests(interests: str) -> tuple[str, ...]:
    """Map free-text interests to GitHub topics; empty means "use the default hot list"."""

    text = str(interests or "").lower()
    topics: list[str] = []
    for keywords, mapped in INTEREST_TOPIC_MAP:
        if any(keyword in text for keyword in keywords):
            for topic in mapped:
                if topic not in topics:
                    topics.append(topic)
    return tuple(topics)


_LEARNING_PATH: tuple[tuple[str, str], ...] = (
    ("装好基础工具", "注册一个 GitHub 账号，安装 Git；Python 项目再装 Python，前端项目再装 Node.js。"),
    ("先把项目跑起来", "把 {name} clone 到本地，照着 README 跑通第一个示例；跑不起来就先看 Issues 区有没有人遇到同样的问题。"),
    ("看懂这个项目", "回到本工具的「项目解读」页粘贴项目链接，让 Agent 用中文讲清它的用途、技术栈和许可证。"),
    ("找到第一个任务", "在项目 Issues 里找带 good first issue 或 help wanted 标签的问题，先从改文档、修错别字开始。"),
    ("做出第一个贡献", "fork 项目，改好后提 Pull Request；维护者的每条反馈都是最好的学习材料。"),
)


def _project_why(item: Mapping[str, Any]) -> str:
    stars = int(item.get("stars") or 0)
    stars_text = f"{stars:,}" if stars else "不少"
    tag = str(item.get("tag") or "热门")
    platform = "GitHub" if item.get("platform") == "github" else "魔搭社区"
    description = str(item.get("description") or "项目主页有更详细的介绍")
    return f"{stars_text} 人标星，是{platform}上「{tag}」方向的热门项目。{description}。新手建议先把它跑起来，再从文档和 Issues 区学习别人怎么讨论和解决问题。"


def recommend_projects(interests: str, items: Any, *, limit: int = 3, model_callback: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Pick beginner-friendly projects from trending items matching free-text interests.

    ``items`` is a list of normalized feed entries (platform/name/url/description/
    stars/tag). Ranking is deterministic: interest term hits first, then stars.
    When ``model_callback`` is configured, only the opening answer comes from the
    model; any model failure falls back to the template answer.
    """

    interests = str(interests or "").strip()
    if not interests:
        raise ValueError("interests is required")
    text = interests.lower()
    terms = {w for w in re.split(r"[^a-z0-9一-鿿]+", text) if len(w) > 1 and (not w.isascii() or len(w) > 2)}
    for keywords, mapped in INTEREST_TOPIC_MAP:
        if any(keyword in text for keyword in keywords):
            terms.update(mapped)
    candidates: dict[str, dict[str, Any]] = {}
    for item in items or []:
        name = str(item.get("name") or "")
        if not name or name in candidates:
            continue
        haystack = " ".join(str(item.get(k) or "") for k in ("name", "tag", "description", "language")).lower()
        hits = sum(1 for term in terms if term in haystack)
        candidates[name] = {"item": dict(item), "hits": hits}
    ranked = sorted(candidates.values(), key=lambda c: (-c["hits"], -int(c["item"].get("stars") or 0)))
    projects: list[dict[str, Any]] = []
    for entry in ranked[:limit]:
        item = entry["item"]
        projects.append({
            "name": str(item.get("name")),
            "url": str(item.get("url") or ""),
            "platform": str(item.get("platform") or "github"),
            "stars": int(item.get("stars") or 0),
            "description": str(item.get("description") or ""),
            "tag": str(item.get("tag") or ""),
            "matched": entry["hits"] > 0,
            "why": _project_why(item),
        })
    top_name = projects[0]["name"] if projects else "你感兴趣的项目"
    learning_path = [{"step": i + 1, "title": title, "detail": detail.replace("{name}", top_name)} for i, (title, detail) in enumerate(_LEARNING_PATH)]
    template = (f"你说对「{interests}」感兴趣，我从热门开源项目里挑了 {len(projects)} 个适合新手的。"
                "每个项目都附了推荐理由；点「详细解读」我会进一步讲清它是做什么的、能不能商用、怎么部署。"
                "下方的学习路线带你从装环境走到第一个贡献，按顺序来就行。")
    answer, source = template, "trending-cache"
    if model_callback and projects:
        try:
            briefs = [{"name": p["name"], "description": p["description"], "stars": p["stars"], "tag": p["tag"]} for p in projects]
            text_out = str(model_callback(json.dumps({"interests": interests, "candidates": briefs}, ensure_ascii=False))).strip()
            if text_out:
                answer, source = text_out, "model+trending"
        except Exception:  # noqa: BLE001 - the model is a polish layer only
            pass
    return {"interests": interests, "projects": projects, "learning_path": learning_path, "answer": answer, "source": source}


def compatible_model_callback_from_env(system_prompt: str | None = None, plain: bool = True) -> Callable[[str], str] | None:
    """Return an optional OpenAI-compatible chat callback when configured.

    No network request is made unless ``OSG_AGENT_BASE_URL`` and an API key are
    explicitly configured. ``system_prompt`` overrides the default plain-text
    explainer prompt; ``plain=False`` keeps markdown markers in the response
    (used for document generation such as README rewrites).
    """

    base = os.getenv("OSG_AGENT_BASE_URL", "").rstrip("/")
    key = os.getenv("OSG_AGENT_API_KEY", "")
    model = os.getenv("OSG_AGENT_MODEL", "")
    if not base or not key or not model:
        return None
    system = system_prompt or _SYSTEM_PROMPT

    def callback(prompt: str) -> str:
        payload = json.dumps({"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}], "temperature": 0.2}).encode()
        request = urllib.request.Request(base + "/chat/completions", payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read())
        text = str(data["choices"][0]["message"]["content"])
        return _plain_text(text) if plain else text

    return callback


def compatible_model_stream_from_env() -> Callable[[str], Iterator[dict[str, str]]] | None:
    """Return an optional streaming OpenAI-compatible chat callback when configured.

    No network request is made unless ``OSG_AGENT_BASE_URL`` and an API key are
    explicitly configured. Yields ``{"think": ...}`` events for reasoning
    content when the model provides it, and ``{"delta": ...}`` events for the
    answer. Answer text is plain text with markdown emphasis removed; thinking
    is passed through unmodified.
    """

    base = os.getenv("OSG_AGENT_BASE_URL", "").rstrip("/")
    key = os.getenv("OSG_AGENT_API_KEY", "")
    model = os.getenv("OSG_AGENT_MODEL", "")
    if not base or not key or not model:
        return None

    def stream(prompt: str) -> Iterator[dict[str, str]]:
        payload = json.dumps({"model": model, "messages": [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": prompt}], "temperature": 0.2, "stream": True}).encode()
        request = urllib.request.Request(base + "/chat/completions", payload, {"Content-Type": "application/json", "Authorization": "Bearer " + key}, method="POST")
        raw = ""
        emitted = 0
        with urllib.request.urlopen(request, timeout=60) as response:
            for line in response:
                line = line.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or [{}]
                delta = choices[0].get("delta") or {}
                thinking = delta.get("reasoning_content") or ""
                if thinking:
                    yield {"think": thinking}
                content = delta.get("content") or ""
                if not content:
                    continue
                raw += content
                cleaned = _plain_text(raw)
                # Hold back a trailing "*" in case the next delta completes "**".
                show = cleaned[:-1] if cleaned.endswith("*") else cleaned
                if len(show) > emitted:
                    yield {"delta": show[emitted:]}
                    emitted = len(show)
        cleaned = _plain_text(raw)
        if len(cleaned) > emitted:
            yield {"delta": cleaned[emitted:]}

    return stream


@dataclass
class DeploymentPlan:
    repo: str
    mode: str
    allowed_command: list[str]
    warnings: list[str]
    requires_confirmation: bool = True

    def to_dict(self):
        return asdict(self)


class LocalDeploymentManager:
    """Generate and optionally execute only known, reviewable commands."""

    def plan(self, repo: str | Path) -> dict[str, Any]:
        root = Path(repo).resolve()
        if not root.is_dir():
            raise ValueError("repository directory does not exist")
        stack, _, inferred = _detect_stack(root)
        warnings = ["首次部署默认只生成预览，不执行命令。", "请先检查依赖来源、许可证、端口和环境变量。"]
        if "Docker" in stack:
            warnings.append("容器可能访问网络或挂载目录；需要维护者明确确认。")
        if inferred:
            command = inferred
        else:
            command = ["manual-review-required"]
        return DeploymentPlan(str(root), "preview", command, warnings).to_dict()

    def execute(self, repo: str | Path, *, confirm: bool = False) -> dict[str, Any]:
        plan = self.plan(repo)
        if not confirm:
            return {"status": "preview", "plan": plan}
        if os.getenv("OSG_DEPLOY_ENABLE", "0").lower() not in {"1", "true", "yes"}:
            return {"status": "blocked", "reason": "OSG_DEPLOY_ENABLE is not enabled", "plan": plan}
        command = plan["allowed_command"]
        if command == ["manual-review-required"]:
            return {"status": "blocked", "reason": "no safe allowlisted command detected", "plan": plan}
        completed = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=300)
        return {"status": "completed" if completed.returncode == 0 else "failed", "plan": plan, "return_code": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
