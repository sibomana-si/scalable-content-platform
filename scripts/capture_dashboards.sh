#!/usr/bin/env bash
# Capture the provisioned Grafana dashboards as PNG files for the report.
#
# Usage:
#   scripts/capture_dashboards.sh FROM TO [UID ...]
#
# FROM and TO are ISO-8601 timestamps that `date -d` accepts, for example
# 2026-08-29T19:56:33+02:00. Each UID names a provisioned dashboard; the default is all four.
# The PNG lands in docs/observability/images/<uid without scp->.png.
#
# The script drives headless Chrome against the kiosk URL of each dashboard. Grafana runs with
# anonymous Admin access in the local compose stack, so no login is needed. Start the stack with
# `docker compose --profile observability up -d` first; the script stops if Grafana does not answer.
set -euo pipefail

GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
CHROME="${CHROME:-google-chrome}"
OUT_DIR="$(cd "$(dirname "$0")/.." && pwd)/docs/observability/images"
DEFAULT_UIDS=(scp-api-overview scp-database scp-cache scp-resilience)

if [ "$#" -lt 2 ]; then
  echo "usage: $0 FROM TO [UID ...]" >&2
  exit 2
fi

from_iso="$1"
to_iso="$2"
shift 2
uids=("$@")
if [ "${#uids[@]}" -eq 0 ]; then
  uids=("${DEFAULT_UIDS[@]}")
fi

if ! curl -sf "$GRAFANA_URL/api/health" >/dev/null; then
  echo "Grafana does not answer at $GRAFANA_URL. Start it with:" >&2
  echo "  docker compose --profile observability up -d" >&2
  exit 1
fi

from_ms=$(( $(date -d "$from_iso" +%s) * 1000 ))
to_ms=$(( $(date -d "$to_iso" +%s) * 1000 ))
mkdir -p "$OUT_DIR"

echo "window: $from_iso -> $to_iso ($from_ms .. $to_ms)"
for uid in "${uids[@]}"; do
  name="${uid#scp-}"
  out="$OUT_DIR/$name.png"
  url="$GRAFANA_URL/d/$uid?orgId=1&kiosk&from=$from_ms&to=$to_ms"
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-sandbox \
    --window-size=1920,1500 --virtual-time-budget=20000 \
    --screenshot="$out" "$url" >/dev/null 2>&1
  if command -v convert >/dev/null; then
    # Grafana leaves empty canvas under the last row. Trim it and keep a 16 px margin.
    background="$(convert "$out" -format '%[pixel:p{0,0}]' info:)"
    convert "$out" -trim +repage -bordercolor "$background" -border 16 "$out"
  fi
  echo "captured $uid -> ${out#"$(pwd)"/}"
done
