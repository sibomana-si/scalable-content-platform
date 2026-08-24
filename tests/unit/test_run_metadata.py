"""The environment block that makes a load-test number citable.

A latency figure with no machine state behind it is an anecdote: nobody can tell later whether
the run met a 45 W clamp, a battery, or a governor that drifted halfway through. `perf_env.sh
report` writes one JSON snapshot before and after every run, and this module decides whether the
pair says the run is trustworthy. A missing key fails loudly here rather than becoming a footnote
in the report.
"""

import json

import pytest

from scripts.run_metadata import (
    REQUIRED_KEYS,
    MetadataError,
    is_citable,
    load,
    throttle_delta,
    validate,
)


def a_snapshot(**overrides: object) -> dict:
    snapshot = {
        "timestamp": "2026-08-22T10:00:00+00:00",
        "hostname": "laptop",
        "kernel": "6.8.0-136-generic",
        "cpu_model": "12th Gen Intel(R) Core(TM) i7-12700H",
        "nproc": 20,
        "governor": "performance",
        "energy_performance_preference": "performance",
        "power_profile": "performance",
        "no_turbo": 0,
        "min_perf_pct": 100,
        "max_perf_pct": 100,
        "rapl_pl1_watts": 45.0,
        "rapl_pl2_watts": 115.0,
        "package_throttle_count": 702944,
        "core_throttle_count": 51,
        "k6_cpuset": "12-19",
        "ac_online": 1,
        "loadavg": 0.42,
    }
    snapshot.update(overrides)
    return snapshot


# --- validation ---------------------------------------------------------------------------------


def test_a_complete_snapshot_validates() -> None:
    assert validate(a_snapshot()) == a_snapshot()


def test_the_required_keys_cover_both_error_terms() -> None:
    """The shared host and the power policy are the two things the report must disclose."""

    assert {"k6_cpuset", "power_profile", "rapl_pl1_watts", "ac_online"} <= REQUIRED_KEYS


@pytest.mark.parametrize("missing", sorted(REQUIRED_KEYS))
def test_a_missing_key_fails_loudly(missing: str) -> None:
    snapshot = a_snapshot()
    del snapshot[missing]

    with pytest.raises(MetadataError, match=missing):
        validate(snapshot)


def test_the_error_names_every_missing_key_at_once() -> None:
    """One run per missing field would be a slow way to fix a broken script."""

    snapshot = a_snapshot()
    del snapshot["governor"]
    del snapshot["ac_online"]

    with pytest.raises(MetadataError) as raised:
        validate(snapshot)

    assert "governor" in str(raised.value)
    assert "ac_online" in str(raised.value)


def test_an_unknown_extra_key_is_allowed() -> None:
    """The snapshot may grow. Only absence is a defect."""

    assert validate(a_snapshot(turbostat_note="idle"))["turbostat_note"] == "idle"


def test_a_snapshot_loads_from_disk(tmp_path) -> None:
    path = tmp_path / "before.json"
    path.write_text(json.dumps(a_snapshot()))

    assert load(path)["cpu_model"].startswith("12th Gen")


def test_a_snapshot_that_is_not_json_fails_with_its_path(tmp_path) -> None:
    path = tmp_path / "before.json"
    path.write_text("performance\n")

    with pytest.raises(MetadataError, match="before.json"):
        load(path)


# --- thermal drift ------------------------------------------------------------------------------


def test_the_throttle_delta_is_the_difference_across_the_run() -> None:
    before = a_snapshot(package_throttle_count=100, core_throttle_count=5)
    after = a_snapshot(package_throttle_count=180, core_throttle_count=9)

    assert throttle_delta(before, after) == {"package": 80, "core": 4}


def test_a_run_with_no_throttling_reports_zero() -> None:
    assert throttle_delta(a_snapshot(), a_snapshot()) == {"package": 0, "core": 0}


# --- citability ---------------------------------------------------------------------------------


def test_a_clean_run_is_citable() -> None:
    citable, reasons = is_citable(a_snapshot(), a_snapshot(), max_package_delta=1_000)

    assert citable
    assert reasons == []


def test_a_run_on_battery_is_not_citable() -> None:
    citable, reasons = is_citable(
        a_snapshot(ac_online=0), a_snapshot(ac_online=0), max_package_delta=1_000
    )

    assert not citable
    assert any("AC power" in reason for reason in reasons)


def test_a_profile_that_drifted_mid_run_is_not_citable() -> None:
    """power-profiles-daemon reverting is silent, and the run still produces numbers."""

    citable, reasons = is_citable(
        a_snapshot(), a_snapshot(power_profile="balanced"), max_package_delta=1_000
    )

    assert not citable
    assert any("power profile" in reason for reason in reasons)


def test_a_generator_pinned_somewhere_else_is_not_citable() -> None:
    citable, reasons = is_citable(
        a_snapshot(), a_snapshot(k6_cpuset="0-19"), max_package_delta=1_000
    )

    assert not citable
    assert any("cpuset" in reason for reason in reasons)


def test_a_run_the_chassis_moved_during_is_not_citable() -> None:
    citable, reasons = is_citable(
        a_snapshot(package_throttle_count=100),
        a_snapshot(package_throttle_count=5_000),
        max_package_delta=1_000,
    )

    assert not citable
    assert any("throttle" in reason for reason in reasons)


def test_a_delta_exactly_on_the_limit_still_counts() -> None:
    citable, _ = is_citable(
        a_snapshot(package_throttle_count=0),
        a_snapshot(package_throttle_count=1_000),
        max_package_delta=1_000,
    )

    assert citable


def test_every_reason_is_reported_not_only_the_first() -> None:
    """A half-fixed environment wastes a second five-minute run."""

    citable, reasons = is_citable(
        a_snapshot(ac_online=0),
        a_snapshot(ac_online=0, power_profile="balanced", k6_cpuset="0-19"),
        max_package_delta=1_000,
    )

    assert not citable
    assert len(reasons) == 3


def test_an_incomplete_snapshot_cannot_be_judged() -> None:
    snapshot = a_snapshot()
    del snapshot["ac_online"]

    with pytest.raises(MetadataError):
        is_citable(snapshot, a_snapshot(), max_package_delta=1_000)
