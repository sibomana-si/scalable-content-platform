"""The report has to carry its own graphs.

Grafana renders panels in a browser, and nothing in this environment drives one, so the charts
come from the same Prometheus range queries the analysis reads. That makes them reproducible
rather than photographed. These tests cover the arithmetic that turns a series into coordinates,
which is where a chart lies without ever failing.
"""

import pytest

from scripts.plot_load_results import (
    ChartError,
    Series,
    escape,
    project,
    render_chart,
)


def series(name: str, points: list[tuple[float, float]], color: str = "#1f77b4") -> Series:
    return Series(name=name, points=points, color=color)


# --- projecting a series onto the drawing area ------------------------------------------------


def test_the_lowest_value_sits_at_the_bottom_and_the_highest_at_the_top() -> None:
    coords = project([(0.0, 0.0), (10.0, 100.0)], x_range=(0.0, 10.0), y_max=100.0, box=(200, 80))

    assert coords[0] == (0.0, 80.0)
    assert coords[1] == (200.0, 0.0)


def test_the_y_axis_starts_at_zero_so_a_small_change_cannot_look_large() -> None:
    """A chart auto-scaled to its own minimum turns a 2% wobble into a cliff."""

    coords = project([(0.0, 90.0), (1.0, 100.0)], x_range=(0.0, 1.0), y_max=100.0, box=(100, 100))

    assert coords[0][1] == pytest.approx(10.0)


def test_a_flat_series_does_not_divide_by_zero() -> None:
    coords = project([(0.0, 5.0), (1.0, 5.0)], x_range=(0.0, 1.0), y_max=5.0, box=(100, 100))

    assert coords[0][1] == coords[1][1] == 0.0


def test_a_zero_series_draws_along_the_baseline() -> None:
    coords = project([(0.0, 0.0), (1.0, 0.0)], x_range=(0.0, 1.0), y_max=0.0, box=(100, 100))

    assert coords[0][1] == coords[1][1] == 100.0


def test_a_single_point_series_does_not_divide_by_zero_on_the_time_axis() -> None:
    coords = project([(7.0, 3.0)], x_range=(7.0, 7.0), y_max=3.0, box=(100, 100))

    assert coords == [(0.0, 0.0)]


# --- the rendered document --------------------------------------------------------------------


def test_the_chart_carries_a_viewbox_so_it_scales_with_the_page() -> None:
    svg = render_chart([series("rps", [(0.0, 1.0), (1.0, 2.0)])], title="Rate", y_label="rps")

    assert "viewBox=" in svg
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")


def test_every_series_becomes_one_polyline() -> None:
    svg = render_chart(
        [series("a", [(0.0, 1.0), (1.0, 2.0)]), series("b", [(0.0, 2.0), (1.0, 1.0)], "#d62728")],
        title="Two",
        y_label="ms",
    )

    assert svg.count("<polyline") == 2


def test_every_series_is_named_in_the_legend() -> None:
    svg = render_chart([series("read p95", [(0.0, 1.0), (1.0, 2.0)])], title="T", y_label="ms")

    assert "read p95" in svg


def test_the_title_and_the_axis_label_reach_the_document() -> None:
    svg = render_chart([series("s", [(0.0, 1.0)])], title="Ramp to the knee", y_label="requests/s")

    assert "Ramp to the knee" in svg
    assert "requests/s" in svg


def test_a_chart_with_no_series_is_an_error_not_an_empty_picture() -> None:
    with pytest.raises(ChartError):
        render_chart([], title="Nothing", y_label="ms")


def test_a_series_with_no_points_is_an_error() -> None:
    """An empty series means the query returned nothing. A blank chart would hide that."""

    with pytest.raises(ChartError):
        render_chart([series("empty", [])], title="Nothing", y_label="ms")


def test_the_theme_follows_the_reader_rather_than_baking_in_white() -> None:
    """The docs render in both themes. A chart with a hard white ground goes black-on-black."""

    svg = render_chart([series("s", [(0.0, 1.0), (1.0, 2.0)])], title="T", y_label="ms")

    assert "prefers-color-scheme: dark" in svg


# --- text that reaches an XML document ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("p95 < 200 ms", "p95 &lt; 200 ms"),
        ("a & b", "a &amp; b"),
        ('say "go"', "say &quot;go&quot;"),
    ],
)
def test_markup_characters_are_escaped(raw: str, expected: str) -> None:
    assert escape(raw) == expected


def test_a_title_with_a_markup_character_does_not_break_the_document() -> None:
    svg = render_chart([series("s", [(0.0, 1.0)])], title="P95 < 200 ms", y_label="ms")

    assert "P95 &lt; 200 ms" in svg
    assert "P95 < 200" not in svg
