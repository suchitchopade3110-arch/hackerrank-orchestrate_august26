r"""Read dataset/output.csv (the blank prediction template) so the writer
takes column order, CSV dialect, quoting, and line terminator from it rather
than a hardcoded list, and so request order matches the template's row order
rather than a sort (roadmap doc \S0 finding 5).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "dataset" / "output.csv"


@dataclass(frozen=True)
class OutputTemplate:
    columns: list[str]
    request_ids: list[str]
    delimiter: str
    quotechar: str
    lineterminator: str
    quoting: int


def load_template(path: Path = TEMPLATE_PATH) -> OutputTemplate:
    with open(path, "rb") as f:
        raw = f.read()
    lineterminator = "\r\n" if b"\r\n" in raw else "\n"

    with open(path, "r", newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)

    header = rows[0]
    request_ids = [row[0] for row in rows[1:] if row]

    return OutputTemplate(
        columns=header,
        request_ids=request_ids,
        delimiter=",",
        quotechar='"',
        lineterminator=lineterminator,
        quoting=csv.QUOTE_MINIMAL,
    )
