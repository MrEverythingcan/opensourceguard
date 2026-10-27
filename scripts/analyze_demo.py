"""Run the bundled CSV case through retrieval and isolated verification."""

from __future__ import annotations

import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opensourceguard.retrieval import HybridRetriever
from opensourceguard.verification import VerificationEngine


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "examples" / "buggy_csv"


def apply_patch(target: Path) -> None:
    parser = target / "parser.py"
    text = parser.read_text()
    text = text.replace("return rows[0] if len(rows) <= 1 else rows", "return [] if not rows else (rows[0] if len(rows) == 1 else rows)")
    parser.write_text(text)


def main() -> None:
    retriever = HybridRetriever.from_tree(CASE)
    evidence = retriever.search("empty csv parse_csv returns empty list")
    verification = VerificationEngine(timeout_seconds=10).verify(CASE, [["python", "-m", "unittest", "test_parser.py"]], patch=apply_patch)
    print({"evidence": evidence, "verification": verification})


if __name__ == "__main__":
    main()
