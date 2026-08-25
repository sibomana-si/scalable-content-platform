"""Reading a matrix of k6 summaries, without a k6 run.

Two mistakes turn a load test into a wrong conclusion, and both are arithmetic rather than
measurement: comparing one percentile against another, and calling a difference a result when
two identical runs already differ by more. Both are checked here.
"""

import json
from pathlib import Path

import pytest

from scripts.summarize_load_results import (
    OPERATIONS,
    ResultError,
    fmt,
    headline,
    is_significant,
    load_summary,
    metric,
    noise_floor,
    results_table,
)


def a_summary(**overrides: object) -> dict:
    metrics: dict[str, dict] = {
        "http_reqs": {"values": {"count": 60_000, "rate": 200.0}},
        "http_req_failed": {"values": {"rate": 0.0004}},
        "dropped_iterations": {"values": {"count": 0}},
        "op_read_detail": {"values": {"p(50)": 8.0, "p(95)": 21.0, "p(99)": 44.0}},
        "op_read_list": {"values": {"p(95)": 35.0}},
        "op_write_create": {"values": {"p(95)": 60.0}},
        "op_write_update": {"values": {"p(95)": 70.0}},
    }
    for name, values in overrides.items():
        metrics[name] = {"values": values}  # type: ignore[assignment]
    return {"metrics": metrics}


# --- reading a file -------------------------------------------------------------------------


def test_a_summary_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "steady-run.json"
    path.write_text(json.dumps(a_summary()))
    assert load_summary(path)["metrics"]["http_reqs"]["values"]["count"] == 60_000


def test_a_missing_file_names_itself(tmp_path: Path) -> None:
    with pytest.raises(ResultError, match="steady-missing.json"):
        load_summary(tmp_path / "steady-missing.json")


def test_a_file_that_is_not_json_names_itself(tmp_path: Path) -> None:
    path = tmp_path / "steady-broken.json"
    path.write_text("k6 crashed and wrote this")
    with pytest.raises(ResultError, match="steady-broken.json"):
        load_summary(path)


def test_a_summary_without_metrics_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "steady-empty.json"
    path.write_text(json.dumps({"root_group": {}}))
    with pytest.raises(ResultError, match="no metrics"):
        load_summary(path)


# --- reading one number ---------------------------------------------------------------------


def test_a_metric_is_read_by_name_and_statistic() -> None:
    assert metric(a_summary(), "op_read_detail", "p(95)") == 21.0


def test_a_metric_the_run_never_recorded_is_none_not_zero() -> None:
    """A scenario that made no writes has no write percentile. Zero would read as a fast write."""

    summary = {"metrics": {"http_reqs": {"values": {"count": 10}}}}
    assert metric(summary, "op_write_create", "p(95)") is None


def test_a_statistic_the_metric_does_not_carry_is_none() -> None:
    assert metric(a_summary(), "op_read_list", "p(99)") is None


@pytest.mark.parametrize("operation", OPERATIONS)
def test_every_operation_the_scenarios_tag_is_readable(operation: str) -> None:
    assert metric(a_summary(), operation, "p(95)") is not None


# --- the headline ---------------------------------------------------------------------------


def test_the_headline_carries_every_number_the_nfr_table_needs() -> None:
    h = headline(a_summary())
    assert h["read_p50"] == 8.0
    assert h["read_p95"] == 21.0
    assert h["read_p99"] == 44.0
    assert h["write_create_p95"] == 60.0
    assert h["rps"] == 200.0


def test_a_run_with_no_dropped_iterations_metric_reports_zero() -> None:
    summary = a_summary()
    del summary["metrics"]["dropped_iterations"]
    assert headline(summary)["dropped_iterations"] == 0.0


# --- the noise floor ------------------------------------------------------------------------


def test_two_identical_runs_have_no_spread() -> None:
    floor = noise_floor(a_summary(), a_summary())
    assert floor["read_p95"] == 0.0


def test_the_spread_is_relative_to_the_mean() -> None:
    slower = a_summary(op_read_detail={"p(50)": 8.0, "p(95)": 23.0, "p(99)": 44.0})
    # 21 against 23: a difference of 2 over a mean of 22.
    assert noise_floor(a_summary(), slower)["read_p95"] == pytest.approx(2 / 22)


def test_a_metric_only_one_run_recorded_is_left_out_of_the_floor() -> None:
    partial = a_summary()
    del partial["metrics"]["op_write_update"]
    assert "write_update_p95" not in noise_floor(a_summary(), partial)


def test_a_zero_measurement_gives_a_zero_spread_rather_than_a_division_error() -> None:
    zeroed = a_summary(http_reqs={"count": 0, "rate": 0.0})
    assert noise_floor(zeroed, zeroed)["rps"] == 0.0


# --- what counts as a win -------------------------------------------------------------------


def test_a_change_larger_than_the_floor_counts() -> None:
    assert is_significant(before=100.0, after=80.0, floor=0.05)


def test_a_change_smaller_than_the_floor_is_drift() -> None:
    assert not is_significant(before=100.0, after=99.0, floor=0.05)


def test_a_change_exactly_on_the_floor_is_drift() -> None:
    """The floor is the size of a difference two identical runs already produce."""

    assert not is_significant(before=100.0, after=105.0, floor=5 / 102.5)


def test_a_regression_is_as_significant_as_an_improvement() -> None:
    assert is_significant(before=80.0, after=100.0, floor=0.05)


def test_a_negative_floor_is_rejected() -> None:
    with pytest.raises(ResultError, match="negative"):
        is_significant(before=100.0, after=80.0, floor=-0.1)


def test_two_zero_measurements_are_never_significant() -> None:
    assert not is_significant(before=0.0, after=0.0, floor=0.05)


# --- rendering ------------------------------------------------------------------------------


def test_a_missing_measurement_renders_as_a_dash_not_a_zero() -> None:
    assert fmt(None) == "—"


def test_a_measurement_renders_at_the_requested_precision() -> None:
    assert fmt(21.456, 2) == "21.46"


def test_the_table_carries_one_row_per_run() -> None:
    table = results_table({"A": a_summary(), "B": a_summary()})
    rows = [
        line for line in table.splitlines() if line.startswith("| A ") or line.startswith("| B ")
    ]
    assert len(rows) == 2


def test_the_table_keeps_the_run_order_it_was_given() -> None:
    table = results_table({"B": a_summary(), "A": a_summary()})
    assert table.index("| B ") < table.index("| A ")


def test_the_table_renders_a_missing_write_percentile_as_a_dash() -> None:
    partial = a_summary()
    del partial["metrics"]["op_write_update"]
    assert "| — |" in results_table({"A": partial})
