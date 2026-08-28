#!/usr/bin/env python
"""Draw the load-test graphs the report carries, straight from Prometheus.

Grafana renders its panels in a browser, and this environment drives no browser, so a screenshot
is not available. The charts instead come from the same range queries the analysis reads, which
makes them reproducible: anyone with the run window can regenerate a byte-identical picture.

The output is a self-contained SVG with no external stylesheet and no script. It carries both
themes, because the documentation site renders in whichever one the reader chose.

```bash
python scripts/plot_load_results.py --run 2026-08-22-nfr-C --scenario ramp \
    --out docs/performance/images/ramp-three-replicas.svg
```
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WIDTH = 760
HEIGHT = 300
PAD_LEFT = 64
PAD_RIGHT = 16
PAD_TOP = 44
PAD_BOTTOM = 52
# Colorblind-safe, and distinguishable in grayscale if the page is printed.
PALETTE = ("#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e")


class ChartError(ValueError):
    """A chart cannot be drawn from what the query returned."""


@dataclass
class Series:
    """One line: a name for the legend, and the points in query order."""

    name: str
    points: list[tuple[float, float]]
    color: str = field(default=PALETTE[0])


def escape(text: str) -> str:
    """Make text safe for an XML document. Every label goes through this."""

    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def project(
    points: list[tuple[float, float]],
    *,
    x_range: tuple[float, float],
    y_max: float,
    box: tuple[int, int],
) -> list[tuple[float, float]]:
    """Map (x, y) values onto the drawing box, y growing upward.

    The y axis always starts at zero. A chart scaled to its own minimum turns a 2% wobble into a
    cliff, which is the one way a correct number still misleads.
    """

    width, height = box
    x_lo, x_hi = x_range
    x_span = (x_hi - x_lo) or 1.0
    y_span = y_max or 1.0
    return [(width * (x - x_lo) / x_span, height * (1.0 - y / y_span)) for x, y in points]


def _ticks(y_max: float, count: int = 4) -> list[float]:
    return [y_max * step / count for step in range(count + 1)]


def _format(value: float) -> str:
    if value >= 100:
        return f"{value:.0f}"
    if value >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def render_chart(
    series: list[Series],
    *,
    title: str,
    y_label: str,
    x_label: str = "seconds into the run",
    width: int = WIDTH,
    height: int = HEIGHT,
) -> str:
    """One SVG holding every series, with a zero-based y axis and a legend."""

    if not series:
        raise ChartError("a chart needs at least one series")
    for line in series:
        if not line.points:
            raise ChartError(f"series {line.name!r} carries no points: the query returned nothing")

    box = (width - PAD_LEFT - PAD_RIGHT, height - PAD_TOP - PAD_BOTTOM)
    xs = [x for line in series for x, _ in line.points]
    y_max = max(y for line in series for _, y in line.points)
    x_range = (min(xs), max(xs))

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img" aria-label="{escape(title)}">',
        "<style>"
        ".bg{fill:#ffffff}.fg{fill:#1a1a1a}.grid{stroke:#d8d8d8}"
        ".tick{fill:#5a5a5a;font-size:11px}.ttl{font-size:14px;font-weight:600}"
        ".lbl{font-size:11px}"
        "text{font-family:-apple-system,Segoe UI,Roboto,sans-serif}"
        "@media (prefers-color-scheme: dark){"
        ".bg{fill:#161616}.fg{fill:#ededed}.grid{stroke:#3a3a3a}.tick{fill:#a8a8a8}}"
        "</style>",
        f'<rect class="bg" width="{width}" height="{height}"/>',
        f'<text class="fg ttl" x="{PAD_LEFT}" y="24">{escape(title)}</text>',
    ]

    for tick in _ticks(y_max):
        y = PAD_TOP + box[1] * (1.0 - (tick / (y_max or 1.0)))
        parts.append(
            f'<line class="grid" x1="{PAD_LEFT}" y1="{y:.1f}" '
            f'x2="{PAD_LEFT + box[0]}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{PAD_LEFT - 8}" y="{y + 4:.1f}" '
            f'text-anchor="end">{_format(tick)}</text>'
        )

    for index, line in enumerate(series):
        coords = project(line.points, x_range=x_range, y_max=y_max, box=box)
        drawn = " ".join(f"{PAD_LEFT + x:.1f},{PAD_TOP + y:.1f}" for x, y in coords)
        parts.append(
            f'<polyline fill="none" stroke="{line.color}" stroke-width="2" '
            f'stroke-linejoin="round" points="{drawn}"/>'
        )
        legend_x = PAD_LEFT + index * 170
        legend_y = height - 14
        parts.append(
            f'<rect x="{legend_x}" y="{legend_y - 9}" width="10" height="10" fill="{line.color}"/>'
        )
        parts.append(
            f'<text class="fg lbl" x="{legend_x + 16}" y="{legend_y}">{escape(line.name)}</text>'
        )

    parts.append(f'<text class="fg lbl" x="{PAD_LEFT}" y="{height - 32}">{escape(x_label)}</text>')
    parts.append(
        f'<text class="fg lbl" x="14" y="{PAD_TOP + box[1] / 2:.0f}" '
        f'transform="rotate(-90 14 {PAD_TOP + box[1] / 2:.0f})" '
        f'text-anchor="middle">{escape(y_label)}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


# --- reading the numbers back out of Prometheus -------------------------------------------------


def range_query(
    expression: str, *, start: float, end: float, step: int, base_url: str
) -> list[tuple[float, float]]:
    """One Prometheus range query, returned as points relative to the window start."""

    url = f"{base_url}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expression, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": str(step)}
    )
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - a local URL
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ChartError(f"prometheus rejected the query: {expression}")
    results = payload["data"]["result"]
    if not results:
        raise ChartError(f"prometheus returned no series for: {expression}")
    return [(float(at) - start, float(value)) for at, value in results[0]["values"]]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=float, required=True, help="window start, unix seconds")
    parser.add_argument("--end", type=float, required=True, help="window end, unix seconds")
    parser.add_argument("--step", type=int, default=15, help="sample step in seconds")
    parser.add_argument("--query", action="append", required=True, metavar="NAME=PROMQL")
    parser.add_argument("--title", required=True)
    parser.add_argument("--y-label", required=True)
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every value")
    parser.add_argument("--prometheus", default="http://localhost:9090")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    series = []
    for index, pair in enumerate(args.query):
        name, _, expression = pair.partition("=")
        points = range_query(
            expression, start=args.start, end=args.end, step=args.step, base_url=args.prometheus
        )
        series.append(
            Series(
                name=name,
                points=[(x, y * args.scale) for x, y in points],
                color=PALETTE[index % len(PALETTE)],
            )
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_chart(series, title=args.title, y_label=args.y_label))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
