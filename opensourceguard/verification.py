"""Constrained local verification for an isolated repository copy."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Sequence


@dataclass
class CommandResult:
    command: list[str]
    return_code: int
    passed: bool
    timed_out: bool
    stdout: str = ""
    stderr: str = ""


@dataclass
class VerificationResult:
    verification_status: str
    execution_mode: str
    tests: list[CommandResult]
    warnings: list[str]

    def to_dict(self) -> dict:
        value = asdict(self)
        return value


class VerificationEngine:
    def __init__(self, timeout_seconds: int = 30, mode: str = "local") -> None:
        self.timeout_seconds = max(1, min(timeout_seconds, 300))
        self.mode = mode

    def _run(self, command: Sequence[str], cwd: Path) -> CommandResult:
        try:
            p = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, timeout=self.timeout_seconds)
            return CommandResult(list(command), p.returncode, p.returncode == 0, False, p.stdout[-4000:], p.stderr[-4000:])
        except subprocess.TimeoutExpired as exc:
            return CommandResult(list(command), -1, False, True, str(exc.stdout or "")[-4000:], str(exc.stderr or "")[-4000:])

    def verify(self, repo: str | Path, commands: Sequence[Sequence[str]], patch: Callable[[Path], None] | None = None) -> dict:
        source = Path(repo).resolve()
        if not source.is_dir():
            raise ValueError("repository directory does not exist")
        if self.mode == "skip":
            return VerificationResult("skipped", "skip", [], ["execution disabled; no code was run"]).to_dict()
        with tempfile.TemporaryDirectory(prefix="osg-verify-") as tmp:
            target = Path(tmp) / "repo"
            self._copy_tree(source, target)
            baseline = [self._run(cmd, target) for cmd in commands]
            if patch:
                patch(target)
            candidate = [self._run(cmd, target) for cmd in commands]
            results = [CommandResult(x.command, x.return_code, x.passed, x.timed_out, x.stdout, x.stderr) for x in baseline]
            results += [CommandResult(x.command, x.return_code, x.passed, x.timed_out, x.stdout, x.stderr) for x in candidate]
            return VerificationResult("verified" if all(x.passed for x in candidate) else "failed", self.mode, results, ["repository was copied to a temporary directory; original files were not modified"]).to_dict()

    @staticmethod
    def _copy_tree(source: Path, target: Path) -> None:
        import shutil
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv", "*.sqlite*"))


def python_unittest(*names: str) -> list[str]:
    return [sys.executable, "-m", "unittest", *names]
