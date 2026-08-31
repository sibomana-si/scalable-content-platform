"""The fault injector, checked before it is trusted to prove anything.

`scripts/inject_fault.py` is the only way a chaos experiment reaches the system under test. A
toxic with the wrong attributes still returns 200 from the toxiproxy API, so a silent mapping bug
produces a run that looks clean and measures nothing. These tests pin the payload for each named
fault, and they run with no toxiproxy in sight.
"""

import pytest

from scripts.inject_fault import (
    FAULTS,
    TARGETS,
    FaultError,
    Toxiproxy,
    build_parser,
    main,
    toxic_for,
)


class FakeTransport:
    """A toxiproxy API that lives in a dictionary.

    Records every call, so a test can assert that `--clear` on an empty proxy sends no DELETE.
    """

    def __init__(self, toxics: dict[str, list[dict]] | None = None) -> None:
        self.toxics = toxics or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, payload: dict | None = None) -> object:
        self.calls.append((method, path, payload))
        target = path.split("/")[2]
        current = self.toxics.setdefault(target, [])
        if method == "GET":
            return list(current)
        if method == "POST":
            assert payload is not None
            current.append(payload)
            return payload
        if method == "DELETE":
            name = path.rsplit("/", 1)[-1]
            self.toxics[target] = [toxic for toxic in current if toxic["name"] != name]
            return None
        raise AssertionError(f"unexpected method {method}")


def a_client(toxics: dict[str, list[dict]] | None = None) -> tuple[Toxiproxy, FakeTransport]:
    transport = FakeTransport(toxics)
    return Toxiproxy("http://toxiproxy:8474", transport=transport), transport


# --- each fault maps to the toxic it claims to be -----------------------------------------------


def test_latency_carries_the_delay_in_milliseconds() -> None:
    toxic = toxic_for("latency", ms=800, jitter_ms=50)

    assert toxic["type"] == "latency"
    assert toxic["attributes"] == {"latency": 800, "jitter": 50}
    assert toxic["toxicity"] == 1.0


def test_timeout_closes_the_connection_after_the_given_wait() -> None:
    toxic = toxic_for("timeout", ms=2_000)

    assert toxic["type"] == "timeout"
    assert toxic["attributes"] == {"timeout": 2_000}


def test_blackhole_holds_the_connection_open_and_never_answers() -> None:
    """A toxiproxy `timeout` of 0 never closes, so the caller waits on its own bound.

    That is the fault the timeout and the breaker exist for: an unreachable server that does not
    have the courtesy to refuse the connection.
    """

    toxic = toxic_for("blackhole")

    assert toxic["type"] == "timeout"
    assert toxic["attributes"] == {"timeout": 0}


def test_reset_peer_refuses_immediately() -> None:
    toxic = toxic_for("reset_peer")

    assert toxic["type"] == "reset_peer"
    assert toxic["attributes"] == {"timeout": 0}


def test_every_fault_names_itself_so_clear_can_find_it() -> None:
    for fault in FAULTS:
        kwargs = {"ms": 100} if fault in {"latency", "timeout"} else {}
        assert toxic_for(fault, **kwargs)["name"] == fault


def test_every_fault_applies_downstream() -> None:
    """Upstream toxics delay the query, not the answer, which is a different experiment."""

    for fault in FAULTS:
        kwargs = {"ms": 100} if fault in {"latency", "timeout"} else {}
        assert toxic_for(fault, **kwargs)["stream"] == "downstream"


# --- the errors a typo produces -----------------------------------------------------------------


def test_an_unknown_fault_names_the_valid_set() -> None:
    with pytest.raises(FaultError) as error:
        toxic_for("brownout")

    message = str(error.value)
    assert "brownout" in message
    for fault in FAULTS:
        assert fault in message


def test_a_latency_fault_without_a_duration_names_the_missing_flag() -> None:
    """`latency` with no `--ms` would post a 0 ms delay and measure the proxy hop."""

    with pytest.raises(FaultError, match="--ms"):
        toxic_for("latency")


def test_a_timeout_fault_without_a_duration_names_the_missing_flag() -> None:
    with pytest.raises(FaultError, match="--ms"):
        toxic_for("timeout")


def test_a_negative_duration_is_refused() -> None:
    with pytest.raises(FaultError, match="--ms"):
        toxic_for("latency", ms=-1)


# --- the client -------------------------------------------------------------------------------


def test_applying_a_fault_posts_it_to_the_named_proxy() -> None:
    client, transport = a_client()

    client.apply("mysql", toxic_for("latency", ms=800))

    method, path, payload = transport.calls[-1]
    assert method == "POST"
    assert path == "/proxies/mysql/toxics"
    assert payload is not None and payload["attributes"]["latency"] == 800


def test_applying_the_same_fault_twice_replaces_it() -> None:
    """Toxiproxy rejects a duplicate name with 409, and a chaos run must not stop on one."""

    client, transport = a_client()

    client.apply("mysql", toxic_for("latency", ms=800))
    client.apply("mysql", toxic_for("latency", ms=1_600))

    assert [toxic["attributes"]["latency"] for toxic in transport.toxics["mysql"]] == [1_600]


def test_clear_removes_every_toxic_on_the_target() -> None:
    client, transport = a_client({"mysql": [{"name": "latency"}, {"name": "blackhole"}]})

    removed = client.clear("mysql")

    assert removed == ["latency", "blackhole"]
    assert transport.toxics["mysql"] == []


def test_clear_is_idempotent_and_sends_no_delete_when_there_is_nothing_to_clear() -> None:
    client, transport = a_client({"mysql": []})

    assert client.clear("mysql") == []
    assert [method for method, _, _ in transport.calls] == ["GET"]


def test_clear_leaves_the_other_target_alone() -> None:
    """A chaos run often holds one fault while it clears another."""

    client, transport = a_client({"mysql": [{"name": "latency"}], "redis": [{"name": "blackhole"}]})

    client.clear("mysql")

    assert [toxic["name"] for toxic in transport.toxics["redis"]] == ["blackhole"]


def test_active_reports_what_is_currently_injected() -> None:
    client, _ = a_client({"redis": [{"name": "blackhole", "type": "timeout"}]})

    assert [toxic["name"] for toxic in client.active("redis")] == ["blackhole"]


# --- the command line ---------------------------------------------------------------------------


def test_the_parser_accepts_every_target() -> None:
    parser = build_parser()

    for target in TARGETS:
        assert parser.parse_args(["--target", target, "--clear"]).target == target


def test_the_parser_refuses_an_unknown_target() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--target", "kafka", "--clear"])


def test_list_prints_the_active_toxics(capsys: pytest.CaptureFixture[str]) -> None:
    client, _ = a_client({"mysql": [{"name": "latency", "type": "latency"}]})

    assert main(["--target", "mysql", "--list"], client=client) == 0

    assert "latency" in capsys.readouterr().out


def test_list_says_so_when_nothing_is_injected(capsys: pytest.CaptureFixture[str]) -> None:
    client, _ = a_client({"mysql": []})

    main(["--target", "mysql", "--list"], client=client)

    assert "no toxics" in capsys.readouterr().out.lower()


def test_injecting_from_the_command_line_reaches_the_proxy() -> None:
    client, transport = a_client()

    assert main(["--target", "mysql", "latency", "--ms", "800"], client=client) == 0

    assert transport.toxics["mysql"][0]["attributes"]["latency"] == 800


def test_clearing_from_the_command_line_is_idempotent() -> None:
    client, _ = a_client({"mysql": [{"name": "latency"}]})

    assert main(["--target", "mysql", "--clear"], client=client) == 0
    assert main(["--target", "mysql", "--clear"], client=client) == 0


def test_a_bad_fault_from_the_command_line_exits_non_zero_without_calling_out(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client, transport = a_client()

    assert main(["--target", "mysql", "latency"], client=client) == 2

    assert transport.calls == []
    assert "--ms" in capsys.readouterr().err
