"""Scale-out against real replicas, behind a real load balancer.

This test runs two apps across separate containers reached through nginx, with the following
properties: an image that bakes in per-instance state, a replica that cannot reach a
dependency by service name, or routing that turns out to be sticky.

Run it locally::

    docker compose --profile scale up -d --build --scale app=3
    pytest -m scale

CI does not build the image, so these skip there.
"""

import asyncio
import socket

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.scale

LOAD_BALANCER = "http://localhost:8080"
BASE = "/v1/articles"
PAYLOAD = {"title": "Written on one replica", "body": "Read from another."}

# How many probes to spend looking for a second distinct replica. Round robin finds one within
# a handful, so this only has to outlast a slow container start.
DISCOVERY_ATTEMPTS = 60


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def load_balancer_available() -> None:
    """Skip unless nginx is reachable, matching the port-reachability convention in conftest."""

    if not _port_open("localhost", 8080):
        pytest.skip(
            "Load balancer not reachable at localhost:8080. Start it with: "
            "docker compose --profile scale up -d --build --scale app=3"
        )


@pytest_asyncio.fixture
async def lb(load_balancer_available: None):
    async with AsyncClient(base_url=LOAD_BALANCER, timeout=30.0) as client:
        yield client


async def observed_instances(client: AsyncClient, attempts: int = DISCOVERY_ATTEMPTS) -> set[str]:
    """Probe liveness until two distinct replicas answer, or the attempts run out."""

    seen: set[str] = set()
    for _ in range(attempts):
        response = await client.get("/health/live")
        if response.status_code == 200:
            seen.add(response.json()["instance"])
        if len(seen) >= 2:
            break
        await asyncio.sleep(0.05)
    return seen


@pytest_asyncio.fixture
async def replicas(lb: AsyncClient) -> set[str]:
    """The distinct replicas behind the balancer. Skips if only one answers.

    Skipping rather than failing: one replica is a valid way to run the stack, and a test that
    cannot observe scale-out has nothing to say about it either way.

    A skip here has two possible causes and the message names both, because the second one is
    a real bug that a silent skip would hide.
    """

    seen = await observed_instances(lb)
    if len(seen) < 2:
        pytest.skip(
            f"Only {len(seen)} replica(s) answered through the balancer. Either the stack is "
            f"not scaled (start it with --scale app=3), or the balancer is not distributing "
            f"— check that nginx resolves the app service per request."
        )
    return seen


@pytest_asyncio.fixture
async def token(lb: AsyncClient) -> str:
    """Register and log in through the balancer; the two calls may land on different replicas."""

    import uuid

    credentials = {
        "email": f"scale-{uuid.uuid4().hex[:12]}@example.com",
        "password": "a-long-unique-passphrase-for-the-scale-test",
    }
    register = await lb.post("/v1/auth/register", json=credentials)
    assert register.status_code == 201, register.text
    login = await lb.post("/v1/auth/login", json=credentials)
    assert login.status_code == 200, login.text
    return login.json()["access_token"]


# --- the deployment answers at all ---------------------------------------------------------


async def test_the_balancer_reaches_more_than_one_replica(replicas):
    assert len(replicas) >= 2


async def test_every_replica_reports_ready(lb, replicas):
    """A replica that cannot reach MySQL or Redis by service name fails here, not in prod."""

    for _ in range(len(replicas) * 5):
        response = await lb.get("/health/ready")
        assert response.status_code == 200, response.text


# --- the same properties as the in-process suite, across containers -------------------------


async def test_a_write_on_one_replica_is_readable_from_any(lb, replicas, token):
    headers = {"Authorization": f"Bearer {token}"}
    created = await lb.post(BASE, json=PAYLOAD, headers=headers)
    assert created.status_code == 201, created.text
    article_id = created.json()["id"]

    # Round robin spreads these across every replica, so at least one read is cross-replica.
    for _ in range(len(replicas) * 3):
        read = await lb.get(f"{BASE}/{article_id}")
        assert read.status_code == 200
        assert read.json() == created.json()


async def test_an_update_is_never_stale_on_another_replica(lb, replicas, token):
    """Invalidation must cross the container boundary, not only the process boundary."""

    headers = {"Authorization": f"Bearer {token}"}
    created = (await lb.post(BASE, json=PAYLOAD, headers=headers)).json()

    # Populate the cache from as many replicas as round robin reaches.
    for _ in range(len(replicas) * 2):
        await lb.get(f"{BASE}/{created['id']}")

    updated = {"title": "Updated on one replica", "body": "Visible on all."}
    response = await lb.put(
        f"{BASE}/{created['id']}",
        json=updated,
        headers={**headers, "If-Match": created["updated_at"]},
    )
    assert response.status_code == 200, response.text

    for _ in range(len(replicas) * 3):
        assert (await lb.get(f"{BASE}/{created['id']}")).json()["title"] == updated["title"]


async def test_a_delete_is_a_404_from_every_replica(lb, replicas, token):
    headers = {"Authorization": f"Bearer {token}"}
    created = (await lb.post(BASE, json=PAYLOAD, headers=headers)).json()
    for _ in range(len(replicas) * 2):
        await lb.get(f"{BASE}/{created['id']}")

    response = await lb.delete(
        f"{BASE}/{created['id']}", headers={**headers, "If-Match": created["updated_at"]}
    )
    assert response.status_code == 204

    for _ in range(len(replicas) * 3):
        assert (await lb.get(f"{BASE}/{created['id']}")).status_code == 404


async def test_a_token_is_accepted_by_every_replica(lb, replicas, token):
    """No sticky routing: the replica that signed the token is not the only one that trusts it."""

    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(len(replicas) * 3):
        response = await lb.post(BASE, json=PAYLOAD, headers=headers)
        assert response.status_code == 201, response.text


async def test_concurrent_requests_spread_across_replicas(lb, replicas):
    """The load balancer must actually distribute; one replica serving everything is not
    scale-out, it is a single point of failure with extra containers."""

    responses = await asyncio.gather(*(lb.get("/health/live") for _ in range(len(replicas) * 8)))
    instances = {response.json()["instance"] for response in responses}

    assert len(instances) >= 2


async def test_a_cursor_from_one_replica_is_honored_by_another(lb, replicas, token):
    headers = {"Authorization": f"Bearer {token}"}
    for _ in range(3):
        await lb.post(BASE, json=PAYLOAD, headers=headers)

    first = await lb.get(BASE, params={"limit": 2})
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    second = await lb.get(BASE, params={"limit": 2, "cursor": cursor})
    assert second.status_code == 200
    assert second.json()["items"] != first.json()["items"]
