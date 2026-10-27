"""Create a clean source archive without credentials, caches or runtime DBs."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / "deliverables"


def main() -> None:
    OUT.mkdir(exist_ok=True)
    archive = OUT / "OpenSourceGuard-AIC-AI开源-0.6.0.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file() or any(part in {".git", "__pycache__", ".venv", "dist", "build"} for part in path.parts) or path.suffix in {".sqlite", ".sqlite3", ".pyc"} or path.name == ".env":
                continue
            zf.write(path, path.relative_to(ROOT))
    print(archive)


if __name__ == "__main__":
    main()
