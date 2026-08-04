"""Observability: structured logging, Prometheus metrics, and OpenTelemetry tracing.

The three pillars are correlated by a single ``request_id`` minted (or accepted, after
sanitising) by ``RequestIDMiddleware`` and carried in log lines, error envelopes and span
attributes.

Nothing in this package opens a socket at import time: telemetry export is best-effort and
must never fail or slow request handling.
"""
