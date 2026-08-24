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

# The chassis throttles routinely on this hardware, so zero is not a realistic bar. The number
# that matters is whether the delta is small next to the spread between two repeats of run B.
DEFAULT_MAX_PACKAGE_DELTA = 1_000


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
    before: dict, after: dict, *, max_package_delta: int = DEFAULT_MAX_PACKAGE_DELTA
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
    delta = throttle_delta(before, after)
    if delta["package"] > max_package_delta:
        reasons.append(
            f"the package throttle count rose by {delta['package']}, above the "
            f"{max_package_delta} limit: the chassis moved during the run"
        )
    return not reasons, reasons
