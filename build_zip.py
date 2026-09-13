"""Build code.zip for submission. Not part of the pipeline -- a one-off
packaging script, deleted after use (or left gitignored).

Included: code/ (prompts, config.yaml, evaluation/), requirements.txt,
.env.example, docs/ (design.md, PRD.md, stack.md, roadmap.md, AGENTS.md,
problem_statement.md -- the documents dozens of docstrings cite by name;
shipping them is cheaper than stripping every reference).
Excluded: .venv/, __pycache__/, .cache/, dataset/, .env, log.txt,
code/audit/, and any .pyc/.pytest_cache noise.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
ZIP_PATH = REPO_ROOT / "code.zip"

EXCLUDE_DIR_NAMES = {"__pycache__", ".cache", "audit", ".pytest_cache", ".venv"}
EXCLUDE_FILE_SUFFIXES = {".pyc", ".pyo"}


def should_include(path: Path) -> bool:
    parts = set(path.parts)
    if parts & EXCLUDE_DIR_NAMES:
        return False
    if path.suffix in EXCLUDE_FILE_SUFFIXES:
        return False
    return True


def main() -> None:
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()

    entries = []
    for f in (REPO_ROOT / "code").rglob("*"):
        if f.is_file() and should_include(f.relative_to(REPO_ROOT)):
            entries.append(f)
    for f in (REPO_ROOT / "docs").rglob("*"):
        if f.is_file() and should_include(f.relative_to(REPO_ROOT)):
            entries.append(f)
    entries.append(REPO_ROOT / "requirements.txt")
    entries.append(REPO_ROOT / ".env.example")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in entries:
            arcname = f.relative_to(REPO_ROOT)
            zf.write(f, arcname)

    print(f"Wrote {ZIP_PATH} with {len(entries)} files.")
    with zipfile.ZipFile(ZIP_PATH) as zf:
        total_size = sum(i.file_size for i in zf.infolist())
        print(f"Uncompressed size: {total_size:,} bytes; compressed zip: {ZIP_PATH.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
