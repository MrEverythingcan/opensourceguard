"""Webhook, queue and autonomous-maintainer policy primitives.

The module deliberately keeps external writes behind an explicit provider and
defaults to a dry-run. A webhook handler must verify the HMAC before parsing
the JSON body; the HTTP server in :mod:`opensourceguard.server` enforces this
ordering.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


RISK_LABELS = frozenset({"security", "critical", "high-risk", "do-not-auto-fix", "needs-human"})


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    """Verify a GitHub-style ``sha256=...`` signature in constant time."""

    if not secret or not signature:
        return False
    supplied = signature.strip()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    if len(supplied) != 64:
        return False
    try:
        bytes.fromhex(supplied)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied)


def normalize_issue_event(payload: Mapping[str, Any], *, event_name: str = "issues", delivery_id: str | None = None) -> dict[str, Any] | None:
    """Convert a GitHub issue webhook into a small stable job record."""

    action = str(payload.get("action", "")).lower()
    if event_name != "issues" or action not in {"opened", "reopened", "edited", "labeled"}:
        return None
    issue = payload.get("issue") or {}
    repo = payload.get("repository") or {}
    labels = [str((label or {}).get("name", "")).lower() for label in issue.get("labels", [])]
    return {
        "delivery_id": delivery_id or str(uuid.uuid4()),
        "event_name": event_name,
        "action": action,
        "repository": repo.get("full_name") or repo.get("name") or "",
        "clone_url": repo.get("clone_url") or repo.get("html_url") or "",
        "issue_number": issue.get("number"),
        "title": issue.get("title", ""),
        "body": issue.get("body") or "",
        "labels": labels,
        "author": ((issue.get("user") or {}).get("login") or ""),
        "html_url": issue.get("html_url") or "",
        "received_at": time.time(),
    }


def manual_issue_event(title: str, body: str = "", labels: Any = (), repository: str = "local") -> dict[str, Any]:
    """Build a stable job record from an issue entered by hand in the UI."""

    title = str(title or "").strip()
    if not title:
        raise ValueError("问题标题不能为空")
    if isinstance(labels, str):
        labels = labels.split(",")
    clean_labels = sorted({str(label).strip().lower() for label in labels if str(label).strip()})
    return {
        "delivery_id": f"manual-{uuid.uuid4()}",
        "event_name": "manual",
        "action": "opened",
        "repository": str(repository or "").strip() or "local",
        "clone_url": "",
        "issue_number": None,
        "title": title,
        "body": str(body or "").strip(),
        "labels": clean_labels,
        "author": "local-user",
        "html_url": "",
        "received_at": time.time(),
    }


@dataclass(frozen=True)
class AutonomyPolicy:
    enabled: bool = False
    execute_mode: str = "skip"
    auto_comment: bool = True
    create_draft_pr: bool = True
    remote_write: bool = False
    max_attempts: int = 2
    blocked_labels: frozenset[str] = RISK_LABELS

    @classmethod
    def from_env(cls) -> "AutonomyPolicy":
        return cls(
            enabled=os.getenv("OSG_AUTONOMY_ENABLED", "0").lower() in {"1", "true", "yes"},
            execute_mode=os.getenv("OSG_AUTONOMY_EXECUTE", "skip"),
            remote_write=os.getenv("OSG_REMOTE_WRITE", "0").lower() in {"1", "true", "yes"},
        )

    def risk_blocked(self, event: Mapping[str, Any]) -> bool:
        return bool(self.blocked_labels.intersection({str(x).lower() for x in event.get("labels", [])}))


class AutonomyStore:
    """SQLite-backed durable queue with delivery deduplication and claims."""

    def __init__(self, path: str | Path = "runtime/opensourceguard.sqlite3") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            """CREATE TABLE IF NOT EXISTS autonomy_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                event_json TEXT NOT NULL,
                result_json TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                claimed_at REAL
            )"""
        )
        self.db.commit()

    def enqueue(self, event: Mapping[str, Any]) -> tuple[int, bool]:
        now = time.time()
        delivery = str(event["delivery_id"])
        cur = self.db.execute(
            "INSERT OR IGNORE INTO autonomy_jobs(delivery_id,status,event_json,created_at,updated_at) VALUES(?,?,?,?,?)",
            (delivery, "queued", json.dumps(dict(event), ensure_ascii=False), now, now),
        )
        self.db.commit()
        row = self.db.execute("SELECT id FROM autonomy_jobs WHERE delivery_id=?", (delivery,)).fetchone()
        return int(row[0]), bool(cur.rowcount)

    def claim_next(self) -> sqlite3.Row | None:
        now = time.time()
        self.db.execute("BEGIN IMMEDIATE")
        row = self.db.execute("SELECT * FROM autonomy_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            self.db.commit()
            return None
        self.db.execute("UPDATE autonomy_jobs SET status='running', attempts=attempts+1, claimed_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
        self.db.commit()
        return self.db.execute("SELECT * FROM autonomy_jobs WHERE id=?", (row["id"],)).fetchone()

    def finish(self, job_id: int, status: str, result: Mapping[str, Any]) -> None:
        self.db.execute("UPDATE autonomy_jobs SET status=?, result_json=?, updated_at=? WHERE id=?", (status, json.dumps(dict(result), ensure_ascii=False), time.time(), job_id))
        self.db.commit()

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute("SELECT id,delivery_id,status,attempts,created_at,updated_at,event_json,result_json FROM autonomy_jobs ORDER BY id DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            event = json.loads(item.pop("event_json") or "{}")
            item["title"] = event.get("title") or ""
            item["labels"] = event.get("labels") or []
            item["repository"] = event.get("repository") or ""
            item["issue_number"] = event.get("issue_number")
            item["source"] = "手动录入" if event.get("event_name") == "manual" else "GitHub Webhook"
            if item.get("result_json"):
                item["result"] = json.loads(item.pop("result_json"))
            else:
                item.pop("result_json", None)
            out.append(item)
        return out

    def counts(self) -> dict[str, int]:
        rows = self.db.execute("SELECT status,COUNT(*) AS n FROM autonomy_jobs GROUP BY status").fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}

    def close(self) -> None:
        self.db.close()


@dataclass
class AutonomousMaintainer:
    store: AutonomyStore
    policy: AutonomyPolicy = field(default_factory=AutonomyPolicy.from_env)
    analyzer: Callable[[Mapping[str, Any], AutonomyPolicy], Mapping[str, Any]] | None = None
    provider: Any = None

    def accept(self, event: Mapping[str, Any]) -> dict[str, Any]:
        if not self.policy.enabled:
            return {"accepted": False, "status": "disabled", "reason": "autonomy disabled"}
        job_id, inserted = self.store.enqueue(event)
        return {"accepted": True, "job_id": job_id, "deduplicated": not inserted, "status": "queued"}

    def run_next(self) -> dict[str, Any]:
        row = self.store.claim_next()
        if row is None:
            return {"status": "empty"}
        event = json.loads(row["event_json"])
        try:
            if self.policy.risk_blocked(event):
                result = {"status": "blocked", "reason": "risk label requires human review", "labels": event.get("labels", [])}
                self.store.finish(row["id"], "blocked", result)
                return {"job_id": row["id"], **result}
            if self.analyzer:
                result = dict(self.analyzer(event, self.policy))
            else:
                result = {"status": "analyzed", "verification_status": "skipped", "next_action": "run analyzer in isolated workspace"}
            if result.get("verification_status") == "verified" and self.policy.create_draft_pr:
                result["draft_pr"] = self.provider.create_draft_pr(event, result) if self.provider else {"status": "dry-run", "reason": "no provider configured"}
            self.store.finish(row["id"], "completed", result)
            return {"job_id": row["id"], **result}
        except Exception as exc:  # keep the queue observable and retry-safe
            result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            self.store.finish(row["id"], "failed", result)
            return {"job_id": row["id"], **result}
