r"""Shared pytest fixtures/markers for code/tests/.

Second-review finding: a reviewer who unzips only code.zip (dataset/ is
correctly excluded from every submission artifact -- AGENTS.md \S6.1, "must
never be used for predictions" applies to shipping it too) previously saw
14 tests fail with a raw FileNotFoundError instead of skipping cleanly.
`requires_dataset` gives those tests one shared, readable skip condition.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"

requires_dataset = pytest.mark.skipif(
    not DATASET_DIR.exists(),
    reason="dataset/ not present -- organizer-only, correctly excluded from the submitted code.zip",
)
