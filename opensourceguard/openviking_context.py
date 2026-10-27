"""Optional OpenViking context-database adapter for OpenSourceGuard.

OpenViking is a context database for AI agents (Resources / Memories /
Skills). This adapter gives the workbench a long-term, cross-session memory:

- ``store_memory(text)`` writes a short conversation memory (session -> message
  -> commit) so later sessions can recall what was discussed.
- ``store_resource(file, to, reason)`` uploads a local file as a resource
  (e.g. a project README or audit report) that can later be retrieved.
- ``list_resources()`` lists resources the account can retrieve.

It is implemented with the Python standard library only (urllib), and reads
credentials from the environment (``OSG_OPENVIKING_URL`` /
``OSG_OPENVIKING_API_KEY``). When the key/URL is absent, every method degrades
to a safe ``{"enabled": False, ...}`` result -- the optional enhancement never
breaks the main dependency-free path.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


def _env_url() -> str:
    return (os.environ.get("OSG_OPENVIKING_URL") or "").strip().rstrip("/")


def _env_key() -> str:
    return (os.environ.get("OSG_OPENVIKING_API_KEY") or "").strip()


class OpenVikingContext:
    """Minimal OpenViking REST client (stdlib only, safe fallbacks)."""

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        self.url = (url or _env_url()).rstrip("/")
        self.api_key = api_key or _env_key()

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.api_key)

    def _headers(self, content_type: bool = True) -> Dict[str, str]:
        headers = {"Authorization": "Bearer " + self.api_key, "User-Agent": "OpenSourceGuard"}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    def _post_json(self, path: str, payload: Dict[str, Any], timeout: float = 120.0) -> Dict[str, Any]:
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode("utf-8"), method="POST", headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _get_json(self, path: str, timeout: float = 60.0) -> Dict[str, Any]:
        req = urllib.request.Request(self.url + path, method="GET", headers=self._headers(content_type=False))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def health(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "reason": "OSG_OPENVIKING_URL/API_KEY not configured", "mode": "disabled"}
        try:
            # A lightweight write that proves auth + reachability without side effects.
            session = self._post_json("/api/v1/sessions", {}, timeout=60.0)
            sid = (session.get("result") or {}).get("session_id")
            return {"enabled": True, "mode": "openviking", "session_id": sid, "status": "ok"}
        except urllib.error.HTTPError as e:
            return {"enabled": True, "mode": "openviking", "status": "error", "http": e.code, "detail": e.read().decode("utf-8", "replace")[:300]}
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "mode": "openviking", "status": "error", "detail": str(exc)[:300]}

    def store_memory(self, text: str, timeout: float = 360.0) -> Dict[str, Any]:
        """Write a short-term memory by session -> message -> commit."""
        if not self.enabled:
            return {"enabled": False, "reason": "not configured", "ok": False}
        if not (text or "").strip():
            return {"enabled": True, "ok": False, "error": "empty text"}
        try:
            session = self._post_json("/api/v1/sessions", {}, timeout=timeout)
            session_id = (session.get("result") or {}).get("session_id")
            if not session_id:
                return {"enabled": True, "ok": False, "result": session}
            self._post_json(
                f"/api/v1/sessions/{session_id}/messages",
                {"role": "user", "parts": [{"type": "text", "text": text}]},
                timeout=timeout,
            )
            committed = self._post_json(
                f"/api/v1/sessions/{session_id}/commit",
                {"telemetry": False},
                timeout=timeout,
            )
            task_id = (committed.get("result") or {}).get("task_id")
            status = "committed"
            if task_id:
                status = self._wait_task(task_id)
            return {"enabled": True, "ok": True, "session_id": session_id, "status": status, "task_id": task_id}
        except urllib.error.HTTPError as e:
            return {"enabled": True, "ok": False, "error": f"http:{e.code}", "detail": e.read().decode("utf-8", "replace")[:300]}
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}

    def store_resource(self, file_path: str, resource_to: str, reason: str = "Project documentation", timeout: float = 120.0) -> Dict[str, Any]:
        """Upload a local file as an OpenViking resource (temp_upload -> resource)."""
        if not self.enabled:
            return {"enabled": False, "reason": "not configured", "ok": False}
        p = Path(file_path)
        if not p.is_file():
            return {"enabled": True, "ok": False, "error": "file not found", "path": str(p)}
        try:
            import mimetypes

            upload_headers = {"Authorization": "Bearer " + self.api_key, "User-Agent": "OpenSourceGuard"}
            boundary = "----osg" + str(int(time.time() * 1000))
            ctype, _ = mimetypes.guess_type(str(p))
            ctype = ctype or "application/octet-stream"
            data = p.read_bytes()
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{p.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
            upload_headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            req = urllib.request.Request(self.url + "/api/v1/resources/temp_upload", data=body, method="POST", headers=upload_headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                upload = json.loads(r.read().decode("utf-8"))
            temp_file_id = (upload.get("result") or {}).get("temp_file_id")
            if not temp_file_id:
                return {"enabled": True, "ok": False, "error": "no temp_file_id", "result": upload}
            created = self._post_json(
                "/api/v1/resources",
                {"temp_file_id": temp_file_id, "source_name": p.name, "to": resource_to, "reason": reason},
                timeout=timeout,
            )
            task_id = (created.get("result") or {}).get("task_id")
            status = "created"
            if task_id:
                status = self._wait_task(task_id)
            return {"enabled": True, "ok": True, "to": resource_to, "temp_file_id": temp_file_id, "status": status, "task_id": task_id, "result": created.get("result")}
        except urllib.error.HTTPError as e:
            return {"enabled": True, "ok": False, "error": f"http:{e.code}", "detail": e.read().decode("utf-8", "replace")[:300]}
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}

    def list_resources(self, timeout: float = 60.0) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "reason": "not configured", "ok": False}
        try:
            result = self._get_json("/api/v1/resources", timeout=timeout)
            return {"enabled": True, "ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            return {"enabled": True, "ok": False, "error": type(exc).__name__, "detail": str(exc)[:300]}

    def _wait_task(self, task_id: str, timeout: float = 240.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                result = self._get_json(f"/api/v1/tasks/{task_id}", timeout=60.0)
            except Exception:  # noqa: BLE001
                result = {}
            status = ((result.get("result") or {}).get("status")) or ""
            if status in ("completed", "failed", "cancelled"):
                return status
            time.sleep(2.0)
        return "timeout"


if __name__ == "__main__":
    import sys

    ctx = OpenVikingContext()
    print(json.dumps(ctx.health(), ensure_ascii=False))
    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        print(json.dumps(ctx.store_memory("OpenSourceGuard 测试记忆：Agent 上下文连续性验证"), ensure_ascii=False))
