"""Deterministic failure-log iteration planner used before model-backed repair."""
from __future__ import annotations
import re
from typing import Any

def iterate_repair(issue: str, failure_log: str, *, attempt: int = 1, max_attempts: int = 3) -> dict[str, Any]:
    if not str(issue).strip(): raise ValueError("issue is required")
    if attempt < 1 or attempt > max_attempts: raise ValueError("attempt is outside the allowed range")
    text = str(failure_log or "")
    clues = []
    for pattern, label in [(r"IndexError|list index out of range", "边界条件/空输入"), (r"KeyError|missing key", "缺失字段"), (r"TypeError|NoneType", "类型或空值"), (r"timeout|Timeout", "超时与资源")]:
        if re.search(pattern, text, re.I): clues.append(label)
    if not clues: clues.append("需要先补充可复现日志和最小输入")
    next_action = "补充最小复现并重新运行基线测试" if attempt == 1 else "缩小补丁范围并增加回归断言" if attempt < max_attempts else "停止自动尝试，转人工审核"
    return {"issue": issue, "attempt": attempt, "max_attempts": max_attempts, "failure_signals": clues, "next_action": next_action, "safe_to_auto_apply": False, "status": "planned" if attempt < max_attempts else "human-review"}
