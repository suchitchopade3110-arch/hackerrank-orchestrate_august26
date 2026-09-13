"""Schema probe: print real columns, dtypes, null counts, value distributions,
and date ranges for every file in dataset/. Run this first (Phase 1 step 1)
and read the output before writing loader.py — a wrong schema assumption here
costs minutes; found downstream it costs hours.

Usage:
    python code/io_layer/probe.py [--file NAME.csv] [--top N]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"

FILES = [
    "financial_profiles.csv",
    "financial_events.csv",
    "exchange_rates.csv",
    "requests.csv",
    "sample_requests.csv",
    "request_payment_options.csv",
    "messages.csv",
    "images.csv",
    "output.csv",
]

# Columns cheap enough to always show full value_counts for (small, likely
# categorical). Everything else falls back to top-N + unique count.
CATEGORICAL_HINT_MAX_UNIQUE = 40

DATE_NAME_HINTS = ("date", "_at", "timestamp")


def _looks_like_date_col(col: str) -> bool:
    lower = col.lower()
    return any(hint in lower for hint in DATE_NAME_HINTS)


def probe_file(path: Path, top_n: int) -> None:
    print("=" * 100)
    print(f"FILE: {path.name}")
    print("=" * 100)

    if not path.exists():
        print(f"  MISSING: {path}")
        return

    # Read everything as string first so we see raw values (blanks vs "0" vs
    # missing), rather than pandas silently coercing types.
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])

    print(f"  shape: {df.shape[0]} rows x {df.shape[1]} cols")
    print(f"  columns: {list(df.columns)}")
    print()

    for col in df.columns:
        series = df[col]
        n_null = series.isna().sum()
        n_unique = series.nunique(dropna=True)
        print(f"  -- {col} --")
        print(f"     dtype(raw)=str  nulls={n_null}/{len(series)}  unique={n_unique}")

        if _looks_like_date_col(col):
            parsed = pd.to_datetime(series, errors="coerce")
            n_unparsed = parsed.isna().sum() - n_null
            if parsed.notna().any():
                print(
                    f"     date range: {parsed.min()} .. {parsed.max()}  "
                    f"(unparseable non-null values: {n_unparsed})"
                )
            else:
                print("     date range: could not parse any value")

        if n_unique <= CATEGORICAL_HINT_MAX_UNIQUE:
            counts = series.value_counts(dropna=True).head(top_n)
            print(f"     value_counts (top {top_n}):")
            for val, cnt in counts.items():
                print(f"       {val!r}: {cnt}")
        else:
            sample = series.dropna().head(5).tolist()
            print(f"     sample values: {sample}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default=None, help="Probe a single file only")
    parser.add_argument("--top", type=int, default=20, help="Top-N value_counts")
    args = parser.parse_args()

    targets = [args.file] if args.file else FILES

    for name in targets:
        probe_file(DATASET_DIR / name, args.top)

    media_dir = DATASET_DIR / "media" / "images"
    if media_dir.exists():
        images = sorted(media_dir.glob("*.png"))
        print("=" * 100)
        print(f"MEDIA: {media_dir}")
        print("=" * 100)
        print(f"  {len(images)} .png files")
        print(f"  sample: {[p.name for p in images[:10]]}")


if __name__ == "__main__":
    sys.exit(main())
