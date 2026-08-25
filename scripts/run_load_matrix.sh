#!/usr/bin/env bash
#
# Run the M6 load-test matrix end to end.
#
#   scripts/run_load_matrix.sh [MATRIX_ID]
#
# Four runs, in this order:
#
#   A   cache off, 1 replica   the uncached ceiling, and a check on the "~500 qps uncached"
#                              figure in the capacity model
#   B   cache on,  1 replica   per-replica capacity and the >= 90% hit ratio
#   C   cache on,  3 replicas  does throughput scale, or is there a shared-state ceiling
#   B2  cache on,  1 replica   a repeat of B. The spread between B and B2 is the noise floor,
#                              and no optimization counts as a win unless it beats that spread.
#
# Each run: a machine-state snapshot, seed, warm, ramp, steady, spike, a second snapshot, then a
# 60-second settle. The snapshot pair decides whether the run is citable; scripts/run_metadata.py
# holds the rules.
#
# The EXIT trap restores the `balanced` power profile. An interrupted matrix must never leave the
# laptop pinned to `performance` — that is a hot machine left behind, not a test artifact.
#
# Never `docker compose down`: it removes the mysql container, which declares no named volume, so
# the seeded dataset goes with it. Teardown stops named services.

set -euo pipefail

cd "$(dirname "$0")/.."

MATRIX_ID="${1:-$(date +%Y%m%d-%H%M%S)}"
RESULTS_DIR="${RESULTS_DIR:-docs/performance/results}"
MATRIX_RUNS="${MATRIX_RUNS:-A B C B2}"
SETTLE_BETWEEN_RUNS="${SETTLE_BETWEEN_RUNS:-60}"
WARM_DURATION="${WARM_DURATION:-30s}"
WARM_RATE="${WARM_RATE:-50}"
SEED_ARTICLES="${SEED_ARTICLES:-10000}"
PYTHON="${PYTHON:-.venv/bin/python}"
export K6_CPUSET="${K6_CPUSET:-12-19}"
export BASE_URL="${BASE_URL:-http://nginx:80}"
# The seeder overwrites this after each seed. AUTO_INCREMENT does not restart at 1, so the
# generator must be told where the seeded block begins or it draws ids that do not exist.
export ARTICLE_ID_MIN="${ARTICLE_ID_MIN:-1}"

say() { printf '\n=== %s\n' "$*"; }

k6_run() {
  # $1 script, $2 run id, rest: extra environment as NAME=VALUE
  local script="$1" run_id="$2"
  shift 2
  local env_args=()
  local pair
  for pair in "$@"; do
    env_args+=(--env "$pair")
  done
  RUN_ID="$run_id" docker compose --profile load run --rm \
    -e "RUN_ID=$run_id" k6 run "${env_args[@]}" "/scripts/$script"
}

stack_up() {
  # $1 replicas, $2 cache enabled
  local replicas="$1" cache="$2"
  CACHE_ENABLED="$cache" docker compose --profile scale up -d --build --scale "app=$replicas"
  # nginx reports healthy only once a replica answers /health/live through it.
  local attempt
  for attempt in $(seq 1 60); do
    if curl -fsS http://localhost:8080/health/live >/dev/null 2>&1; then
      echo "stack ready after ${attempt}s · replicas=${replicas} cache=${cache}"
      return 0
    fi
    sleep 1
  done
  echo "the stack did not answer on :8080 within 60s" >&2
  return 1
}

judge() {
  local run_id="$1"
  "$PYTHON" - "$RESULTS_DIR/${run_id}-before.json" "$RESULTS_DIR/${run_id}-after.json" <<'PY'
import sys
from scripts.run_metadata import is_citable, load, throttle_delta

before, after = load(sys.argv[1]), load(sys.argv[2])
delta = throttle_delta(before, after)
citable, reasons = is_citable(before, after)
print(f"throttle delta: package={delta['package']} core={delta['core']}")
if citable:
    print("run is citable")
else:
    for reason in reasons:
        print(f"NOT CITABLE: {reason}")
PY
}

one_run() {
  # $1 label, $2 replicas, $3 cache enabled
  local label="$1" replicas="$2" cache="$3"
  local run_id="${MATRIX_ID}-${label}"

  say "run ${label}: replicas=${replicas} cache=${cache} (run id ${run_id})"
  scripts/perf_env.sh report "$RESULTS_DIR/${run_id}-before.json"

  stack_up "$replicas" "$cache"
  "$PYTHON" scripts/seed_load_dataset.py \
    --articles "$SEED_ARTICLES" --emit-range "$RESULTS_DIR/${run_id}-dataset.json"
  ARTICLE_ID_MIN="$("$PYTHON" -c \
    "import json,sys; print(json.load(open(sys.argv[1]))['id_min'])" \
    "$RESULTS_DIR/${run_id}-dataset.json")"
  export ARTICLE_ID_MIN
  echo "article ids start at ${ARTICLE_ID_MIN}"

  say "run ${label}: warming (results discarded)"
  k6_run scenarios/steady.js "${run_id}-warmup" "RATE=$WARM_RATE" "DURATION=$WARM_DURATION"

  say "run ${label}: ramp"
  k6_run scenarios/ramp.js "$run_id"
  say "run ${label}: steady"
  k6_run scenarios/steady.js "$run_id"
  say "run ${label}: spike"
  k6_run scenarios/spike.js "$run_id"

  scripts/perf_env.sh report "$RESULTS_DIR/${run_id}-after.json"
  judge "$run_id"

  say "run ${label}: settling for ${SETTLE_BETWEEN_RUNS}s"
  sleep "$SETTLE_BETWEEN_RUNS"
}

main() {
  mkdir -p "$RESULTS_DIR"

  say "matrix ${MATRIX_ID}"
  scripts/perf_env.sh lock
  trap 'scripts/perf_env.sh unlock' EXIT

  docker compose up -d
  docker compose --profile observability up -d

  # The selftest is seconds long and catches a broken picker before five minutes of fiction.
  k6_run selftest.js "${MATRIX_ID}-selftest"

  local label
  for label in $MATRIX_RUNS; do
    case "$label" in
      A) one_run A 1 false ;;
      B) one_run B 1 true ;;
      C) one_run C 3 true ;;
      B2) one_run B2 1 true ;;
      *)
        echo "unknown run label ${label}" >&2
        return 2
        ;;
    esac
  done

  say "matrix ${MATRIX_ID} complete · results in ${RESULTS_DIR}"
  docker compose stop app nginx
}

main "$@"
