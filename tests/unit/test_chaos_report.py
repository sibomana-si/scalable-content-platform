"""The chaos report, made executable.

A chaos report goes wrong the same two silent ways the load report does. A placeholder survives
the edit, so a template ships as a finding. Or the report cites evidence that is not on disk, and
nobody can check the number it rests on.

One check has no counterpart in `test_load_report.py`: every experiment needs an observed behavior
*and* a verdict. A row with a hypothesis and no observation is a plan, and this document is the
place a plan stops being acceptable.

Follows the `test_capacity_model.py` precedent: a document that makes claims gets a test.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT = REPO_ROOT / "docs" / "resilience" / "chaos-test-report.md"
NFR = REPO_ROOT / "docs" / "requirements" / "non-functional-requirements.md"
CAPACITY = REPO_ROOT / "docs" / "architecture" / "capacity-scaling-model.md"
RESULTS = REPO_ROOT / "docs" / "resilience" / "results"

# The experiment set the runbook defines. Numbered, because the report is read by number.
EXPERIMENTS = ("0", "1", "2", "3", "4")

VERDICTS = {"🟩", "🟨", "🟥"}


def report() -> str:
    return REPORT.read_text()


def table_rows(text: str) -> list[list[str]]:
    """Every table row in the document, as its stripped cells. Separator rows are dropped."""

    rows = []
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def experiment_rows() -> dict[str, list[str]]:
    """The rows of the Experiments table, keyed by experiment number."""

    section = report().split("## Experiments", 1)[1].split("\n## ", 1)[0]
    return {cells[0]: cells for cells in table_rows(section) if cells[0] in EXPERIMENTS}


# --- the report is finished ---------------------------------------------------------------------


def test_the_report_exists() -> None:
    assert REPORT.is_file()


def test_no_template_placeholder_survives() -> None:
    """The template marks every empty cell `_..._`. One left behind ships a form as a result."""

    assert "_..._" not in report()


def test_no_italic_placeholder_survives() -> None:
    """The template also leaves whole sections as one italic line. Those are placeholders too."""

    leftovers = [
        line.strip() for line in report().splitlines() if re.fullmatch(r"_[^_]{10,}_", line.strip())
    ]

    assert not leftovers, f"placeholder lines survive: {leftovers}"


def test_the_report_records_no_unstarted_experiment() -> None:
    """🟥 in this document means an experiment that did not run, not a target that was missed."""

    assert "🟥" not in report()


def test_the_status_line_is_no_longer_a_baseline() -> None:
    status = report().splitlines()[3]

    assert "🟨" not in status, "the report still calls itself a baseline"


# --- every experiment reaches an observation and a verdict ---------------------------------------


@pytest.mark.parametrize("number", EXPERIMENTS)
def test_every_experiment_has_a_row(number: str) -> None:
    assert number in experiment_rows(), f"experiment {number} is missing from the table"


@pytest.mark.parametrize("number", EXPERIMENTS)
def test_every_experiment_records_an_observed_behavior(number: str) -> None:
    """The column that separates a result from a plan."""

    observed = experiment_rows()[number][3]

    assert len(observed) > 20, f"experiment {number} records no observed behavior"
    assert re.search(r"\d", observed), f"experiment {number} observes no number"


@pytest.mark.parametrize("number", EXPERIMENTS)
def test_every_experiment_reaches_a_verdict(number: str) -> None:
    verdict = experiment_rows()[number][4]

    assert verdict in VERDICTS, f"experiment {number} carries no verdict: {verdict!r}"


def test_no_table_row_has_an_empty_cell() -> None:
    """A blank cell in a results table reads as a measurement of zero."""

    for cells in table_rows(report()):
        assert all(cells), f"empty cell in report row: {cells}"


# --- every artifact it names is on disk -----------------------------------------------------------


def test_the_results_directory_exists() -> None:
    assert RESULTS.is_dir(), "the report has nowhere to keep its evidence"


def test_every_evidence_file_the_report_names_exists() -> None:
    for name in re.findall(r"`(chaos-[\w.-]+\.json)`", report()):
        assert (RESULTS / name).is_file(), f"the report names a missing result: {name}"


def test_every_image_the_report_shows_exists() -> None:
    for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", report()):
        assert (REPORT.parent / target).is_file(), f"the report shows a missing image: {target}"


def test_the_report_links_to_its_method_and_its_design() -> None:
    """A number without its method is a claim. The report has to say where to check it."""

    text = report()

    assert "chaos-test-runbook.md" in text
    assert "fault-tolerance-design.md" in text


# --- the caveats belong in the summary, not in a footnote -----------------------------------------


def test_the_method_states_the_proxy_hop_is_in_the_baseline() -> None:
    """Measuring a fault against the M6 figures would charge the proxy to the fault."""

    method = report().split("## Method", 1)[1].split("\n## ", 1)[0].lower()

    assert "baseline" in method
    assert "proxy" in method


def test_the_report_states_its_noise_floor() -> None:
    """Without a run and its repeat, no reader can tell a real change from drift."""

    assert "noise floor" in report().lower()


# --- the documents that depend on it -------------------------------------------------------------


def test_the_nfr_degradation_row_points_at_the_report() -> None:
    row = next(cells for cells in table_rows(NFR.read_text()) if cells[0] == "Graceful degradation")

    assert "chaos-test-report.md" in row[1], "the NFR row cites no evidence"


def test_the_capacity_model_fall_through_row_is_measured() -> None:
    row = next(
        cells
        for cells in table_rows(CAPACITY.read_text())
        if cells[0].startswith("Fall-through survival")
    )

    assert "pending" not in row[1].lower(), "the fall-through row is still pending"
    assert re.search(r"\d", row[1]), "the fall-through row records no number"
    assert row[2] in VERDICTS, f"the fall-through row carries no verdict: {row[2]!r}"
