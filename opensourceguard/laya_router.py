"""Optional Laya typed-decision routing adapter for OpenSourceGuard.

Laya is a lightweight System-1 decision model (Apache-2.0 weights, local).
It is used ONLY as a fast router for fuzzy/ambiguous user intents inside the
workbench (e.g. "我想让项目更好" -> which tool page?). Deterministic keyword
rules always run first; Laya only refines when keywords do not agree or the
intent is vague, and its answer is confidence-gated. If the ``laya`` package
is missing or the model fails to load, every call degrades to the safe
rule-based fallback -- the optional dependency never breaks the main path.

The model is executed in an isolated venv (``g:\\trae\\.venv-laya``) because
torch is a heavy dependency we deliberately keep out of the main runtime.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# venv containing laya + torch (isolated heavy dependencies)
DEFAULT_VENV_PY = Path(r"g:\trae\.venv-laya\Scripts\python.exe")
HF_CACHE = Path(r"C:\Users\Administrator\.cache\huggingface")

# Deterministic intent keywords -> tool page id. Runs first; wins on exact hit.
KEYWORD_RULES: Dict[str, List[str]] = {
    "deploy": ["部署", "怎么运行", "怎么跑", "安装", "启动"],
    "complete": ["补齐", "缺什么", "缺少", "完善", "README", "LICENSE", "规范文件", "gitignore"],
    "verify": ["试跑", "测试", "语法", "发布前", "检查错误", "跑一遍"],
    "secure": ["漏洞", "安全体检", "供应链", "依赖", "flask 0.12", "OSV", "SBOM", "密钥"],
    "organize": ["整理", "归档", "太乱", "杂乱", "垃圾文件", "敏感文件"],
    "publish": ["发布", "上传", "同步仓库", "推到", "推送", "推送到", "开源社区", "上线", "github 上"],
    "maintain": ["issue", "维护", "修复", "问题", "草稿", "队列", "自动维护"],
    "explain": ["看不懂", "解读", "介绍", "是什么", "干什么的", "讲解", "了解这个项目"],
    "health": ["健康度", "打分", "雷达图", "评估项目", "质量", "报告"],
}

DEFAULT_QUESTIONS = {
    "workbench_page": {
        "type": "choice",
        "instructions": (
            "Route the user's goal to the single most relevant OpenSourceGuard "
            "workbench page. Do not explain; choose exactly one page id."
        ),
        "criteria": {
            "deploy": "The user wants to run, deploy or install a project locally.",
            "complete": "The user wants to add missing standard files (README, LICENSE, .gitignore, ...).",
            "verify": "The user is about to publish and wants a pre-publish trial (syntax + tests).",
            "secure": "The user wants a security or supply-chain scan (vulnerabilities, licenses, SBOM).",
            "organize": "The user wants to tidy a messy directory (archive temp/duplicate files).",
            "publish": "The user wants to publish the project to a hosting platform.",
            "maintain": "The user wants to fix an issue or use the maintenance queue.",
            "explain": "The user wants a plain-language explanation of a repository.",
            "health": "The user wants a health score, radar chart or readiness report.",
        },
    }
}


def _venv_python() -> Optional[Path]:
    p = DEFAULT_VENV_PY
    return p if p.exists() else None


def _rule_route(text: str) -> Optional[str]:
    """Deterministic keyword routing; returns first exact hit (rule order wins)."""
    t = text.lower()
    for page, kws in KEYWORD_RULES.items():
        if any(kw.lower() in t for kw in kws):
            return page
    return None


class LayaRouter:
    """Confidence-gated Laya router with deterministic fallback.

    ``venv_py``: explicit path to a venv python that has ``laya`` installed.
    Pass ``False`` to force-disable model routing (safe rule-only mode);
    pass ``None`` to auto-detect the default venv path.
    """

    def __init__(self, venv_py: Optional[Path] = None):
        self._disabled = venv_py is False
        if self._disabled:
            self.venv_py = None
            self._load_error: Optional[str] = "laya routing disabled; rule-only mode"
        else:
            self.venv_py = venv_py or _venv_python()
            self._load_error = None
            if self.venv_py is None:
                self._load_error = "laya venv not found; using rule-only routing"

    def available(self) -> bool:
        return not self._disabled and self.venv_py is not None and self.venv_py.exists() and self._load_error is None

    def health(self) -> Dict[str, Any]:
        if self._disabled:
            return {"available": False, "reason": "laya routing disabled", "mode": "rule-only"}
        if self.venv_py is None:
            return {"available": False, "reason": "laya venv not found", "mode": "rule-only"}
        code = (
            "import asyncio, os;"
            'os.environ["HF_HOME"]=r"C:\\Users\\Administrator\\.cache\\huggingface";'
            'os.environ["HF_HUB_OFFLINE"]="1";'
            'os.environ["TRANSFORMERS_OFFLINE"]="1";'
            "import laya;"
            "print('laya', laya.__version__);"
            "print('model', 'convaiinnovations/laya@multilingual')"
        )
        try:
            r = subprocess.run([str(self.venv_py), "-c", code], capture_output=True, text=True, encoding="utf-8", timeout=60)
            if r.returncode == 0:
                out = (r.stdout or "").strip().splitlines()
                return {"available": True, "mode": "laya+rules", "package": out[0] if out else "laya"}
            return {"available": False, "mode": "rule-only", "reason": (r.stderr or r.stdout or "load failed")[-300:]}
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "mode": "rule-only", "reason": str(exc)[:300]}

    def decide(self, text: str, state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return {page, source, confidence, needs_human, rules_hit}.

        Priority: deterministic keyword rule (exact hit) -> Laya typed-decision
        (confidence-gated) -> safe fallback.
        """
        text = (text or "").strip()
        if not text:
            return {"page": "explain", "source": "empty-input", "confidence": 0.0, "needs_human": False, "rules_hit": None}

        # 1) Deterministic keyword routing (fast, always safe)
        rule_hit = _rule_route(text)
        if rule_hit:
            return {"page": rule_hit, "source": "keyword-rule", "confidence": 1.0, "needs_human": False, "rules_hit": rule_hit}

        # 2) Laya typed-decision as a refinement for vague intents
        if self.venv_py is None:
            return {"page": "explain", "source": "fallback", "confidence": 0.0, "needs_human": True, "rules_hit": None}

        payload = {
            "text": text,
        }
        if state and state.get("intent_hint"):
            payload["intent_hint"] = state["intent_hint"]
        script = (
            "import asyncio, os, sys, json;"
            'os.environ["HF_HOME"]=r"C:\\Users\\Administrator\\.cache\\huggingface";'
            'os.environ["HF_HUB_OFFLINE"]="1";'
            'os.environ["TRANSFORMERS_OFFLINE"]="1";'
            "import laya;"
            "q=" + json.dumps(DEFAULT_QUESTIONS, ensure_ascii=False) + ";"
            "p=" + json.dumps(payload, ensure_ascii=False) + ";"
            "a=laya.load('convaiinnovations/laya', subfolder='multilingual');"
            "r=a.predict(p, q);"
            "ans=(r.get('answers') or {}).get('workbench_page') or {};"
            "conf=ans.get('answer_confidence');"
            "conf=float(conf) if isinstance(conf,(int,float)) else 0.0;"
            "print(json.dumps({'page': ans.get('choice'), 'confidence': conf}, ensure_ascii=False))"
        )
        try:
            r = subprocess.run([str(self.venv_py), "-c", script], capture_output=True, text=True, encoding="utf-8", timeout=120)
            if r.returncode != 0:
                return {"page": "explain", "source": "laya-error", "confidence": 0.0, "needs_human": True, "rules_hit": None}
            result = json.loads((r.stdout or "").strip().splitlines()[-1])
            page = result.get("page")
            confidence = max(0.0, min(1.0, float(result.get("confidence", 0.0))))
            # Confidence gate: low-confidence Laya output must not auto-route.
            if page and confidence >= 0.85:
                return {"page": page, "source": "laya", "confidence": round(confidence, 3), "needs_human": False, "rules_hit": None}
            return {"page": "explain", "source": "low-confidence", "confidence": round(confidence, 3), "needs_human": True, "rules_hit": None}
        except Exception as exc:  # noqa: BLE001
            return {"page": "explain", "source": "laya-error", "confidence": 0.0, "needs_human": True, "rules_hit": None, "error": str(exc)[:200]}


if __name__ == "__main__":
    router = LayaRouter()
    print(json.dumps(router.health(), ensure_ascii=False))
    for sample in ["我想做游戏", "帮我看看这个仓库是干什么的", "项目缺 README 帮我补齐", "发布前帮我跑一遍测试", "看看 flask 有没有漏洞"]:
        print(json.dumps(router.decide(sample), ensure_ascii=False))
