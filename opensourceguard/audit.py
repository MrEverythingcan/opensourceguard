"""Auditable, provider-independent usage and cost summary."""
from __future__ import annotations
from typing import Any

def summarize_audit(jobs: list[dict[str, Any]], *, model_calls: int = 0, input_tokens: int = 0, output_tokens: int = 0) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for job in jobs: counts[str(job.get("status", "unknown"))] = counts.get(str(job.get("status", "unknown")), 0) + 1
    return {"jobs": counts, "model_calls": model_calls, "tokens": {"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens}, "estimated_cost_usd": 0.0, "adoption_feedback": {"accepted": 0, "rejected": 0, "pending": len(jobs)}, "policy": {"remote_write_default": False, "auto_merge": False, "high_risk_requires_human": True}}
