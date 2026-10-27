"""OpenSourceGuard release verification; no network or remote writes."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def wait_get(url: str, timeout: float = 8):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return response.status, response.read()
        except Exception:
            time.sleep(.1)
    return None, b""


def post_json(url: str, payload: dict):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, body, {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def main() -> int:
    required = ["README.md", "LICENSE", "pyproject.toml", "opensourceguard/autonomy.py", "opensourceguard/retrieval.py", "opensourceguard/verification.py", "opensourceguard/beginner.py", "demo/agent.html", "docs/AIC_TECHNICAL_REPORT.md", "docs/AIC_RESOURCE_MANIFEST.csv"]
    checks: list[tuple[str, bool, str]] = []
    for name in required:
        path = ROOT / name
        checks.append((f"file:{name}", path.is_file(), "present" if path.is_file() else "missing"))
    compile_result = subprocess.run([sys.executable, "-m", "compileall", "-q", "opensourceguard", "scripts"], cwd=ROOT, capture_output=True, text=True)
    checks.append(("compileall", compile_result.returncode == 0, compile_result.stderr.strip() or "ok"))
    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=ROOT, capture_output=True, text=True)
    checks.append(("unit-tests", tests.returncode == 0, (tests.stdout + tests.stderr).splitlines()[-1] if (tests.stdout + tests.stderr).splitlines() else "no output"))
    demo = subprocess.run([sys.executable, "scripts/analyze_demo.py"], cwd=ROOT, capture_output=True, text=True)
    checks.append(("case-demo", demo.returncode == 0 and "verification" in demo.stdout, demo.stdout[-500:] or demo.stderr[-500:]))
    with tempfile.TemporaryDirectory(prefix="osg-release-") as td:
        server = subprocess.Popen([sys.executable, "-m", "opensourceguard.server", "--port", "8799", "--db", str(Path(td) / "jobs.sqlite3")], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            status, body = wait_get("http://127.0.0.1:8799/health")
            checks.append(("http-health", status == 200 and b'"status": "ok"' in body, f"status={status}"))
            status, body = wait_get("http://127.0.0.1:8799/autonomy/status")
            checks.append(("http-status", status == 200 and b"counts" in body, f"status={status}"))
            try:
                status, body = post_json("http://127.0.0.1:8799/api/beginner/explain", {"repo": "."})
                checks.append(("beginner-explain", status == 200 and b"commercial_fit" in body, f"status={status}"))
                status, body = post_json("http://127.0.0.1:8799/api/beginner/ask", {"repo": ".", "question": "这个项目适合商用吗？"})
                checks.append(("beginner-ask", status == 200 and b"answer" in body, f"status={status}"))
                status, body = post_json("http://127.0.0.1:8799/api/deploy/preview", {"repo": "."})
                checks.append(("deploy-preview", status == 200 and b"allowed_command" in body, f"status={status}"))
                status, body = post_json("http://127.0.0.1:8799/api/open-source/complete", {"repo": ".", "confirm": False})
                checks.append(("open-source-completion-preview", status == 200 and b"files" in body and b"preview" in body, f"status={status}"))
                status, body = post_json("http://127.0.0.1:8799/api/platform/connect", {"platform": "github", "token": ""})
                checks.append(("platform-token-guard", status == 400 and b"Token" in body, f"status={status}"))
                with urllib.request.urlopen("http://127.0.0.1:8799/api/agent/tools") as tools_response:
                    status, body = tools_response.status, tools_response.read()
                checks.append(("agent-tool-registry", status == 200 and b"platform.publish" in body, f"status={status}"))
            except Exception as exc:
                checks.append(("beginner-api", False, str(exc)))
        finally:
            server.terminate()
            try: server.wait(timeout=3)
            except subprocess.TimeoutExpired: server.kill()
    failed = 0
    print("OpenSourceGuard 0.6.0 release verification")
    for name, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += not ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
