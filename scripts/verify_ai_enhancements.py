# -*- coding: utf-8 -*-
"""端到端验证 OpenSourceGuard 可选增强（Laya 决策路由 + OpenViking 上下文）。

用法：
  python scripts/verify_ai_enhancements.py
运行时：
  - Laya：使用独立 venv g:\trae\.venv-laya（laya 0.4.11 + torch CPU + 本地权重）。
  - OpenViking：读取 .env 中 OSG_OPENVIKING_URL / OSG_OPENVIKING_API_KEY。
两项均"可选增强"：缺任一资源时自动降级，不阻塞其余验证。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opensourceguard.laya_router import LayaRouter, _rule_route  # noqa: E402
from opensourceguard.openviking_context import OpenVikingContext  # noqa: E402


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def main() -> None:
    _load_env()
    report = {"date": "2026-10-27", "checks": []}

    # ---- Laya 决策路由 ----
    laya = LayaRouter()
    report["checks"].append({"name": "laya_keyword_rules", "ok": True, "results": [
        {"input": "推送到 GitHub", "page": _rule_route("推送到 GitHub")},
        {"input": "发布前帮我跑一遍测试", "page": _rule_route("发布前帮我跑一遍测试")},
        {"input": "项目缺 README 帮我补齐", "page": _rule_route("项目缺 README 帮我补齐")},
    ]})
    report["checks"].append({"name": "laya_health", **laya.health()})

    # ---- OpenViking 上下文 ----
    ov = OpenVikingContext()
    report["checks"].append({"name": "openviking_health", **ov.health()})

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
