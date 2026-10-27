"""Token based repository management and publishing adapters.

The browser only sends a token for the current action.  This module never
persists credentials and never puts them in a git remote URL.  GitHub uses its
REST API for account/repository management and an ephemeral ``GIT_ASKPASS``
helper for the final push.  ModelScope is supported through the official
``modelscope_hub`` client when it is installed, which keeps this package
dependency-free for the local dry-run experience.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .open_source import assess_open_source


class PlatformError(ValueError):
    """A user-facing platform or credential error."""


def _mask(token: str) -> str:
    value = str(token or "")
    if len(value) <= 8:
        return "••••••••" if value else "未配置"
    return value[:4] + "••••" + value[-3:]


def _safe_message(text: str, token: str = "") -> str:
    message = str(text or "")
    if token:
        message = message.replace(token, "[已隐藏 Token]")
    return message[:700]


def _request_json(
    url: str,
    *,
    token: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
) -> Any:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "OpenSourceGuard/0.6",
    }
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2_000_000)
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(4000).decode("utf-8", "replace")
        except Exception:
            pass
        raise PlatformError(f"平台返回 {exc.code}：{_safe_message(detail or exc.reason, token)}") from exc
    except urllib.error.URLError as exc:
        raise PlatformError(f"暂时无法连接平台：{_safe_message(exc.reason, token)}") from exc


def _require_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        raise PlatformError("请先填写平台 Token")
    if len(value) < 8:
        raise PlatformError("Token 长度看起来不完整，请重新粘贴")
    return value


def _repo_item(item: dict[str, Any], platform: str, current_user: str = "") -> dict[str, Any]:
    owner = item.get("owner") or {}
    owner_name = owner.get("login") if isinstance(owner, dict) else owner
    name = item.get("name") or item.get("repo_name") or item.get("model_name") or item.get("id") or "未命名仓库"
    full_name = item.get("full_name") or item.get("repo_id") or (f"{owner_name}/{name}" if owner_name else name)
    visibility = item.get("visibility")
    if visibility is None:
        visibility = "private" if item.get("private") else "public"
    return {
        "platform": platform,
        "name": str(name),
        "full_name": str(full_name),
        "description": str(item.get("description") or item.get("chinese_name") or ""),
        "visibility": str(visibility),
        "url": item.get("html_url") or item.get("url") or item.get("repo_url") or "",
        "updated_at": item.get("updated_at") or item.get("last_modified") or "",
        "owner": str(owner_name or current_user or ""),
    }


def _github_connect(token: str) -> dict[str, Any]:
    user = _request_json("https://api.github.com/user", token=token)
    login = str(user.get("login") or "")
    if not login:
        raise PlatformError("GitHub Token 有效，但没有读取到账号信息")
    repos: list[dict[str, Any]] = []
    # /user/repos includes private repositories and organization repositories the
    # token can see.  We fetch a bounded number of pages to avoid a UI hang.
    for page in range(1, 51):
        data = _request_json(
            f"https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner,collaborator,organization_member&sort=updated",
            token=token,
        )
        if not isinstance(data, list):
            break
        repos.extend(_repo_item(item, "github", login) for item in data if isinstance(item, dict))
        if len(data) < 100:
            break
    return {
        "status": "connected",
        "platform": "github",
        "user": {"login": login, "name": user.get("name") or login, "avatar_url": user.get("avatar_url", "")},
        "token": _mask(token),
        "repositories": repos,
        "repository_count": len(repos),
        "capabilities": ["读取仓库", "创建仓库", "上传项目", "同步仓库列表"],
    }


def _modelscope_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return {k: _modelscope_value(v) for k, v in vars(value).items() if not k.startswith("_")}
    return str(value)


def _modelscope_connect(token: str) -> dict[str, Any]:
    try:
        from modelscope_hub import HubApi  # type: ignore
    except ImportError as exc:
        raise PlatformError(
            "ModelScope 连接器尚未安装。执行 `pip install modelscope-hub` 后即可使用，当前 Token 未被保存。"
        ) from exc
    try:
        api = HubApi(token=token)
        user_obj = api.whoami()
        user = _modelscope_value(user_obj)
        username = str(
            (user.get("username") if isinstance(user, dict) else "")
            or (user.get("name") if isinstance(user, dict) else "")
            or getattr(user_obj, "username", "")
            or ""
        )
        if not username:
            raise PlatformError("ModelScope Token 有效，但没有读取到账号信息")
        repositories: list[dict[str, Any]] = []
        # Official SDK exposes the same listing operation for model/dataset and
        # newer repo types.  A missing type is harmless and keeps older SDKs
        # compatible.
        for repo_type in ("model", "dataset", "studio"):
            try:
                result = api.list_repos(repo_type=repo_type, owner=username, page_size=100)
            except TypeError:
                result = api.list_repos(repo_type=repo_type, owner=username)
            except Exception:
                continue
            values = result if isinstance(result, list) else getattr(result, "items", result)
            if isinstance(values, dict):
                values = values.get("items") or values.get("data") or []
            if not isinstance(values, (list, tuple)):
                values = []
            for item in values:
                data = _modelscope_value(item)
                if isinstance(data, dict):
                    data.setdefault("repo_type", repo_type)
                    repositories.append(_repo_item(data, "modelscope", username))
        return {
            "status": "connected",
            "platform": "modelscope",
            "user": {"login": username, "name": username},
            "token": _mask(token),
            "repositories": repositories,
            "repository_count": len(repositories),
            "capabilities": ["读取仓库", "创建模型仓库", "上传项目", "同步仓库列表"],
        }
    except PlatformError:
        raise
    except Exception as exc:
        raise PlatformError(f"ModelScope 连接失败：{_safe_message(exc, token)}") from exc


def connect_platform(platform: str, token: str) -> dict[str, Any]:
    """Validate a token and return a safe account/repository snapshot."""
    value = _require_token(token)
    name = str(platform or "github").strip().lower()
    if name == "github":
        return _github_connect(value)
    if name == "modelscope":
        return _modelscope_connect(value)
    raise PlatformError("当前支持 GitHub 和 ModelScope")


def _github_repo(login: str, repository: str) -> tuple[str, str]:
    raw = str(repository or "").strip().strip("/")
    if not raw:
        raise PlatformError("请填写远程仓库名")
    parts = raw.split("/")
    if len(parts) == 1:
        return login, parts[0]
    if len(parts) == 2 and parts[0].lower() == login.lower():
        return login, parts[1]
    raise PlatformError("为了避免误推送，只允许推送到 Token 所属账号下的仓库")


def _safe_repo_name(value: str) -> str:
    name = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name):
        raise PlatformError("仓库名只能包含字母、数字、点、下划线和短横线")
    return name


def _git_run(args: list[str], root: Path, *, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=root, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def _git_push(root: Path, *, owner: str, name: str, token: str) -> dict[str, Any]:
    if not (root / ".git").exists():
        result = _git_run(["git", "init"], root)
        if result.returncode != 0:
            raise PlatformError(f"初始化 Git 失败：{_safe_message(result.stderr)}")
    _git_run(["git", "config", "user.name", "OpenSourceGuard Agent"], root)
    _git_run(["git", "config", "user.email", "agent@opensourceguard.local"], root)
    remote = f"https://github.com/{owner}/{name}.git"
    existing = _git_run(["git", "remote"], root)
    if "origin" in existing.stdout.split():
        _git_run(["git", "remote", "set-url", "origin", remote], root)
    else:
        add = _git_run(["git", "remote", "add", "origin", remote], root)
        if add.returncode != 0:
            raise PlatformError(f"设置远程仓库失败：{_safe_message(add.stderr)}")
    add = _git_run(["git", "add", "-A"], root)
    if add.returncode != 0:
        raise PlatformError(f"准备文件失败：{_safe_message(add.stderr)}")
    status = _git_run(["git", "status", "--porcelain"], root)
    if status.stdout.strip():
        commit = _git_run(["git", "commit", "-m", "chore: publish with OpenSourceGuard"], root)
        if commit.returncode != 0:
            raise PlatformError(f"创建提交失败：{_safe_message(commit.stderr)}")
    _git_run(["git", "branch", "-M", "main"], root)
    askpass = tempfile.NamedTemporaryFile("w", delete=False, prefix="osg-askpass-", suffix=".py")
    try:
        askpass.write(
            "import sys\n"
            f"token = {token!r}\n"
            f"user = {owner!r}\n"
            "prompt = (sys.argv[1] if len(sys.argv) > 1 else '').lower()\n"
            "print(user if 'username' in prompt else token)\n"
        )
        askpass.close()
        os.chmod(askpass.name, 0o700)
        env = os.environ.copy()
        env.update({"GIT_ASKPASS": askpass.name, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"})
        push = _git_run(["git", "push", "-u", "origin", "main"], root, env=env, timeout=600)
        if push.returncode != 0:
            raise PlatformError(f"推送失败：{_safe_message(push.stderr or push.stdout, token)}")
    finally:
        try:
            Path(askpass.name).unlink(missing_ok=True)
        except OSError:
            pass
    return {"status": "published", "platform": "github", "repository": f"{owner}/{name}", "url": f"https://github.com/{owner}/{name}", "commit": "chore: publish with OpenSourceGuard"}


def _modelscope_publish(root: Path, *, token: str, repository: str, description: str = "") -> dict[str, Any]:
    try:
        from modelscope_hub import HubApi  # type: ignore
    except ImportError as exc:
        raise PlatformError("ModelScope 连接器尚未安装。执行 `pip install modelscope-hub` 后重试。") from exc
    api = HubApi(token=token)
    user_obj = api.whoami()
    user = _modelscope_value(user_obj)
    username = str((user.get("username") if isinstance(user, dict) else "") or getattr(user_obj, "username", ""))
    if not username:
        raise PlatformError("没有读取到 ModelScope 用户名")
    raw = str(repository or root.name).strip().strip("/")
    parts = raw.split("/")
    if len(parts) == 1:
        repo_id = f"{username}/{_safe_repo_name(parts[0])}"
    elif len(parts) == 2 and parts[0].lower() == username.lower():
        repo_id = f"{username}/{_safe_repo_name(parts[1])}"
    else:
        raise PlatformError("为了避免误推送，只允许推送到 Token 所属账号下的 ModelScope 仓库")
    try:
        exists = bool(api.repo_exists(repo_id, repo_type="model"))
    except TypeError:
        exists = bool(api.repo_exists(repo_id))
    if not exists:
        api.create_repo(repo_id, repo_type="model", visibility="public", license="mit", description=description or root.name)
    api.upload_folder(repo_id, "model", str(root), path_in_repo="", commit_message="chore: publish with OpenSourceGuard")
    return {"status": "published", "platform": "modelscope", "repository": repo_id, "url": f"https://www.modelscope.cn/models/{repo_id}", "commit": "chore: publish with OpenSourceGuard"}


def publish_project(
    repo: str | Path,
    *,
    platform: str,
    repository: str = "",
    token: str = "",
    description: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Return a readable plan, or perform a confirmed platform publish."""
    root = Path(repo).resolve()
    if not root.is_dir():
        raise PlatformError("repository directory does not exist")
    value = _require_token(token)
    assessment = assess_open_source(root)
    if not assessment["can_open_source"]:
        return {"status": "blocked", "reason": "请先补齐开源必需项", "assessment": assessment}
    platform_name = str(platform or "github").strip().lower()
    if platform_name not in {"github", "modelscope"}:
        raise PlatformError("当前支持 GitHub 和 ModelScope")
    if platform_name == "github":
        account = _github_connect(value)
        owner = account["user"]["login"]
        _, name = _github_repo(owner, repository or root.name)
        name = _safe_repo_name(name)
        plan = {
            "status": "preview",
            "platform": "github",
            "repository": f"{owner}/{name}",
            "url": f"https://github.com/{owner}/{name}",
            "credential_status": account["token"],
            "steps": ["检查开源清单", "创建或复用远程仓库", "生成本地提交", "安全推送到 main 分支", "返回仓库链接"],
            "repository_exists": any(x["full_name"].lower() == f"{owner}/{name}".lower() for x in account["repositories"]),
            "assessment": assessment,
            "requires_confirmation": True,
        }
        if not confirm:
            return plan
        if not plan["repository_exists"]:
            created = _request_json(
                "https://api.github.com/user/repos",
                token=value,
                method="POST",
                payload={"name": name, "description": description or root.name, "private": False, "auto_init": False},
            )
            plan["created"] = True
            plan["url"] = created.get("html_url") or plan["url"]
        result = _git_push(root, owner=owner, name=name, token=value)
        result["assessment"] = assessment
        return result
    # ModelScope plan and confirmed upload use the official optional client.
    account = _modelscope_connect(value)
    username = account["user"]["login"]
    raw = str(repository or root.name).strip().strip("/")
    name = raw.split("/", 1)[-1]
    name = _safe_repo_name(name)
    repo_id = f"{username}/{name}"
    plan = {
        "status": "preview",
        "platform": "modelscope",
        "repository": repo_id,
        "url": f"https://www.modelscope.cn/models/{repo_id}",
        "credential_status": account["token"],
        "steps": ["检查开源清单", "创建或复用 ModelScope 模型仓库", "上传项目文件", "返回仓库链接"],
        "repository_exists": any(x["full_name"].lower() == repo_id.lower() for x in account["repositories"]),
        "assessment": assessment,
        "requires_confirmation": True,
    }
    if not confirm:
        return plan
    result = _modelscope_publish(root, token=value, repository=repo_id, description=description)
    result["assessment"] = assessment
    return result

