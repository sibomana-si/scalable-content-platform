"""Structured JSON logging (structlog).

One line per event on stdout, shipped by the platform — the app never writes log files.
Field names are fixed: ``timestamp``, ``level``, ``message``, plus whatever context is
bound (``request_id``, ``user_id``, ``route``, ``latency_ms``, ``status``).

Every decision lives in a processor (a pure ``(logger, method_name, event_dict) -> ...``
callable), so the behaviour is testable without emitting anything.
"""

import logging
import re
from sys import stdout
from typing import Any, TextIO

import structlog

from app.config import get_settings

# Placeholder substituted for censored values. Distinct enough to grep for in an incident.
REDACTED = "***REDACTED***"

# Word-boundary matching, not substring: `token` must censor `access_token` but not `tokenizer`.
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "credential",
        "credentials",
        "cookie",
    }
)
_SENSITIVE_KEYS = frozenset({"api_key", "apikey", "x-api-key", "set-cookie"})
_WORD_SPLIT = re.compile(r"[._\-\s]+")


def _is_sensitive(key: str) -> bool:
    normalized = key.lower()
    if normalized in _SENSITIVE_KEYS:
        return True
    return any(word in _SENSITIVE_WORDS for word in _WORD_SPLIT.split(normalized))


def redact_secrets(_logger: Any, _method_name: str, event_dict: Any) -> Any:
    """Censor secret-bearing keys anywhere in the event, at any nesting depth.

    A defence in depth, not a licence to log secrets: it catches the accidental
    ``logger.info("...", **payload)`` where ``payload`` happens to carry a password. ``None``
    values are left alone — there is nothing to leak, and keeping them distinguishes
    "absent" from "censored".
    """
    if not isinstance(event_dict, dict):
        return event_dict
    return {
        key: REDACTED
        if _is_sensitive(str(key)) and value is not None
        else (redact_secrets(_logger, _method_name, value) if isinstance(value, dict) else value)
        for key, value in event_dict.items()
    }


def build_processors(fmt: str) -> list[Any]:
    """The processor chain, innermost first. ``fmt`` selects the final renderer."""
    renderer: Any = (
        structlog.dev.ConsoleRenderer()
        if fmt.lower() == "console"
        else structlog.processors.JSONRenderer()
    )
    return [
        structlog.contextvars.merge_contextvars,  # request-scoped bindings
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_secrets,
        structlog.processors.EventRenamer("message"),
        renderer,
    ]


def _level_number(level: str) -> int:
    resolved = logging.getLevelNamesMapping().get(level.strip().upper())
    # An unparseable LOG_LEVEL must not stop the app from booting; be verbose, not silent.
    return resolved if isinstance(resolved, int) else logging.INFO


def configure_logging(
    level: str | None = None, fmt: str | None = None, stream: TextIO | None = None
) -> None:
    """Install the structlog configuration. Idempotent; safe to call more than once.

    Called at import of :mod:`app.main` rather than from a lifespan hook, so logging is
    already configured for anything that happens during app construction (and so the test
    harness, which does not run lifespan events, gets the same configuration).

    ``stream`` defaults to stdout — the platform ships it; the app never writes log files.
    Tests pass a buffer to read back what was actually rendered.
    """
    settings = get_settings()
    structlog.configure(
        processors=build_processors(fmt if fmt is not None else settings.log_format),
        wrapper_class=structlog.make_filtering_bound_logger(
            _level_number(level if level is not None else settings.log_level)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=stream if stream is not None else stdout),
        # Resolve configuration per call: reconfiguring at runtime (and in tests) must take
        # effect on loggers that were bound earlier.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> Any:
    """A lazily-configured structlog logger."""
    return structlog.get_logger(name)
