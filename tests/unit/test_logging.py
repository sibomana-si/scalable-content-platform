"""Unit tests for the structured-logging processors.

Pure functions only: every logging decision (what gets censored, which keys the renderer
emits, how the level filter behaves) lives in a side-effect-free processor so it can be
driven directly, without an app, a request, or a socket.
"""

import io
import json
from collections.abc import Iterator

import pytest
import structlog

from app.observability.logging import (
    REDACTED,
    build_processors,
    configure_logging,
    get_logger,
    redact_secrets,
)


def _redact(event_dict: dict) -> dict:
    """Drive the processor the way structlog would."""
    return redact_secrets(None, "info", event_dict)


def _render(event_dict: dict, *, fmt: str = "json", method: str = "info") -> str:
    """Run an event dict through the full configured chain and return the rendered line."""
    processors = build_processors(fmt)
    for processor in processors[:-1]:
        event_dict = processor(None, method, event_dict)
    return processors[-1](None, method, event_dict)


# --- redact_secrets -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["password", "token", "access_token", "refresh_token", "authorization", "jwt_secret", "secret"],
)
def test_redacts_documented_sensitive_keys(key: str) -> None:
    assert _redact({"event": "x", key: "s3cret"}) == {"event": "x", key: REDACTED}


@pytest.mark.parametrize("key", ["Password", "AUTHORIZATION", "JWT_Secret", "Access-Token"])
def test_redaction_is_case_and_separator_insensitive(key: str) -> None:
    assert _redact({key: "s3cret"})[key] == REDACTED


def test_redacts_nested_dicts_at_any_depth() -> None:
    event = {"event": "x", "ctx": {"headers": {"authorization": "Bearer abc"}, "route": "/v1"}}

    assert _redact(event) == {
        "event": "x",
        "ctx": {"headers": {"authorization": REDACTED}, "route": "/v1"},
    }


def test_redacts_non_string_values() -> None:
    # A secret is a secret whatever its type; the censor must not depend on str-ness.
    assert _redact({"token": 12345})["token"] == REDACTED
    assert _redact({"secret": ["a", "b"]})["secret"] == REDACTED


def test_leaves_none_valued_sensitive_keys_alone() -> None:
    # There is nothing to leak, and keeping None distinguishes "absent" from "censored".
    assert _redact({"password": None}) == {"password": None}


def test_passes_through_when_no_sensitive_key_is_present() -> None:
    event = {"event": "http.request", "route": "/v1/articles", "status": 200}
    assert _redact(dict(event)) == event


def test_does_not_redact_innocuous_lookalike_keys() -> None:
    # Word-boundary matching, not substring: "tokenizer" is not "token".
    event = {"tokenizer": "bpe", "cache_key": "articles:1", "secretary": "ada"}
    assert _redact(dict(event)) == event


def test_non_dict_values_pass_through_unharmed() -> None:
    event = {"event": "x", "items": ["a", 1, None], "count": 3}
    assert _redact(dict(event)) == event


# --- renderer contract ----------------------------------------------------------------------


def test_json_renderer_emits_the_documented_keys() -> None:
    payload = json.loads(_render({"event": "http.request", "request_id": "abc123"}))

    assert payload["message"] == "http.request"  # structlog's `event`, renamed
    assert payload["level"] == "info"
    assert payload["request_id"] == "abc123"  # bound context survives
    assert set(payload) >= {"timestamp", "level", "message", "request_id"}


def test_json_renderer_timestamp_is_iso8601_utc() -> None:
    timestamp = json.loads(_render({"event": "x"}))["timestamp"]

    assert timestamp.endswith("Z")
    assert "T" in timestamp


def test_json_renderer_output_is_parseable_json() -> None:
    # A quoted, newline-bearing message must not break the line format.
    payload = json.loads(_render({"event": 'we "broke" it\nagain'}))

    assert payload["message"] == 'we "broke" it\nagain'


def test_renderer_redacts_before_writing() -> None:
    payload = json.loads(_render({"event": "auth.failed", "token": "leaky"}))
    assert payload["token"] == REDACTED


def test_console_format_selects_a_different_renderer() -> None:
    line = _render({"event": "hello"}, fmt="console")

    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert "hello" in line


# --- configure_logging ----------------------------------------------------------------------


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    """A buffer to render into, so assertions read the real rendered output, not a stub."""
    buffer = io.StringIO()
    yield buffer
    structlog.reset_defaults()
    configure_logging()  # leave the process configured as the app expects


def lines(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line.strip()]


def test_configure_logging_honours_log_level(stream: io.StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    get_logger().debug("dropped")
    assert lines(stream) == []

    configure_logging(level="DEBUG", stream=stream)
    get_logger().debug("kept")
    assert json.loads(lines(stream)[0])["message"] == "kept"


def test_configure_logging_accepts_lowercase_levels(stream: io.StringIO) -> None:
    configure_logging(level="debug", stream=stream)
    get_logger().debug("kept")
    assert json.loads(lines(stream)[0])["message"] == "kept"


def test_unparseable_level_falls_back_to_info(stream: io.StringIO) -> None:
    # A typo in LOG_LEVEL must not stop the app from booting, nor silence it.
    configure_logging(level="not-a-level", stream=stream)
    get_logger().debug("dropped")
    get_logger().info("kept")
    assert [json.loads(line)["message"] for line in lines(stream)] == ["kept"]


def test_configure_logging_is_idempotent(stream: io.StringIO) -> None:
    configure_logging(level="INFO", fmt="json", stream=stream)
    configure_logging(level="INFO", fmt="json", stream=stream)
    get_logger().info("once")
    assert len(lines(stream)) == 1


def test_configure_logging_console_format(stream: io.StringIO) -> None:
    configure_logging(level="INFO", fmt="console", stream=stream)
    get_logger().info("human readable")
    assert "human readable" in stream.getvalue()


def test_bound_context_reaches_the_rendered_line(stream: io.StringIO) -> None:
    configure_logging(level="INFO", stream=stream)
    get_logger().bind(request_id="rid-1").info("http.request", status=200)
    payload = json.loads(lines(stream)[0])
    assert payload["request_id"] == "rid-1"
    assert payload["status"] == 200
