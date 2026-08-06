"""The DB latency histogram is fed by real SQLAlchemy execution, against a real MySQL.

Repositories and the instrumentation that wraps them are never exercised against mocks,
so this needs the compose stack.
"""

import pytest
from httpx import AsyncClient
from prometheus_client import REGISTRY

pytestmark = pytest.mark.integration


def query_count(operation: str) -> float:
    value = REGISTRY.get_sample_value("db_query_duration_seconds_count", {"query": operation})
    return value or 0.0


def query_sum(operation: str) -> float:
    value = REGISTRY.get_sample_value("db_query_duration_seconds_sum", {"query": operation})
    return value or 0.0


async def test_a_read_request_records_select_latency(client: AsyncClient, clean_db: None) -> None:
    before_count, before_sum = query_count("select"), query_sum("select")
    response = await client.get("/v1/articles")

    assert response.status_code == 200
    assert query_count("select") > before_count
    assert query_sum("select") > before_sum  # a real query takes non-zero time


async def test_the_query_label_stays_within_the_closed_verb_set(
    client: AsyncClient, clean_db: None
) -> None:
    await client.get("/v1/articles")

    labels = {
        sample.labels["query"]
        for metric in REGISTRY.collect()
        if metric.name == "db_query_duration_seconds"
        for sample in metric.samples
    }

    assert labels <= {"select", "insert", "update", "delete", "other"}


async def test_no_sql_text_reaches_the_exposition(client: AsyncClient, clean_db: None) -> None:
    await client.get("/v1/articles")

    body = (await client.get("/metrics")).text

    assert "FROM articles" not in body
    assert "deleted_at" not in body
