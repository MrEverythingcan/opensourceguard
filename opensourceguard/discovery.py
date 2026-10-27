"""Trending open-source project discovery from GitHub and ModelScope.

Fetches hot repositories on a schedule, normalizes both platforms into a
single item shape, and caches results on disk so the workbench page loads
instantly. If a refresh fails, the previous cache keeps being served with a
``stale`` flag instead of an error.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

GITHUB_TOPICS = ("llm", "ai-agents", "mcp")
DEFAULT_TTL_HOURS = 6.0
_HEADERS = {"User-Agent": "OpenSourceGuard/0.6", "Accept": "application/json"}


def _clip(text: str, limit: int = 140) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _http_json(url: str, *, body: dict | None = None, token: str = "", timeout: int = 10) -> dict:
    headers = dict(_HEADERS)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="PUT" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fetch_github_hot(topics: tuple[str, ...] = GITHUB_TOPICS, per_topic: int = 4, token: str = "") -> list[dict[str, Any]]:
    """Fetch top-starred repositories for each topic via the GitHub search API."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for topic in topics:
        url = f"https://api.github.com/search/repositories?q=topic:{topic}+stars:%3E500&sort=stars&order=desc&per_page={per_topic}"
        for repo in (_http_json(url, token=token).get("items") or []):
            name = repo.get("full_name") or ""
            if not name or name in seen:
                continue
            seen.add(name)
            items.append({
                "platform": "github",
                "name": name,
                "url": repo.get("html_url") or f"https://github.com/{name}",
                "description": _clip(repo.get("description") or "这个仓库还没有写简介"),
                "stars": int(repo.get("stargazers_count") or 0),
                "downloads": 0,
                "language": repo.get("language") or "",
                "tag": topic,
            })
    return items


def fetch_modelscope_hot(page_size: int = 6) -> list[dict[str, Any]]:
    """Fetch hot models via the ModelScope open ranking endpoint."""
    body = {"PageSize": page_size, "PageNumber": 1, "SortBy": "Default", "Query": "", "SingleCriterion": []}
    data = _http_json("https://modelscope.cn/api/v1/dolphin/models", body=body)
    models = ((data.get("Data") or {}).get("Model") or {}).get("Models") or []
    items: list[dict[str, Any]] = []
    for model in models:
        namespace, name = model.get("Path") or "", model.get("Name") or ""
        if not namespace or not name:
            continue
        tasks = model.get("Tasks") or []
        tag = ""
        if isinstance(tasks, list) and tasks:
            first = tasks[0]
            tag = str(first.get("Name") if isinstance(first, dict) else first)
        items.append({
            "platform": "modelscope",
            "name": f"{namespace}/{name}",
            "url": f"https://modelscope.cn/models/{namespace}/{name}",
            "description": _clip(model.get("Description") or model.get("ChineseName") or "魔搭社区热门模型"),
            "stars": int(model.get("Stars") or 0),
            "downloads": int(model.get("Downloads") or 0),
            "language": "",
            "tag": tag,
        })
    return items


class DiscoveryFeed:
    """TTL-cached trending feed. ``fetcher`` is injectable for tests."""

    def __init__(self, cache_path: str | Path, ttl_hours: float = DEFAULT_TTL_HOURS, github_token: str = "", fetcher: Callable[[], tuple[list, list]] | None = None):
        self.cache_path = Path(cache_path)
        self.ttl_seconds = max(60.0, ttl_hours * 3600)
        self.github_token = github_token
        self._fetcher = fetcher or self._fetch_all
        self._lock = threading.Lock()

    def _fetch_all(self) -> tuple[list[dict[str, Any]], list[str]]:
        items: list[dict[str, Any]] = []
        errors: list[str] = []
        for label, fn in (("GitHub", lambda: fetch_github_hot(token=self.github_token)), ("魔搭", fetch_modelscope_hot)):
            try:
                items.extend(fn())
            except Exception as exc:  # noqa: BLE001 - one platform failing must not block the other
                errors.append(f"{label} 抓取失败：{exc}")
        if not items and errors:
            raise RuntimeError("；".join(errors))
        return items, errors

    def _load_cache(self) -> dict | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None

    def _save_cache(self, items: list[dict[str, Any]]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps({"fetched_at": time.time(), "items": items}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _payload(items: list, fetched_at: float, stale: bool, warning: str = "") -> dict:
        return {
            "items": items,
            "updated_at": fetched_at,
            "updated_at_text": time.strftime("%Y-%m-%d %H:%M", time.localtime(fetched_at)) if fetched_at else "",
            "stale": stale,
            "warning": warning,
        }

    def get(self, force: bool = False) -> dict:
        with self._lock:
            cache = self._load_cache()
            fresh = bool(cache) and (time.time() - float(cache.get("fetched_at", 0)) < self.ttl_seconds)
            if cache and fresh and not force:
                return self._payload(cache["items"], cache["fetched_at"], False)
            try:
                items, errors = self._fetcher()
            except Exception as exc:  # noqa: BLE001 - keep serving stale cache instead of failing the page
                if cache:
                    return self._payload(cache["items"], cache["fetched_at"], True, f"刷新失败（{exc}），当前展示的是上一次缓存")
                return self._payload([], 0, True, f"暂时抓不到推荐：{exc}")
            self._save_cache(items)
            warning = ("；".join(errors) + "，该平台本次缺席") if errors else ""
            return self._payload(items, time.time(), False, warning)

    def refresh(self) -> dict:
        return self.get(force=True)


def feed_from_env() -> DiscoveryFeed:
    raw_ttl = os.getenv("OSG_DISCOVERY_TTL_HOURS", "").strip()
    try:
        ttl = float(raw_ttl) if raw_ttl else DEFAULT_TTL_HOURS
    except ValueError:
        ttl = DEFAULT_TTL_HOURS
    return DiscoveryFeed(
        os.getenv("OSG_DISCOVERY_CACHE", "runtime/discovery_cache.json"),
        ttl_hours=ttl,
        github_token=os.getenv("OSG_GITHUB_TOKEN", ""),
    )


def run_refresh_loop(feed: DiscoveryFeed, interval_seconds: float = 1800.0) -> None:
    """Background loop: refresh whenever the cache turns stale. Runs as a daemon thread."""
    while True:
        try:
            feed.get()
        except Exception:  # noqa: BLE001 - the loop must never die; errors surface via the payload
            pass
        time.sleep(max(300.0, interval_seconds))
