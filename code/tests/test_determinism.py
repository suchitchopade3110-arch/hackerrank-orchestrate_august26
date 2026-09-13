r"""Two runs of the same input must produce byte-identical output
(stack.md \S7: "A second run with a warm cache produces a byte-identical
output.csv"). Tested here on the deterministic arithmetic core
(--no-vlm --no-llm) -- that's the strongest, cleanest claim: no model
call, no cache-dependent I/O, just the pure candidate/ranking/status
pipeline run twice on the same 25 samples and diffed byte for byte.

(Live model calls are separately measured as NOT perfectly deterministic
at temperature=0 on this account -- see evaluation/ablations.md \S8 -- so
asserting byte-identical output WITH perception on would be testing a
claim already known false, not a regression guard.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest

import main as pipeline
from tests.conftest import requires_dataset

pytestmark = requires_dataset


@pytest.fixture(scope="module")
def two_runs():
    # Loading + FX-converting all 25k events is expensive -- run the pair
    # once for this whole file, not once per assertion.
    sample_ids = [f"request_{i:02d}" for i in range(1, 26)]
    run_1 = pipeline.run(sample_ids, verbose=False, use_vlm=False, use_llm=False)
    run_2 = pipeline.run(sample_ids, verbose=False, use_vlm=False, use_llm=False)
    return run_1, run_2


def test_deterministic_core_is_byte_identical_on_rerun(two_runs):
    run_1, run_2 = two_runs
    # Compare as the exact bytes a CSV writer would produce, not just
    # Python equality -- catches any float/formatting nondeterminism too.
    bytes_1 = json.dumps(run_1, sort_keys=True, default=str).encode("utf-8")
    bytes_2 = json.dumps(run_2, sort_keys=True, default=str).encode("utf-8")
    assert bytes_1 == bytes_2


def test_deterministic_core_reruns_produce_the_same_row_count_and_ids(two_runs):
    run_1, run_2 = two_runs
    assert len(run_1) == len(run_2) == 25
    assert [r["request_id"] for r in run_1] == [r["request_id"] for r in run_2]
