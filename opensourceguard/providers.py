"""Reviewable remote-provider adapter with a safe dry-run default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class DraftProvider:
    platform: str = "github"
    token: str | None = None
    remote_write: bool = False

    def create_draft_pr(self, event: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
        title = f"修复：{event.get('title', 'Issue')}"
        if not self.remote_write:
            return {"status": "dry-run", "platform": self.platform, "title": title, "reason": "remote writes disabled"}
        if not self.token:
            return {"status": "blocked", "platform": self.platform, "title": title, "reason": "provider token missing"}
        # A real provider implementation belongs behind an installed GitHub/
        # GitLab connector. Never silently turn on direct main-branch writes.
        return {"status": "ready-for-provider", "platform": self.platform, "title": title, "draft": True, "auto_merge": False}
