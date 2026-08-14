"""Wiring checks for the metrics layer: the scrape endpoint and the request instrumentation.

These run against the process-wide default registry (the collectors are module-level, once
per-process), so every assertion is on the delta across a request rather than an absolute count.
"""

from httpx import AsyncClient
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY


def counter(route: str, method: str = "GET", status: str = "200") -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total", {"route": route, "method": method, "status": status}
    )
    return value or 0.0


def duration_count(route: str, method: str = "GET") -> float:
    value = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count", {"route": route, "method": method}
    )
    return value or 0.0


async def test_metrics_endpoint_is_scrapeable_without_a_token(client: AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"] == CONTENT_TYPE_LATEST
    assert "http_requests_total" in response.text


async def test_exposition_declares_the_documented_collectors(client: AsyncClient) -> None:
    body = (await client.get("/metrics")).text

    assert "# TYPE http_requests_total counter" in body
    assert "# TYPE http_request_duration_seconds histogram" in body
    assert "# TYPE db_query_duration_seconds histogram" in body


# /openapi.json rather than a health route: probes are excluded from the RED metrics,
# so they cannot carry these assertions.


async def test_a_request_increments_the_counter_for_its_own_route(client: AsyncClient) -> None:
    before = counter("/openapi.json")

    await client.get("/openapi.json")
    assert counter("/openapi.json") == before + 1


async def test_a_request_records_one_latency_observation(client: AsyncClient) -> None:
    before = duration_count("/openapi.json")
    await client.get("/openapi.json")

    assert duration_count("/openapi.json") == before + 1


async def test_error_responses_are_counted_under_their_status(client: AsyncClient) -> None:
    before = counter("/v1/articles", method="POST", status="401")
    response = await client.post("/v1/articles", json={"title": "t", "body": "b"})
    assert response.status_code == 401

    # The 401 is produced inside AuthMiddleware, below the metrics layer: a request rejected
    # by authorization must still show up in the error rate.
    assert counter("/v1/articles", method="POST", status="401") == before + 1


async def test_unmatched_paths_do_not_mint_a_series_per_path(client: AsyncClient) -> None:
    before = counter("__unmatched__", status="404")

    for i in range(3):
        await client.get(f"/health/scanner-probe-{i}")

    assert counter("__unmatched__", status="404") == before + 3

    body = (await client.get("/metrics")).text

    assert "scanner-probe" not in body


async def test_health_probes_are_excluded(client: AsyncClient) -> None:
    before_live = counter("/health/live")
    before_ready = duration_count("/health/ready")

    await client.get("/health/live")
    await client.get("/health/live")
    await client.get("/health/ready")

    # Kubernetes probes are infrastructure, not traffic: counted, they dilute the availability
    # ratio and the error budget, and they hold NoTrafficReceived permanently silent.
    assert counter("/health/live") == before_live
    assert duration_count("/health/ready") == before_ready


async def test_the_scrape_endpoint_excludes_itself(client: AsyncClient) -> None:
    before = counter("/metrics")
    await client.get("/metrics")
    await client.get("/metrics")

    # Self-instrumentation would make the scrape interval itself look like traffic.
    assert counter("/metrics") == before
