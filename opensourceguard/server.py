"""Dependency-free local HTTP server for the maintainer demo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .autonomy import AutonomousMaintainer, AutonomyPolicy, AutonomyStore, manual_issue_event, normalize_issue_event, verify_signature
from .beginner import BeginnerAgent, LocalDeploymentManager, compatible_model_callback_from_env, compatible_model_stream_from_env, recommend_projects, topics_for_interests
from .discovery import feed_from_env, fetch_github_hot, run_refresh_loop
from .health import build_readiness_report, render_report_html, score_health
from .license_audit import audit_licenses
from .organize import apply_organization, plan_organization
from .readme_review import REWRITE_PROMPT, review_readme
from .providers import DraftProvider
from .security import scan_repository
from .reporting import build_report, render_markdown
from .integrations import build_provider_plan, provider_capabilities
from .iteration import iterate_repair
from .runtime import runtime_plan, trial_run
from .supplychain import supplychain_scan
from .audit import summarize_audit
from .open_source import assess_open_source, complete_open_source, publish_preview
from .remote import inspect_repository_url, deployment_plan
from .tooling import ToolRegistry
from .publishing import connect_platform, publish_project
from .laya_router import LayaRouter
from .openviking_context import OpenVikingContext

ROOT = Path(__file__).resolve().parent.parent

# Optional local decision router (Laya typed-decision + keyword rules).
# Safe fallback keeps the main path dependency-free when laya is absent.
_laya_router = LayaRouter()
# Optional OpenViking context database (long-term Agent memory).
# Reads OSG_OPENVIKING_URL / OSG_OPENVIKING_API_KEY from the environment.
_openviking = OpenVikingContext()

_INSPECT_PROMPT = ("你是开源项目解读助手。根据用户给出的仓库元数据和 README 节选，用中文面向初学者输出一段详细解读，"
    "依次讲清楚四点：1）这个项目是做什么的、解决什么问题；2）核心功能或亮点，列 2 到 4 条；3）适合什么人用、上手需要什么基础；"
    "4）结合给出的许可证说明商用与使用注意。只能输出纯文本，不要任何 Markdown 标记（包括 **、#、反引号、- 列表符号），"
    "四个部分之间换行分隔，每部分用「一、」「二、」「三、」「四、」开头。")


def make_handler(maintainer: AutonomousMaintainer, allowed_root: Path, feed=None):
    beginner = BeginnerAgent(compatible_model_callback_from_env(), compatible_model_stream_from_env())
    inspect_callback = compatible_model_callback_from_env(_INSPECT_PROMPT)
    tools = ToolRegistry(beginner)
    deployer = LocalDeploymentManager()
    discovery_feed = feed if feed is not None else feed_from_env()

    def safe_repo(value: str | None) -> Path:
        candidate = (allowed_root / (value or ".")).resolve() if not Path(value or ".").is_absolute() else Path(value).resolve()
        if candidate != allowed_root and allowed_root not in candidate.parents:
            raise ValueError("repository path is outside OSG_REPO_ROOT")
        if not candidate.is_dir():
            raise ValueError("repository directory does not exist")
        return candidate

    _BROWSE_SKIP = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv", ".idea", ".vscode", ".pytest_cache", ".mypy_cache", "dist", "build"}

    def browse_dirs(value: str | None) -> dict:
        """List subdirectories inside OSG_REPO_ROOT for the visual path picker."""
        base = safe_repo(value)
        if base == allowed_root:
            rel, parent = ".", None
        else:
            rel = base.relative_to(allowed_root).as_posix()
            parent = base.parent.relative_to(allowed_root).as_posix() if base.parent != allowed_root else "."
        dirs = []
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith(".") or child.name in _BROWSE_SKIP:
                continue
            signals = []
            if (child / ".git").exists():
                signals.append("git")
            if any(child.glob("README*")):
                signals.append("readme")
            if any((child / f).exists() for f in ("requirements.txt", "pyproject.toml", "package.json")):
                signals.append("deps")
            dirs.append({"name": child.name, "rel": (rel + "/" + child.name) if rel != "." else child.name, "signals": signals})
        return {"path": rel, "parent": parent, "dirs": dirs, "root": allowed_root.name or str(allowed_root)}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def _send(self, status: int, data: dict, content_type: str = "application/json"):
            body = json.dumps(data, ensure_ascii=False).encode() if content_type == "application/json" else str(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _chunk(self, data: bytes):
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")

        def _stream_ndjson(self, events, final_base: dict):
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            answer = ""
            thinking = ""
            try:
                for event in events:
                    if isinstance(event, str):
                        event = {"delta": event}
                    if event.get("think"):
                        thinking += event["think"]
                    if event.get("delta"):
                        answer += event["delta"]
                    self._chunk((json.dumps(event, ensure_ascii=False) + "\n").encode())
                final = {**final_base, "done": True, "answer": answer}
                if thinking:
                    final["thinking"] = thinking
                self._chunk((json.dumps(final, ensure_ascii=False) + "\n").encode())
            except Exception as exc:
                try:
                    self._chunk((json.dumps({"done": True, "error": f"模型调用失败：{exc}"}, ensure_ascii=False) + "\n").encode())
                except OSError:
                    pass
            finally:
                try:
                    self._chunk(b"")
                except OSError:
                    pass

        def _stream_ask(self, repo: Path, question: str, context: dict, actions: list):
            final = {"source": "model+local-context" if beginner.model_stream else "local-analysis", "repository": repo.name, "context": context}
            if actions:
                final["actions"] = actions
            self._stream_ndjson(beginner.ask_stream(repo, question, context), final)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in {"/", "/index.html", "/agent", "/agent/", "/demo/agent.html"}:
                body = (ROOT / "demo" / "agent.html").read_bytes()
                self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Cache-Control", "no-store"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            if path == "/health": return self._send(200, {"status": "ok", "version": "0.6.0"})
            if path == "/autonomy/status": return self._send(200, {"policy": maintainer.policy.__dict__ | {"blocked_labels": sorted(maintainer.policy.blocked_labels)}, "counts": maintainer.store.counts()})
            if path == "/autonomy/jobs": return self._send(200, {"jobs": maintainer.store.list_jobs()})
            if path == "/api/integrations/capabilities": return self._send(200, provider_capabilities())
            if path == "/api/agent/tools": return self._send(200, {"tools": tools.describe()})
            if path == "/api/discovery": return self._send(200, discovery_feed.get(force="refresh=1" in self.path))
            self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= length <= 512 * 1024:
                return self._send(413, {"error": "invalid content length"})
            body = self.rfile.read(length) if length else b""
            if self.path in {"/api/beginner/explain", "/api/beginner/ask", "/api/deploy/preview", "/api/deploy/run", "/api/security/scan", "/api/report/export", "/api/integrations/preview", "/api/repair/iterate", "/api/runtime/plan", "/api/trial/run", "/api/supplychain/scan", "/api/audit/summary", "/api/open-source/assess", "/api/open-source/complete", "/api/open-source/publish", "/api/repository/inspect", "/api/repository/deploy-preview", "/api/platform/connect", "/api/platform/repos", "/api/platform/publish", "/api/agent/tool-call", "/api/health/score", "/api/license/audit", "/api/readme/review", "/api/report/html", "/autonomy/enqueue", "/api/beginner/recommend", "/api/fs/browse", "/api/organize/plan", "/api/organize/apply", "/api/laya/route", "/api/laya/health", "/api/openviking/health", "/api/openviking/memory", "/api/openviking/resource"}:
                try:
                    payload = json.loads(body)
                    if self.path == "/api/laya/route":
                        text = str(payload.get("text") or "").strip()
                        if not text:
                            return self._send(400, {"error": "text is required"})
                        return self._send(200, _laya_router.decide(text, payload.get("state") or {}))
                    if self.path == "/api/laya/health":
                        return self._send(200, _laya_router.health())
                    if self.path == "/api/openviking/health":
                        return self._send(200, _openviking.health())
                    if self.path == "/api/openviking/memory":
                        text = str(payload.get("text") or "").strip()
                        if not text:
                            return self._send(400, {"error": "text is required"})
                        return self._send(200, _openviking.store_memory(text))
                    if self.path == "/api/openviking/resource":
                        file_path = str(payload.get("file_path") or "")
                        resource_to = str(payload.get("to") or "")
                        if not file_path or not resource_to:
                            return self._send(400, {"error": "file_path and to are required"})
                        return self._send(200, _openviking.store_resource(file_path, resource_to, reason=str(payload.get("reason") or "Project documentation")))
                    if self.path == "/api/fs/browse":
                        return self._send(200, browse_dirs(payload.get("path")))
                    if self.path == "/api/organize/plan":
                        return self._send(200, plan_organization(safe_repo(payload.get("repo")), model_callback=beginner.model_callback))
                    if self.path == "/api/organize/apply":
                        return self._send(200, apply_organization(safe_repo(payload.get("repo")), append_gitignore=bool(payload.get("append_gitignore", True))))
                    if self.path == "/api/agent/tool-call":
                        return self._send(200, tools.call(payload.get("name", ""), payload.get("arguments") or {}))
                    if self.path in {"/api/platform/connect", "/api/platform/repos"}:
                        return self._send(200, connect_platform(payload.get("platform", "github"), payload.get("token", "")))
                    if self.path == "/api/repository/inspect":
                        return self._send(200, inspect_repository_url(payload.get("url", ""), model_callback=inspect_callback))
                    if self.path == "/api/repository/deploy-preview":
                        return self._send(200, deployment_plan(payload.get("url", "")))
                    if self.path == "/autonomy/enqueue":
                        # Manual entry is an explicit user action, so it bypasses
                        # policy.enabled; risk labels are still enforced in run_next.
                        event = manual_issue_event(payload.get("title", ""), payload.get("body", ""), labels=payload.get("labels") or [], repository=payload.get("repository", "local"))
                        job_id, inserted = maintainer.store.enqueue(event)
                        response = {"accepted": True, "job_id": job_id, "deduplicated": not inserted, "status": "queued"}
                        if maintainer.policy.risk_blocked(event):
                            response["warning"] = "该问题带有高风险标签，运行时将自动转人工处理"
                        return self._send(200, response)
                    if self.path == "/api/beginner/recommend":
                        # Best-effort: cached trending feed plus a targeted topic fetch;
                        # a failed targeted fetch never blocks the recommendation.
                        interests = str(payload.get("interests") or "").strip()
                        if not interests:
                            return self._send(400, {"error": "interests is required"})
                        feed_payload = discovery_feed.get()
                        items = list(feed_payload.get("items") or [])
                        topics = topics_for_interests(interests)
                        if topics:
                            try:
                                items.extend(fetch_github_hot(topics=topics, per_topic=3))
                            except Exception:  # noqa: BLE001 - trending cache still covers us
                                pass
                        result = recommend_projects(interests, items, model_callback=beginner.model_callback)
                        if feed_payload.get("warning"):
                            result["warning"] = feed_payload["warning"]
                        return self._send(200, result)
                    if self.path == "/api/integrations/preview":
                        return self._send(200, build_provider_plan(payload.get("platform"), payload.get("repository", ""), use_fork=bool(payload.get("use_fork")), remote_write=False))
                    if self.path == "/api/repair/iterate":
                        return self._send(200, iterate_repair(payload.get("issue", ""), payload.get("failure_log", ""), attempt=int(payload.get("attempt", 1))))
                    if self.path == "/api/audit/summary":
                        return self._send(200, summarize_audit(maintainer.store.list_jobs()))
                    if self.path == "/api/beginner/ask" and payload.get("repo_url"): 
                        remote = inspect_repository_url(payload.get("repo_url"))
                        question = str(payload.get("question", "")).strip()
                        context = payload.get("context") or {}
                        actions = []
                        if any(word in question for word in ("部署", "下载", "运行", "安装")):
                            actions.append({"id": "deploy-preview", "label": "打开部署预览"})
                        if any(word in question for word in ("发布", "上传", "开源社区", "同步仓库")):
                            actions.append({"id": "open-publish", "label": "打开发布向导"})
                        if payload.get("stream"):
                            final = {"source": "model+remote-context" if beginner.model_stream else "remote-repository-context", "repository": remote.get("repository"), "context": context}
                            if actions:
                                final["actions"] = actions
                            return self._stream_ndjson(beginner.ask_remote_stream(remote, question, context), final)
                        result = beginner.ask_remote(remote, question, context)
                        if actions:
                            result["actions"] = actions
                        result["inspection"] = remote
                        return self._send(200, result)
                    repo = safe_repo(payload.get("repo"))
                    if self.path == "/api/beginner/explain":
                        return self._send(200, beginner.explain(repo))
                    if self.path == "/api/beginner/ask":
                        question = str(payload.get("question", "")).strip()
                        actions = []
                        if any(word in question for word in ("补齐", "缺什么", "缺少", "完善")):
                            actions.append({"id": "open-complete", "label": "打开一键补齐"})
                        if any(word in question for word in ("发布", "上传", "开源社区", "同步仓库")):
                            actions.append({"id": "open-publish", "label": "打开发布向导"})
                        if payload.get("stream"):
                            return self._stream_ask(repo, question, payload.get("context") or {}, actions)
                        result = beginner.ask(repo, question, context=payload.get("context") or {})
                        if actions:
                            result["actions"] = actions
                        return self._send(200, result)
                    if self.path == "/api/deploy/preview":
                        return self._send(200, deployer.plan(repo))
                    if self.path == "/api/security/scan":
                        return self._send(200, scan_repository(repo))
                    if self.path == "/api/runtime/plan":
                        return self._send(200, runtime_plan(repo))
                    if self.path == "/api/trial/run":
                        # Trial run executes only allowlisted commands on a
                        # temporary copy; the original repo is never modified.
                        return self._send(200, trial_run(repo, model_callback=beginner.model_callback))
                    if self.path == "/api/supplychain/scan":
                        # OSV query is a read-only public API call; parsing,
                        # SBOM and baseline checks keep working offline.
                        return self._send(200, supplychain_scan(repo, model_callback=beginner.model_callback))
                    if self.path == "/api/health/score":
                        return self._send(200, score_health(repo))
                    if self.path == "/api/license/audit":
                        return self._send(200, audit_licenses(repo))
                    if self.path == "/api/readme/review":
                        rewrite = bool(payload.get("rewrite"))
                        callback = None
                        if rewrite:
                            callback = compatible_model_callback_from_env(REWRITE_PROMPT, plain=False)
                            if callback is None:
                                return self._send(400, {"error": "模型未配置，无法 AI 改写。请检查 .env 中的 OSG_AGENT_* 配置。"})
                        return self._send(200, review_readme(repo, rewrite=rewrite, model_callback=callback))
                    if self.path == "/api/report/html":
                        report = build_readiness_report(repo)
                        return self._send(200, {"html": render_report_html(report), "overall": report["health"]["overall"], "grade": report["health"]["grade"]})
                    if self.path == "/api/open-source/assess":
                        return self._send(200, assess_open_source(repo))
                    if self.path == "/api/open-source/complete":
                        return self._send(200, complete_open_source(repo, confirm=bool(payload.get("confirm"))))
                    if self.path == "/api/open-source/publish":
                        return self._send(200, publish_preview(repo, platform=payload.get("platform", "github"), repository=payload.get("repository", ""), token=payload.get("token", ""), confirm=bool(payload.get("confirm"))))
                    if self.path == "/api/platform/publish":
                        return self._send(200, publish_project(repo, platform=payload.get("platform", "github"), repository=payload.get("repository", ""), token=payload.get("token", ""), description=payload.get("description", ""), confirm=bool(payload.get("confirm"))))
                    if self.path == "/api/report/export":
                        report = build_report(repo, scan=scan_repository(repo), status={"counts": maintainer.store.counts()}, jobs=maintainer.store.list_jobs())
                        return self._send(200, {"report": report, "markdown": render_markdown(report)})
                    return self._send(200, deployer.execute(repo, confirm=bool(payload.get("confirm"))))
                except (ValueError, json.JSONDecodeError) as exc:
                    return self._send(400, {"error": str(exc)})
            if self.path == "/github/webhook":
                secret = os.getenv("OSG_GITHUB_WEBHOOK_SECRET", "")
                # Signature verification is intentionally before JSON parsing.
                if not verify_signature(secret, body, self.headers.get("X-Hub-Signature-256")):
                    return self._send(401, {"error": "invalid signature"})
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    return self._send(400, {"error": "invalid JSON"})
                event = normalize_issue_event(payload, event_name=self.headers.get("X-GitHub-Event", "issues"), delivery_id=self.headers.get("X-GitHub-Delivery"))
                if event is None:
                    return self._send(202, {"accepted": False, "reason": "event ignored"})
                return self._send(202, maintainer.accept(event))
            if self.path == "/autonomy/run-next":
                return self._send(200, maintainer.run_next())
            self._send(404, {"error": "not found"})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--db", default=os.getenv("OSG_DATABASE", "runtime/opensourceguard.sqlite3"))
    args = parser.parse_args()
    store = AutonomyStore(args.db)
    policy = AutonomyPolicy.from_env()
    allowed_root = Path(os.getenv("OSG_REPO_ROOT", os.getcwd())).resolve()
    maintainer = AutonomousMaintainer(store, policy, provider=DraftProvider(token=os.getenv("OSG_GITHUB_TOKEN"), remote_write=policy.remote_write))
    discovery_feed = feed_from_env()
    if os.getenv("OSG_DISCOVERY_DISABLED") != "1":
        threading.Thread(target=run_refresh_loop, args=(discovery_feed,), daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(maintainer, allowed_root, discovery_feed))
    print(f"OpenSourceGuard 0.6.0 http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close(); store.close()


if __name__ == "__main__":
    main()
