"""Read and judge the machine-state snapshots that `perf_env.sh report` writes.

Every load-test run is bracketed by two snapshots. Together they answer the question a reader
asks first: was the machine the same at the end as at the start? Four things can move and each
one invalidates the comparison the report is built on — the laptop dropped to battery, the power
profile drifted back to `balanced`, the generator was pinned somewhere else, or the chassis
throttled hard enough to change the silicon under the test.

The rules here are confidence checks 5 and 6 of the load-test plan, made executable so a run that
fails them cannot quietly reach the report.
"""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED_KEYS = frozenset(
    {
        "timestamp",
        "hostname",
        "kernel",
        "cpu_model",
        "nproc",
        "governor",
        "energy_performance_preference",
        "power_profile",
        "no_turbo",
        "min_perf_pct",
        "max_perf_pct",
        "rapl_pl1_watts",
        "rapl_pl2_watts",
        "package_throttle_count",
        "core_throttle_count",
        "k6_cpuset",
        "ac_online",
        "loadavg",
    }
)

# An absolute throttle cap is off by default, because the first matrix measured what it was
# guessing at. Four runs on this chassis produced package deltas of 16,015 (A), 11,859 (B),
# 2,123 (C) and 11,468 (B2). The spread tracks how long each run spent saturated, not how far the
# environment moved: run A drove every read to MySQL and throttled most, run C never reached its
# knee and throttled least. A cap of any value therefore marks the run that finds the bottleneck
# as the least trustworthy, which is backwards.
#
# Drift lives in a different pair: a run against its own repeat. B and B2 ran the same workload
# 35 minutes apart, and their deltas agree to 3.3%. `repeat_is_consistent` applies that rule with
# a tolerance of 10%, three times the observed spread. Pass `max_package_delta` to `is_citable`
# only to answer a specific question about one run.
DEFAULT_MAX_PACKAGE_DELTA = None

# Three times the measured B-to-B2 spread of 3.3%.
DEFAULT_MAX_THROTTLE_DRIFT = 0.10


class MetadataError(ValueError):
    """A snapshot is unreadable or incomplete, so no result built on it can be cited."""


def load(path: str | Path) -> dict:
    """Read one snapshot from disk."""

    path = Path(path)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise MetadataError(f"cannot read the run metadata at {path}: {error}") from error
    if not isinstance(payload, dict):
        raise MetadataError(f"the run metadata at {path} is not a JSON object")
    return validate(payload)


def validate(snapshot: dict) -> dict:
    """Return the snapshot, or raise naming every key it lacks."""

    missing = sorted(REQUIRED_KEYS - set(snapshot))
    if missing:
        raise MetadataError(f"run metadata is missing {', '.join(missing)}")
    return snapshot


def throttle_delta(before: dict, after: dict) -> dict[str, int]:
    """Package and core throttle counts accumulated during the run."""

    return {
        "package": after["package_throttle_count"] - before["package_throttle_count"],
        "core": after["core_throttle_count"] - before["core_throttle_count"],
    }


def is_citable(
    before: dict, after: dict, *, max_package_delta: int | None = DEFAULT_MAX_PACKAGE_DELTA
) -> tuple[bool, list[str]]:
    """Decide whether the pair of snapshots supports a citable result.

    Returns every reason, not the first one. A half-fixed environment costs another five-minute
    run to discover the rest.
    """

    validate(before)
    validate(after)

    reasons: list[str] = []
    if not (before["ac_online"] and after["ac_online"]):
        reasons.append("the laptop was not on AC power, so the numbers mean nothing")
    if before["power_profile"] != after["power_profile"]:
        reasons.append(
            f"the power profile moved from {before['power_profile']!r} to "
            f"{after['power_profile']!r} during the run"
        )
    if before["k6_cpuset"] != after["k6_cpuset"]:
        reasons.append(
            f"the generator cpuset moved from {before['k6_cpuset']!r} to {after['k6_cpuset']!r}"
        )
    if max_package_delta is not None:
        delta = throttle_delta(before, after)
        if delta["package"] > max_package_delta:
            reasons.append(
                f"the package throttle count rose by {delta['package']}, above the "
                f"{max_package_delta} limit: the chassis moved during the run"
            )
    return not reasons, reasons


def throttle_drift(first_delta: int, second_delta: int) -> float:
    """How far two runs of the same workload disagree about how much the chassis throttled.

    The result is the difference over the mean of the pair, so it reads as a fraction: 0.0 for
    two identical runs, 2.0 when one run throttled and the other did not at all. Order does not
    matter.
    """

    if first_delta < 0 or second_delta < 0:
        raise ValueError(
            f"a throttle delta cannot be negative, got {first_delta} and {second_delta}: "
            "the snapshots are out of order or come from different runs"
        )
    total = first_delta + second_delta
    if total == 0:
        return 0.0
    return abs(first_delta - second_delta) / (total / 2)


def repeat_is_consistent(
    run: tuple[dict, dict],
    repeat: tuple[dict, dict],
    *,
    max_drift: float = DEFAULT_MAX_THROTTLE_DRIFT,
) -> tuple[bool, list[str]]:
    """Decide whether a run and its repeat met the same machine.

    Both runs must pass their own environment checks, and their throttle deltas must agree. This
    is the rule that an absolute cap cannot express: it holds the workload fixed, so anything left
    over is drift.
    """

    reasons: list[str] = []
    for label, (before, after) in (("run", run), ("repeat", repeat)):
        _, run_reasons = is_citable(before, after)
        reasons.extend(f"{label}: {reason}" for reason in run_reasons)

    first = throttle_delta(*run)["package"]
    second = throttle_delta(*repeat)["package"]
    drift = throttle_drift(first, second)
    if drift > max_drift:
        reasons.append(
            f"the package throttle count moved {drift:.1%} between the run and its repeat "
            f"({first} against {second}), above the {max_drift:.0%} limit: the machine drifted "
            "between them, so the pair does not set a noise floor"
        )
    return not reasons, reasons
