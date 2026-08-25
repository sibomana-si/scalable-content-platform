"""The load-test seeder's arithmetic and distribution, without touching a database.

The seeder decides two things no later stage can correct: how many rows exist, and how skewed
the authorship is. Both live in pure functions, so a wrong dataset fails in milliseconds
instead of after a five-minute run.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.seed_load_dataset import (
    DEFAULT_ARTICLES,
    DEFAULT_AUTHORS,
    DEFAULT_HOT_SHARE,
    SeedPlan,
    SeedResult,
    author_slots,
    build_body,
    parse_args,
    parse_cli,
    plan_batches,
    range_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def a_plan(**overrides: object) -> SeedPlan:
    defaults: dict[str, object] = {
        "articles": 1_000,
        "authors": 50,
        "hot_share": 0.2,
        "min_body_bytes": 2_048,
        "max_body_bytes": 8_192,
        "batch_size": 100,
        "seed": 1337,
    }
    return SeedPlan(**{**defaults, **overrides})  # type: ignore[arg-type]


# --- batching ---------------------------------------------------------------------------------


def test_the_batches_add_up_to_the_missing_rows() -> None:
    batches = plan_batches(missing=1_050, batch_size=400)

    assert batches == [400, 400, 250]
    assert sum(batches) == 1_050


def test_an_exact_multiple_needs_no_short_last_batch() -> None:
    assert plan_batches(missing=800, batch_size=400) == [400, 400]


def test_a_full_dataset_needs_no_batches() -> None:
    """Idempotence: a second run with nothing missing must do no work at all."""

    assert plan_batches(missing=0, batch_size=400) == []


def test_a_batch_size_of_zero_is_rejected() -> None:
    with pytest.raises(ValueError, match="batch size"):
        plan_batches(missing=10, batch_size=0)


def test_a_negative_missing_count_is_rejected() -> None:
    """More rows than asked for is a state the seeder must report, not silently accept."""

    with pytest.raises(ValueError, match="missing"):
        plan_batches(missing=-1, batch_size=400)


# --- the plan ---------------------------------------------------------------------------------


def test_the_defaults_match_the_capacity_model() -> None:
    plan = parse_args([])

    assert plan.articles == DEFAULT_ARTICLES == 10_000
    assert plan.authors == DEFAULT_AUTHORS
    assert plan.hot_share == DEFAULT_HOT_SHARE
    assert (plan.min_body_bytes, plan.max_body_bytes) == (2_048, 8_192)


def test_the_body_bounds_parse_from_one_flag() -> None:
    plan = parse_args(["--body-bytes", "100-200"])

    assert (plan.min_body_bytes, plan.max_body_bytes) == (100, 200)


def test_zero_articles_is_a_valid_no_op() -> None:
    plan = parse_args(["--articles", "0"])

    assert plan.articles == 0
    assert plan_batches(missing=plan.articles, batch_size=plan.batch_size) == []


def test_a_negative_article_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="articles"):
        a_plan(articles=-1)


def test_a_hot_share_of_one_is_rejected() -> None:
    """Every author hot means no skew at all, which is the workload this run must not have."""

    with pytest.raises(ValueError, match="hot share"):
        a_plan(hot_share=1.0)


def test_inverted_body_bounds_are_rejected() -> None:
    with pytest.raises(ValueError, match="body"):
        a_plan(min_body_bytes=8_192, max_body_bytes=2_048)


# --- bodies -----------------------------------------------------------------------------------


def test_every_body_stays_inside_the_documented_byte_bounds() -> None:
    import random

    rng = random.Random(1337)
    plan = a_plan()

    for _ in range(500):
        body = build_body(rng, plan.min_body_bytes, plan.max_body_bytes)
        assert plan.min_body_bytes <= len(body.encode()) <= plan.max_body_bytes


def test_a_body_is_ascii_so_bytes_and_characters_agree() -> None:
    """The capacity model sizes Redis in bytes. A multi-byte body would understate that."""

    import random

    body = build_body(random.Random(1), 2_048, 8_192)

    assert body.isascii()
    assert len(body) == len(body.encode())


def test_the_smallest_allowed_body_is_produced_exactly() -> None:
    import random

    body = build_body(random.Random(7), 16, 16)

    assert len(body) == 16


# --- authorship skew --------------------------------------------------------------------------


def test_the_hot_authors_write_the_documented_majority() -> None:
    """A hot share of 0.2 means 20% of the authors write about 80% of the articles."""

    plan = a_plan(articles=10_000, authors=50, hot_share=0.2)
    hot_count = int(plan.authors * plan.hot_share)

    slots = author_slots(plan)
    hot_written = sum(1 for slot in slots if slot < hot_count)

    assert hot_count == 10
    assert 0.77 <= hot_written / len(slots) <= 0.83


def test_no_author_is_starved() -> None:
    """A cold author with no articles makes the author filter untestable under load."""

    plan = a_plan(articles=10_000, authors=50, hot_share=0.2)

    assert len(set(author_slots(plan))) == plan.authors


def test_a_hot_share_of_zero_spreads_the_articles_evenly() -> None:
    plan = a_plan(articles=10_000, authors=10, hot_share=0.0)

    counts = [author_slots(plan).count(index) for index in range(plan.authors)]

    assert all(800 <= count <= 1_200 for count in counts), counts


def test_the_same_seed_produces_the_same_dataset() -> None:
    """Two runs of the matrix must meet the same rows, or the comparison is not one."""

    assert author_slots(a_plan(seed=42)) == author_slots(a_plan(seed=42))


def test_a_different_seed_produces_a_different_dataset() -> None:
    assert author_slots(a_plan(seed=42)) != author_slots(a_plan(seed=43))


def test_every_slot_names_a_real_author() -> None:
    plan = a_plan(articles=2_000, authors=7, hot_share=0.3)

    assert all(0 <= slot < plan.authors for slot in author_slots(plan))


def test_the_script_runs_as_a_command_from_the_repository_root() -> None:
    """The runbook documents ``python scripts/seed_load_dataset.py``.

    Run that way, ``sys.path[0]`` is ``scripts/``, not the repository root, so ``import app``
    fails. ``pytest`` hides the problem because ``pythonpath = ["."]`` puts the root on the path
    for it. This test runs the documented command, so the two cannot diverge.
    """

    result = subprocess.run(
        [sys.executable, "scripts/seed_load_dataset.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--articles" in result.stdout


# --- The id range the load generator must draw from ------------------------------------------
#
# The generator picks article ids. MySQL `AUTO_INCREMENT` does not restart at 1 after a delete,
# so the seeded block starts wherever the counter happened to be. A generator that assumes
# 1..10,000 reads ids that do not exist, and every 404 is a cheap miss that never populates the
# cache — the hit ratio then measures the wrong thing while looking plausible.


def a_result(**overrides: object) -> SeedResult:
    defaults: dict[str, object] = {
        "authors_created": 0,
        "articles_before": 0,
        "articles_created": 10,
        "id_min": 5_001,
        "id_max": 5_010,
    }
    defaults.update(overrides)
    return SeedResult(**defaults)  # type: ignore[arg-type]


def test_the_range_payload_carries_the_observed_ids() -> None:
    assert range_payload(a_result()) == {"id_min": 5_001, "id_max": 5_010, "articles": 10}


def test_the_range_payload_counts_rows_that_were_already_there() -> None:
    payload = range_payload(a_result(articles_before=90, articles_created=10, id_max=5_100))
    assert payload["articles"] == 100


def test_a_dataset_with_rows_but_no_ids_is_an_error() -> None:
    with pytest.raises(ValueError, match="id range"):
        range_payload(a_result(id_min=None, id_max=None))


def test_an_empty_dataset_reports_an_empty_range() -> None:
    payload = range_payload(a_result(articles_created=0, id_min=None, id_max=None))
    assert payload == {"id_min": None, "id_max": None, "articles": 0}


def test_an_inverted_range_is_an_error() -> None:
    with pytest.raises(ValueError, match="id range"):
        range_payload(a_result(id_min=5_010, id_max=5_001))


def test_the_range_file_path_is_optional() -> None:
    _, emit_range = parse_cli([])
    assert emit_range is None


def test_the_range_file_path_is_read_from_the_command_line() -> None:
    plan, emit_range = parse_cli(["--articles", "50", "--emit-range", "/results/dataset.json"])
    assert emit_range == Path("/results/dataset.json")
    assert plan.articles == 50
