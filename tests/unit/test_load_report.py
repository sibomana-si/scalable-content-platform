"""The load-test report, made executable.

The report is the artifact a reader trusts without having been in the room, and the two ways it
goes wrong are silent. A placeholder survives the edit, so a template ships as a result. Or a
number in the report and the same number in the NFR table drift apart, and nobody can tell which
one the run produced.

Follows the `test_capacity_model.py` precedent: a document that makes claims gets a test.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = REPO_ROOT / "docs" / "performance" / "load-test-report.md"
NFR = REPO_ROOT / "docs" / "requirements" / "non-functional-requirements.md"
RESULTS = REPO_ROOT / "docs" / "performance" / "results"

# The rows of the NFR Performance table, and the label each one carries.
PERFORMANCE_ROWS = (
    "read p50 latency",
    "read p95 latency",
    "read p99 latency",
    "write p95 latency",
    "throughput (sustained)",
)


def report() -> str:
    return REPORT.read_text()


def nfr_rows() -> dict[str, list[str]]:
    """Metric name -> the cells of its row, from every table in the NFR document."""

    rows = {}
    for line in NFR.read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and not cells[0].startswith("-"):
            rows[cells[0].lower()] = cells
    return rows


# --- the report is finished ---------------------------------------------------------------------


def test_the_report_exists() -> None:
    assert REPORT.is_file()


def test_no_template_placeholder_survives() -> None:
    """The template marks every empty cell `_..._`. One left behind ships a form as a finding."""

    assert "_..._" not in report()


def test_the_report_is_no_longer_marked_as_not_started() -> None:
    assert "🟥 Not started" not in report()


def test_the_results_table_has_no_empty_cells() -> None:
    """A results row with blank cells reads as a measurement of zero."""

    for line in report().splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set("".join(cells)) <= set("-: "):
            continue
        assert all(cells), f"empty cell in report row: {line.strip()}"


# --- it agrees with the requirement it answers ----------------------------------------------------


@pytest.mark.parametrize("row", PERFORMANCE_ROWS)
def test_every_performance_row_carries_a_measurement(row: str) -> None:
    """M6 is what fills these. A row still reading `pending` means the report is not done."""

    measured = nfr_rows()[row][2]

    assert "pending" not in measured.lower(), f"{row} is still pending"
    assert re.search(r"\d", measured), f"{row} records no number"


@pytest.mark.parametrize("row", PERFORMANCE_ROWS)
def test_every_performance_row_reaches_a_verdict(row: str) -> None:
    status = nfr_rows()[row][3]

    assert status in {"✅", "🟥", "🟨"}, f"{row} carries no status"


def test_the_nfr_points_at_the_report_for_its_evidence() -> None:
    assert "load-test-report.md" in NFR.read_text()


# --- every artifact it names is on disk ------------------------------------------------------


def test_every_image_the_report_shows_exists() -> None:
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report()):
        assert (REPORT.parent / target).is_file(), f"the report shows a missing image: {target}"


def test_every_results_file_the_report_names_exists() -> None:
    for name in re.findall(r"`((?:steady|ramp|spike)-[\w.-]+\.json)`", report()):
        assert (RESULTS / name).is_file(), f"the report names a missing result: {name}"


def test_the_report_links_to_the_plan_and_the_analysis() -> None:
    """A number without its method is a claim. The report has to say where to check it."""

    text = report()

    assert "load-test-plan.md" in text
    assert "bottleneck-analysis.md" in text


# --- the caveats the plan requires in the summary, not in a footnote ----------------------------


def test_the_summary_states_the_shared_host_caveat() -> None:
    """One laptop runs the service and the generator. A reader who misses that over-trusts the
    absolute numbers."""

    summary = report().split("##", 2)[1].lower()

    assert "generator" in summary
    assert "same" in summary or "shared" in summary


def test_the_summary_states_the_noise_floor() -> None:
    """A reader must be able to tell a real win from run-to-run drift."""

    assert "noise floor" in report().split("##", 2)[1].lower()
