#!/usr/bin/env python3
"""Inject a network fault between the application and one of its dependencies.

Toxiproxy sits in front of MySQL and Redis under the `chaos` compose profile, and this is the
command line over its HTTP API. Four faults are enough for the four pre-registered experiments in
`docs/resilience/chaos-test-report.md`:

    scripts/inject_fault.py --target mysql latency --ms 800
    scripts/inject_fault.py --target mysql blackhole
    scripts/inject_fault.py --target redis reset_peer
    scripts/inject_fault.py --target mysql --clear

Every toxic carries the fault name, so `--clear` removes exactly what an experiment added and
leaves a fault held on the other dependency alone.

Pick the value of a latency or timeout fault from the configured timeout budget in
`docs/operations/configuration-reference.md`, never from a template. A 500 ms delay against a
2 s statement timeout trips nothing, and the run measures the proxy hop.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import cast

DEFAULT_URL = os.environ.get("TOXIPROXY_URL", "http://localhost:8474")

TARGETS = ("mysql", "redis")
FAULTS = ("latency", "timeout", "blackhole", "reset_peer")

# The faults that mean nothing without a duration. `blackhole` and `reset_peer` are absolute.
TIMED_FAULTS = ("latency", "timeout")

Transport = Callable[[str, str, "dict | None"], object]


class FaultError(ValueError):
    """The requested fault cannot be built, so nothing is sent to the proxy."""


def toxic_for(fault: str, *, ms: int | None = None, jitter_ms: int = 0) -> dict:
    """Build the toxiproxy toxic for one named fault.

    The name of the toxic is the name of the fault. That is what lets `--clear` be precise and
    what makes `--list` readable during a run.
    """

    if fault not in FAULTS:
        raise FaultError(f"unknown fault {fault!r}, expected one of: {', '.join(FAULTS)}")
    if fault in TIMED_FAULTS and (ms is None or ms < 0):
        raise FaultError(f"the {fault} fault needs a duration: pass --ms with a value of 0 or more")

    if fault == "latency":
        attributes: dict[str, int] = {"latency": int(ms or 0), "jitter": jitter_ms}
    elif fault == "timeout":
        attributes = {"timeout": int(ms or 0)}
    else:
        # A toxiproxy `timeout` of 0 never closes the connection, so the caller waits on its own
        # bound. `reset_peer` of 0 refuses at once. Together they cover a hung server and a dead
        # one, which fail in different places in the request path.
        attributes = {"timeout": 0}

    return {
        "name": fault,
        "type": "timeout" if fault == "blackhole" else fault,
        # Downstream delays the answer. An upstream toxic delays the query instead, which is a
        # different experiment and not one this milestone registers.
        "stream": "downstream",
        "toxicity": 1.0,
        "attributes": attributes,
    }


def _http(method: str, path: str, payload: dict | None = None, *, base_url: str = "") -> object:
    request = urllib.request.Request(  # noqa: S310 - a local URL from a flag
        f"{base_url}{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - a local URL
        body = response.read()
    return json.loads(body) if body else None


class Toxiproxy:
    """A small client over the toxiproxy API, with the HTTP layer injectable for tests."""

    def __init__(self, base_url: str = DEFAULT_URL, *, transport: Transport | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport: Transport = transport or cast(
            Transport,
            lambda method, path, payload=None: _http(method, path, payload, base_url=self.base_url),
        )

    def active(self, target: str) -> list[dict]:
        """The toxics currently on one proxy."""

        toxics = self._transport("GET", f"/proxies/{target}/toxics", None)
        return cast(list[dict], toxics or [])

    def apply(self, target: str, toxic: dict) -> None:
        """Put one toxic on a proxy, replacing any toxic of the same name.

        Toxiproxy answers a duplicate name with 409. An experiment that re-injects a fault at a
        new value is ordinary, so replace rather than fail.
        """

        if any(existing["name"] == toxic["name"] for existing in self.active(target)):
            self._transport("DELETE", f"/proxies/{target}/toxics/{toxic['name']}", None)
        self._transport("POST", f"/proxies/{target}/toxics", toxic)

    def clear(self, target: str) -> list[str]:
        """Remove every toxic on one proxy and return the names removed."""

        names = [toxic["name"] for toxic in self.active(target)]
        for name in names:
            self._transport("DELETE", f"/proxies/{target}/toxics/{name}", None)
        return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", required=True, choices=TARGETS, help="Which proxy to act on.")
    parser.add_argument("fault", nargs="?", choices=FAULTS, help="The fault to inject.")
    parser.add_argument("--ms", type=int, help="Duration of a latency or timeout fault.")
    parser.add_argument("--jitter-ms", type=int, default=0, help="Jitter around a latency fault.")
    parser.add_argument("--clear", action="store_true", help="Remove every toxic on the target.")
    parser.add_argument("--list", action="store_true", help="Print the toxics on the target.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Toxiproxy API base URL.")
    return parser


def main(argv: list[str] | None = None, *, client: Toxiproxy | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = client or Toxiproxy(args.url)

    try:
        if args.list:
            toxics = client.active(args.target)
            if not toxics:
                print(f"{args.target}: no toxics are injected")
            for toxic in toxics:
                attributes = toxic.get("attributes", "")
                print(f"{args.target}: {toxic['name']} ({toxic.get('type')}) {attributes}")
            return 0

        if args.clear:
            removed = client.clear(args.target)
            print(f"{args.target}: cleared {', '.join(removed) if removed else 'nothing'}")
            return 0

        if not args.fault:
            print("give a fault to inject, or pass --clear or --list", file=sys.stderr)
            return 2

        toxic = toxic_for(args.fault, ms=args.ms, jitter_ms=args.jitter_ms)
        client.apply(args.target, toxic)
        print(f"{args.target}: injected {toxic['name']} {toxic['attributes']}")
        return 0
    except FaultError as error:
        print(str(error), file=sys.stderr)
        return 2
    except urllib.error.URLError as error:
        print(f"cannot reach toxiproxy at {args.url}: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
