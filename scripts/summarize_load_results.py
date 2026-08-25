#!/usr/bin/env python
"""Turn a matrix of k6 summaries into the tables the report and the analysis carry.

The k6 summary JSON is large and nested. Reading four of them by eye invites the two mistakes
this module exists to prevent: comparing a p(95) against a p(99), and calling a difference a
result when it is smaller than the noise floor.

``noise_floor`` measures the spread between the two cached single-replica runs. ``is_significant``
answers the only question an optimization has to pass.

```bash
python scripts/summarize_load_results.py docs/performance/results 2026-08-22-baseline
```
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The operation trends the scenarios register, in the order the report shows them.
OPERATIONS = ("op_read_detail", "op_read_list", "op_write_create", "op_write_update")


class ResultError(ValueError):
    """A summary file is missing, unreadable, or does not carry what the report needs."""


def load_summary(path: str | Path) -> dict:
    """Read one k6 summary, naming the file when it cannot be used."""

    path = Path(path)
    try:
        text = path.read_text()
    except OSError as error:
        raise ResultError(f"cannot read {path}: {error}") from error
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ResultError(f"{path} is not valid JSON: {error}") from error
    if "metrics" not in data:
        raise ResultError(f"{path} carries no metrics block")
    return data


def metric(summary: dict, name: str, statistic: str) -> float | None:
    """Return one statistic, or None when the run never recorded that metric.

    None is not zero. A scenario that made no writes has no write percentile, and reporting
    zero there would read as an impossibly fast write.
    """

    values = summary.get("metrics", {}).get(name, {}).get("values", {})
    value = values.get(statistic)
    return None if value is None else float(value)


def headline(summary: dict) -> dict[str, float | None]:
    """The numbers every run is judged on."""

    return {
        "requests": metric(summary, "http_reqs", "count"),
        "rps": metric(summary, "http_reqs", "rate"),
        "error_rate": metric(summary, "http_req_failed", "rate"),
        "dropped_iterations": metric(summary, "dropped_iterations", "count") or 0.0,
        "read_p50": metric(summary, "op_read_detail", "p(50)"),
        "read_p95": metric(summary, "op_read_detail", "p(95)"),
        "read_p99": metric(summary, "op_read_detail", "p(99)"),
        "list_p95": metric(summary, "op_read_list", "p(95)"),
        "write_create_p95": metric(summary, "op_write_create", "p(95)"),
        "write_update_p95": metric(summary, "op_write_update", "p(95)"),
    }


def noise_floor(first: dict, second: dict) -> dict[str, float]:
    """The relative spread between two runs of the same configuration.

    Every comparison in the report is a difference between two runs on a shared laptop. A
    difference smaller than this spread is drift, so this is the number an optimization has to
    beat before it counts as a win.
    """

    a, b = headline(first), headline(second)
    spread: dict[str, float] = {}
    for key, left in a.items():
        right = b.get(key)
        if left is None or right is None:
            continue
        mean = (left + right) / 2
        if mean == 0:
            spread[key] = 0.0
            continue
        spread[key] = abs(left - right) / mean
    return spread


def is_significant(before: float, after: float, floor: float) -> bool:
    """Does a change beat the noise floor?

    ``floor`` is a fraction, as ``noise_floor`` returns it. A change equal to the floor does not
    count: the floor is the size of a difference two identical runs already produce.
    """

    if floor < 0:
        raise ResultError(f"the noise floor cannot be negative, got {floor}")
    mean = (before + after) / 2
    if mean == 0:
        return False
    return abs(after - before) / mean > floor


def fmt(value: float | None, digits: int = 1) -> str:
    """Render one cell. A missing measurement is a dash, never a zero."""

    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def results_table(runs: dict[str, dict]) -> str:
    """One markdown row per run, in the order given."""

    header = (
        "| Run | Requests | rps | Error rate | Read P50 | Read P95 | Read P99 | List P95 "
        "| Create P95 | Update P95 |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = []
    for label, summary in runs.items():
        h = headline(summary)
        rows.append(
            f"| {label} | {fmt(h['requests'], 0)} | {fmt(h['rps'])} "
            f"| {fmt((h['error_rate'] or 0) * 100, 3)}% | {fmt(h['read_p50'])} "
            f"| {fmt(h['read_p95'])} | {fmt(h['read_p99'])} | {fmt(h['list_p95'])} "
            f"| {fmt(h['write_create_p95'])} | {fmt(h['write_update_p95'])} |"
        )
    return header + "\n".join(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path, help="directory holding the summaries")
    parser.add_argument("matrix_id", help="the matrix id the run used")
    parser.add_argument(
        "--scenario", default="steady", help="which scenario to tabulate (default: steady)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runs: dict[str, dict] = {}
    for label in ("A", "B", "C", "B2"):
        path = args.results_dir / f"{args.scenario}-{args.matrix_id}-{label}.json"
        if path.is_file():
            runs[label] = load_summary(path)
    if not runs:
        raise ResultError(f"no {args.scenario} summaries for matrix {args.matrix_id}")

    print(results_table(runs))
    if "B" in runs and "B2" in runs:
        print("\nNoise floor, B against B2:")
        for key, spread in sorted(noise_floor(runs["B"], runs["B2"]).items()):
            print(f"  {key}: {spread * 100:.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
