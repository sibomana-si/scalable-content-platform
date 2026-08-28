"""``tests/load/lib/slo.json`` is the single source of the k6 thresholds. This keeps it honest.

k6 fails a run on its thresholds and sets the exit code from them, so those numbers *are* the
pass/fail line for M6. If they drift from the NFR table, the load test passes against a target
nobody agreed to. The document is the authority; this test makes the JSON follow it.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOAD_DIR = REPO_ROOT / "tests" / "load"
SLO_FILE = LOAD_DIR / "lib" / "slo.json"
NFR = REPO_ROOT / "docs" / "requirements" / "non-functional-requirements.md"
PLAN = REPO_ROOT / "docs" / "performance" / "load-test-plan.md"

SCENARIOS = ("steady", "ramp", "spike")


def slo() -> dict:
    return json.loads(SLO_FILE.read_text())


def nfr_targets() -> dict[str, str]:
    """Metric name -> target cell, from every table row in the NFR document."""

    targets = {}
    for line in NFR.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and not cells[0].startswith("-"):
            targets[cells[0].lower()] = cells[1]
    return targets


def first_number(text: str) -> float:
    match = re.search(r"[\d.]+", text.replace(",", ""))
    assert match, f"no number in {text!r}"
    return float(match.group())


# --- the file itself --------------------------------------------------------------------------


def test_the_slo_file_parses() -> None:
    assert slo()["source"].endswith("non-functional-requirements.md")


def test_the_slo_file_names_every_latency_target() -> None:
    assert set(slo()["latency_ms"]) == {"read_p50", "read_p95", "read_p99", "write_p95"}


# --- it agrees with the NFR table -------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "row"),
    [
        ("read_p50", "read p50 latency"),
        ("read_p95", "read p95 latency"),
        ("read_p99", "read p99 latency"),
        ("write_p95", "write p95 latency"),
    ],
)
def test_every_latency_threshold_matches_the_nfr_row(key: str, row: str) -> None:
    assert slo()["latency_ms"][key] == first_number(nfr_targets()[row])


def test_the_throughput_target_matches_the_nfr_row() -> None:
    assert slo()["throughput_rps"] == first_number(nfr_targets()["throughput (sustained)"])


def test_the_cache_hit_target_matches_the_nfr_row() -> None:
    target = first_number(nfr_targets()["cache hit ratio (hot reads)"])

    assert slo()["cache_hit_ratio"] == target / 100


def test_the_error_budget_matches_the_nfr_row() -> None:
    target = first_number(nfr_targets()["error rate (slo)"])

    assert slo()["error_rate"] == target / 100


# --- the numbers are in the units k6 uses -----------------------------------------------------


def test_latency_thresholds_are_milliseconds_not_seconds() -> None:
    """k6 `http_req_duration` is milliseconds. A threshold of 0.2 would never fail."""

    for name, value in slo()["latency_ms"].items():
        assert isinstance(value, int), f"{name} is not an integer millisecond value"
        assert 1 <= value <= 60_000, f"{name}={value} is not a plausible millisecond target"


def test_every_ratio_is_a_fraction_not_a_percentage() -> None:
    ratios = {
        "error_rate": slo()["error_rate"],
        "cache_hit_ratio": slo()["cache_hit_ratio"],
        **{f"workload.{k}": v for k, v in slo()["workload"].items() if k.endswith("share")},
        "workload.hot_set_traffic": slo()["workload"]["hot_set_traffic"],
    }
    for name, value in ratios.items():
        assert 0.0 <= value <= 1.0, f"{name}={value} looks like a percentage, not a fraction"


def test_the_latency_targets_increase_with_the_quantile() -> None:
    latency = slo()["latency_ms"]

    assert latency["read_p50"] < latency["read_p95"] < latency["read_p99"]


# --- the workload -----------------------------------------------------------------------------


def test_the_read_and_write_shares_add_up() -> None:
    workload = slo()["workload"]

    assert workload["read_share"] + workload["write_share"] == pytest.approx(1.0)


def test_the_hot_set_is_a_minority_of_the_ids_and_a_majority_of_the_reads() -> None:
    """This is the whole reason the >= 90% hit ratio is reachable. Get it backwards and the
    run measures a cache that cannot work."""

    workload = slo()["workload"]

    assert workload["hot_set_share"] < 0.5 < workload["hot_set_traffic"]


def test_the_dataset_size_matches_the_seeder_default() -> None:
    from scripts.seed_load_dataset import DEFAULT_ARTICLES

    assert slo()["workload"]["articles"] == DEFAULT_ARTICLES


def test_the_hot_share_matches_the_seeder_default() -> None:
    """The reads and the authorship are skewed by the same number, on purpose."""

    from scripts.seed_load_dataset import DEFAULT_HOT_SHARE

    assert slo()["workload"]["hot_set_share"] == DEFAULT_HOT_SHARE


# --- the scenarios ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_script_exists(name: str) -> None:
    assert (LOAD_DIR / "scenarios" / f"{name}.js").is_file()


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_is_configured_in_the_slo_file(name: str) -> None:
    assert name in slo()["scenarios"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_is_named_in_the_load_test_plan(name: str) -> None:
    """A scenario the plan does not mention is a scenario nobody agreed to run."""

    assert f"{name}.js" in PLAN.read_text()


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_scenario_uses_an_open_load_model(name: str) -> None:
    """Arrival rate, never a VU count. A closed model hides saturation behind its own backoff."""

    source = (LOAD_DIR / "scenarios" / f"{name}.js").read_text()

    assert "arrival-rate" in source


def test_the_library_files_the_scenarios_import_all_exist() -> None:
    for module in ("slo.json", "workload.js", "api.js", "summary.js"):
        assert (LOAD_DIR / "lib" / module).is_file()


def test_the_selftest_runs_without_infrastructure() -> None:
    """It must not import the API helper, or it would need a running server."""

    source = (LOAD_DIR / "selftest.js").read_text()

    assert "lib/workload.js" in source
    assert "lib/api.js" not in source


# --- the silent-miss guard --------------------------------------------------------------------


def test_a_missing_detail_read_is_counted() -> None:
    """A 404 is not an error, so nothing else records it.

    The 2026-08-24 run drew ids the seeder never wrote. Every read answered 404, which cost
    nothing and cached nothing, and the run still reported a clean pass with a plausible latency
    series. ``read_detail_miss`` is the only number that shows it.
    """

    source = (LOAD_DIR / "lib" / "api.js").read_text()

    assert "read_detail_miss" in source
    assert "missRate.add(response.status === 404)" in source


def test_the_miss_rate_is_bounded_by_a_threshold() -> None:
    """A metric nothing fails on is a metric nobody reads."""

    source = (LOAD_DIR / "lib" / "iteration.js").read_text()

    assert re.search(r"read_detail_miss:\s*\['rate<0\.01'\]", source)


# --- the scenario shapes agree with the plan --------------------------------------------------


def plan_shape(scenario: str) -> str:
    """The Shape cell for one row of the workload-profile table in the plan."""

    for line in PLAN.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and cells[1] == f"`{scenario}.js`":
            return cells[3]
    raise AssertionError(f"the plan has no workload row for {scenario}.js")


def numbers(text: str) -> list[float]:
    return [float(match) for match in re.findall(r"[\d.]+", text.replace(",", ""))]


def test_the_steady_shape_matches_the_plan() -> None:
    """The rate the pass/fail run offers is a published figure, not a leftover default.

    ``steady.js`` sets the exit code for M6. If its rate drifts from the plan, the run reports a
    pass at a load nobody agreed to.
    """
    rate, minutes = numbers(plan_shape("steady"))
    steady = slo()["scenarios"]["steady"]

    assert steady["rate_rps"] == rate
    assert steady["duration"] == f"{int(minutes)}m"


def test_the_ramp_shape_matches_the_plan() -> None:
    start, top, steps, seconds = numbers(plan_shape("ramp"))
    ramp = slo()["scenarios"]["ramp"]

    assert ramp["start_rps"] == start
    assert ramp["steps"] == steps
    assert ramp["step_duration"] == f"{int(seconds)}s"
    # The top of the ramp is the last step, not one step beyond it.
    assert ramp["start_rps"] + (ramp["steps"] - 1) * ramp["step_rps"] == top


def test_the_spike_shape_matches_the_plan() -> None:
    base, peak, seconds, back = numbers(plan_shape("spike"))
    spike = slo()["scenarios"]["spike"]

    assert spike["base_rps"] == base == back
    assert spike["peak_rps"] == peak
    assert spike["spike_duration"] == f"{int(seconds)}s"
