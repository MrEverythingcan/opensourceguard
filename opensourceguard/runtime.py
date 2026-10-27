"""Runtime, dependency-lock and ModelScope/Docker delivery preview."""
from __future__ import annotations
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from .verification import VerificationEngine

def runtime_plan(repo: str | Path) -> dict[str, Any]:
    root = Path(repo).resolve()
    if not root.is_dir(): raise ValueError("repository directory does not exist")
    names = {p.name for p in root.iterdir()}
    stack = []
    if any(x in names for x in ("pyproject.toml", "requirements.txt", "setup.py")) or any(root.glob("*.py")): stack.append("Python")
    if "package.json" in names: stack.append("Node.js")
    docker = any(x in names for x in ("Dockerfile", "docker-compose.yml", "compose.yaml"))
    locks = [x for x in ("poetry.lock", "Pipfile.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock") if x in names]
    steps = []
    if "Python" in stack:
        steps.append("创建并激活虚拟环境，再按 requirements.txt 或 pyproject.toml 安装依赖。")
    if "Node.js" in stack:
        steps.append("核对 package.json 中的 scripts，人工确认后再安装依赖和执行启动命令。")
    if docker:
        steps.append("检测到 Docker 配置，可选择容器部署；先核对端口和环境变量。")
    steps.append("优先按锁文件安装依赖：" + "、".join(locks) + "。" if locks else "未发现依赖锁文件，正式发布前建议锁定依赖版本。")
    steps.append("点击「让 Agent 试跑一次」，可在临时副本中执行语法检查和测试。")
    return {"repository": str(root), "stack": stack or ["unknown"], "docker": docker, "lockfiles": locks, "virtual_environment": "recommended" if "Python" in stack else "not-applicable", "steps": steps, "modelscope": {"space_ready": (root / "app.py").exists(), "upload": "preview-only", "credentials_required": True}, "warnings": ["只生成部署方案，不上传、不发布、不执行远程命令。", "正式部署前请锁定依赖、检查环境变量和许可证。"]}


_TEST_DIRS = ("tests", "test")


def _run_check(engine: VerificationEngine, cwd: Path, name: str, command: list[str]) -> dict[str, Any]:
    r = engine._run(command, cwd)  # allowlisted command on an isolated copy
    output = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()[-600:]
    no_tests = name == "运行测试" and "Ran 0 tests" in output
    passed = r.passed and not no_tests
    summary = "未发现可执行测试（0 个用例）" if no_tests else ("通过" if passed else ("运行超时（可能存在死循环或常驻进程）" if r.timed_out else f"未通过（返回码 {r.return_code}）"))
    return {"name": name, "command": " ".join(command), "passed": passed, "no_tests": no_tests, "timed_out": r.timed_out, "return_code": r.return_code, "summary": summary, "output": output}


def _analyze_failures(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for check in checks:
        if check["passed"]:
            continue
        out = check["output"]
        if check["timed_out"]:
            failures.append({"kind": "运行超时", "title": f"{check['name']}超时", "detail": "可能存在死循环、等待输入或启动了不退出的常驻服务。"})
            continue
        if check.get("no_tests"):
            failures.append({"kind": "未找到测试", "title": "测试目录中没有可执行用例", "detail": "unittest 发现 0 个测试；独立 pytest 函数不会被 unittest 执行。"})
            continue
        m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", out)
        if m:
            module = m.group(1).split(".")[0]
            failures.append({"kind": "缺少依赖", "title": f"缺少依赖 {module}", "detail": "代码 import 了环境中未安装的包。", "module": module})
            continue
        m = re.search(r'File "([^"]+)", line (\d+)[\s\S]*?(?:SyntaxError|IndentationError): (.+)', out)
        if m:
            failures.append({"kind": "语法错误", "title": f"{Path(m.group(1)).name} 第 {m.group(2)} 行", "detail": m.group(3).strip()[:200]})
            continue
        failed_tests = re.findall(r"(?:FAIL|ERROR): ([\w.]+)", out)
        if failed_tests:
            failures.append({"kind": "测试未通过", "title": f"{len(failed_tests)} 个测试失败", "detail": "、".join(failed_tests[:5])})
            continue
        last = out.strip().splitlines()[-1][:200] if out.strip() else "无错误输出"
        failures.append({"kind": "运行失败", "title": f"{check['name']}未通过", "detail": last})
    return failures


def _trial_suggestions(failures: list[dict[str, Any]], checks: list[dict[str, Any]], notes: list[str]) -> list[str]:
    suggestions: list[str] = []
    for failure in failures:
        if failure["kind"] == "缺少依赖":
            suggestions.append(f"安装缺失依赖：pip install {failure['module']}，并把它写进 requirements.txt——发布后别人也需要它。")
        elif failure["kind"] == "语法错误":
            suggestions.append(f"先修复 {failure['title']} 的语法错误：{failure['detail']}。语法错误会让整个项目无法运行。")
        elif failure["kind"] == "测试未通过":
            suggestions.append("逐个查看失败测试的断言信息，优先修复核心流程相关用例，修好后重新试跑。")
        elif failure["kind"] == "运行超时":
            suggestions.append("让程序可以自动退出（加超时或参数化），避免试跑和评审演示时卡住。")
        elif failure["kind"] == "未找到测试":
            suggestions.append("测试目录存在但未发现可执行用例，可用「一键补齐」生成 unittest 基础测试，或补充至少一个核心流程测试。")
        else:
            suggestions.append("定位日志中第一个报错点，修复后重新试跑确认。")
    if checks and all(c["passed"] for c in checks):
        suggestions.append("全部检查通过，可以进入「发布项目」流程。")
    if not any(c["name"] == "运行测试" for c in checks):
        suggestions.append("项目还没有可自动运行的测试，可用「一键补齐」生成基础测试，发布更有底气。")
    suggestions.extend(notes)
    return suggestions


def _trial_advice(verdict: str, failures: list[dict[str, Any]], checks: list[dict[str, Any]], model_callback: Callable[[str], str] | None) -> tuple[str, str]:
    if verdict == "passed":
        template = f"试跑通过：{len(checks)} 项检查全部通过，可以放心进入发布流程。"
    elif verdict == "no-checks":
        template = "没有识别到可自动运行的检查项。如果是 Python 项目，建议补上 requirements.txt 和 tests 目录后再试跑。"
    else:
        template = f"试跑发现 {len(failures)} 个问题。建议按下面的修复建议逐条处理，全部解决并重新试跑通过后再发布。"
    if model_callback:
        try:
            log = "\n".join(f"[{c['name']}] {c['summary']}\n{c['output'][:400]}" for c in checks if not c["passed"]) or "全部通过"
            prompt = (
                "你在帮一个准备开源的本地项目做发布前试跑，代码在临时副本中运行，未改动原文件。\n"
                f"试跑结果：{log}\n"
                "请用 3 句以内中文给出修复优先级建议，口吻像有经验的维护者，纯文本、不要使用任何 Markdown 符号。"
            )
            text = str(model_callback(prompt) or "").strip()
            if text:
                return text, "model+local-run"
        except Exception:  # noqa: BLE001 - model polish is best-effort
            pass
    return template, "local-run"


def trial_run(repo: str | Path, *, model_callback: Callable[[str], str] | None = None, timeout: int = 60) -> dict[str, Any]:
    """Run allowlisted checks on a temporary copy and turn failures into fix suggestions.

    The original repository is never modified; only `compileall` and
    `unittest discover` are executed, each with a hard timeout.
    """
    root = Path(repo).resolve()
    if not root.is_dir():
        raise ValueError("repository directory does not exist")
    names = {p.name for p in root.iterdir()}
    is_python = any(x in names for x in ("pyproject.toml", "requirements.txt", "setup.py")) or any(root.glob("*.py"))
    test_dir = next((d for d in _TEST_DIRS if (root / d).is_dir()), None)
    notes: list[str] = []
    if "package.json" in names:
        notes.append("检测到 Node.js 项目：npm install 会改动环境，暂不自动执行，请人工确认后再运行 npm test。")
    checks: list[dict[str, Any]] = []
    engine = VerificationEngine(timeout_seconds=timeout)
    with tempfile.TemporaryDirectory(prefix="osg-trial-") as tmp:
        target = Path(tmp) / "repo"
        VerificationEngine._copy_tree(root, target)
        if is_python:
            checks.append(_run_check(engine, target, "语法检查", [sys.executable, "-m", "compileall", "-q", "."]))
        if test_dir:
            checks.append(_run_check(engine, target, "运行测试", [sys.executable, "-m", "unittest", "discover", "-s", test_dir, "-q"]))
    if not checks:
        verdict = "no-checks"
    elif all(c["passed"] for c in checks):
        verdict = "passed"
    else:
        verdict = "failed"
    failures = _analyze_failures(checks)
    advice, source = _trial_advice(verdict, failures, checks, model_callback)
    return {
        "repository": str(root),
        "verdict": verdict,
        "ready_to_publish": verdict == "passed",
        "checks": checks,
        "failures": failures,
        "suggestions": _trial_suggestions(failures, checks, notes),
        "advice": advice,
        "source": source,
        "isolation": "试跑在临时副本中进行，未改动你的项目文件；仅执行白名单命令并有时长限制。",
    }
