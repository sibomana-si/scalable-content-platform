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
#   C   cache on,  3 replicas  does throughput scale, or is there a shared-state ceiling. This run
#                              overrides the published scenario shape: a higher steady rate and a
#                              longer ramp, because three replicas do not bend where one does.
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
# Empty means "whatever slo.json says". The scale-out run needs more than that: three replicas do
# not bend where one does, so a ramp that tops out at the single-replica knee finds nothing, and a
# steady rate below 500 rps cannot test the sustained-throughput target at all.
SCALED_STEADY_RATE="${SCALED_STEADY_RATE:-500}"
SCALED_RAMP_START_RPS="${SCALED_RAMP_START_RPS:-100}"
SCALED_RAMP_STEP_RPS="${SCALED_RAMP_STEP_RPS:-100}"
SCALED_RAMP_STEPS="${SCALED_RAMP_STEPS:-10}"
PYTHON="${PYTHON:-.venv/bin/python}"
export K6_CPUSET="${K6_CPUSET:-12-19}"
export BASE_URL="${BASE_URL:-http://nginx:80}"
# The seeder overwrites this after each seed. AUTO_INCREMENT does not restart at 1, so the
# generator must be told where the seeded block begins or it draws ids that do not exist.
export ARTICLE_ID_MIN="${ARTICLE_ID_MIN:-1}"

say() { printf '\n=== %s\n' "$*"; }

# k6 exits 99 when a threshold is crossed. A crossed threshold is a measurement, not a broken
# run — the matrix exists to find where the targets stop holding — so the run is recorded and the
# matrix goes on. Every other non-zero status is a real failure and stops the matrix.
K6_THRESHOLD_EXIT=99
THRESHOLD_CROSSED=()

k6_run() {
  # $1 script, $2 run id, rest: extra environment as NAME=VALUE
  local script="$1" run_id="$2"
  shift 2
  local env_args=()
  local pair
  for pair in "$@"; do
    env_args+=(--env "$pair")
  done
  local status=0
  RUN_ID="$run_id" docker compose --profile load run --rm \
    -e "RUN_ID=$run_id" k6 run "${env_args[@]}" "/scripts/$script" || status=$?
  if [ "$status" -eq "$K6_THRESHOLD_EXIT" ]; then
    THRESHOLD_CROSSED+=("${run_id} ${script}")
    echo "threshold crossed in ${run_id} (${script}) · recorded, the matrix continues"
    return 0
  fi
  return "$status"
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


judge_repeat() {
  # The drift rule needs a run and its repeat. Only the B and B2 pair holds the workload fixed.
  local first="$1" second="$2"
  "$PYTHON" - "$RESULTS_DIR/${first}" "$RESULTS_DIR/${second}" <<'PY'
import sys
from scripts.run_metadata import load, repeat_is_consistent, throttle_delta

stem_a, stem_b = sys.argv[1], sys.argv[2]
run = (load(f"{stem_a}-before.json"), load(f"{stem_a}-after.json"))
repeat = (load(f"{stem_b}-before.json"), load(f"{stem_b}-after.json"))
first, second = throttle_delta(*run)["package"], throttle_delta(*repeat)["package"]
consistent, reasons = repeat_is_consistent(run, repeat)
print(f"repeat check: package deltas {first} against {second}")
if consistent:
    print("the run and its repeat met the same machine")
else:
    for reason in reasons:
        print(f"REPEAT DRIFTED: {reason}")
PY
}

one_run() {
  # $1 label, $2 replicas, $3 cache enabled, rest: extra environment for the ramp and the steady
  # run as NAME=VALUE. A run that overrides the published shape records the override here.
  local label="$1" replicas="$2" cache="$3"
  shift 3
  local ramp_env=() steady_env=() pair
  for pair in "$@"; do
    case "$pair" in
      RATE=*) steady_env+=("$pair") ;;
      *) ramp_env+=("$pair") ;;
    esac
  done
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

  say "run ${label}: ramp ${ramp_env[*]:-(published shape)}"
  k6_run scenarios/ramp.js "$run_id" ${ramp_env[@]+"${ramp_env[@]}"}
  say "run ${label}: steady ${steady_env[*]:-(published rate)}"
  k6_run scenarios/steady.js "$run_id" ${steady_env[@]+"${steady_env[@]}"}
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
      C)
        one_run C 3 true \
          "RATE=$SCALED_STEADY_RATE" \
          "START_RPS=$SCALED_RAMP_START_RPS" \
          "STEP_RPS=$SCALED_RAMP_STEP_RPS" \
          "STEPS=$SCALED_RAMP_STEPS"
        ;;
      B2) one_run B2 1 true ;;
      *)
        echo "unknown run label ${label}" >&2
        return 2
        ;;
    esac
  done

  if [ -f "$RESULTS_DIR/${MATRIX_ID}-B-after.json" ] &&
     [ -f "$RESULTS_DIR/${MATRIX_ID}-B2-after.json" ]; then
    say "noise floor: run B against run B2"
    judge_repeat "${MATRIX_ID}-B" "${MATRIX_ID}-B2"
  fi

  say "matrix ${MATRIX_ID} complete · results in ${RESULTS_DIR}"
  if [ "${#THRESHOLD_CROSSED[@]}" -eq 0 ]; then
    echo "no thresholds crossed"
  else
    echo "thresholds crossed in ${#THRESHOLD_CROSSED[@]} scenario runs:"
    printf '  %s\n' "${THRESHOLD_CROSSED[@]}"
  fi
  docker compose stop app nginx
}

main "$@"
