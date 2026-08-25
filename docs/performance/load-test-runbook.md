# Load Test Runbook

> **Status:** 🟨 In progress — sections 5 and 7 are filled by the M6 measurement run · **Owner:** Simon Sibomana · **Last updated:** 2026-08-25

How to repeat the M6 load test on a cold machine, without having been in the room.

This document holds the commands. The [load test plan](load-test-plan.md) holds the intent and
the workload shape. The [load test report](load-test-report.md) holds the results and their
caveats. Nothing here carries a number a result could contradict. When the three disagree, this
document is the wrong one, because it is the one you execute.

`tests/unit/test_load_runbook.py` parses the command blocks below and fails the build if a script
path, a compose profile, a k6 argument, or an environment variable stops resolving.

## Before you start

You need a Linux host with Docker, about 30 minutes of wall-clock time for a full matrix, and
2 GB of free disk for the containers and results.

Check all five prerequisites in under a minute:

```bash
docker --version
powerprofilesctl get
cat /sys/class/power_supply/AC/online
df -h --output=avail .
ss -ltn '( sport = :3307 or sport = :6379 or sport = :8080 or sport = :9090 or sport = :3000 )'
```

What each answer must say:

| Check | Required answer | If it is wrong |
|---|---|---|
| `docker --version` | any version that supports `docker compose` | Install Docker Engine with the Compose plugin. |
| `powerprofilesctl get` | `balanced`, `performance`, or `power-saver` | `power-profiles-daemon` is not running. Install it, or the run cannot hold a power policy. |
| `/sys/class/power_supply/AC/online` | `1` | Plug the laptop in. On battery the numbers mean nothing, and `perf_env.sh lock` refuses to run. |
| `df -h` | 2 GB or more available | Free space. Prometheus and the container images need it. |
| `ss -ltn` | no output | Another process holds a port the stack needs. Stop it. |

Then create the Python environment and confirm the load generator starts:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
docker compose --profile load run --rm k6 run /scripts/selftest.js
```

The selftest takes about one second and needs no other service. Eleven checks must pass. A
failure there means the hot-set picker is broken, and every latency number a real run produced
afterwards would be fiction.

## Replicating the full matrix

One script runs everything: four runs, each with a machine-state snapshot on both sides.

```bash
scripts/run_load_matrix.sh 2026-08-22-baseline
```

The argument is the matrix id. Every file the run writes is named after it, so two matrices never
overwrite each other. Omit it and the script uses a timestamp.

What the script does, in order:

1. Locks the power profile to `performance` and installs a trap that restores `balanced` on exit.
2. Starts MySQL, Redis, Prometheus, and Grafana.
3. Runs the k6 selftest.
4. Runs A, B, C, and B2 in that order.
5. Stops the app replicas and the load balancer, and restores `balanced`.

Each of the four runs then repeats the same seven steps: a machine-state snapshot, a stack start
at the run's replica count and cache setting, a seed to 10,000 articles, a 30-second warm-up whose
results are discarded, `ramp.js`, `steady.js`, `spike.js`, a second snapshot, a citability
verdict, and a 60-second settle.

| Run | Cache | Replicas | What it answers |
|---|---|---|---|
| A | off | 1 | The uncached ceiling, and whether the capacity model's "~500 qps uncached" figure holds. |
| B | on | 1 | Per-replica capacity and the cache hit ratio. |
| C | on | 3 | Whether throughput scales with replicas, or a shared-state ceiling appears. |
| B2 | on | 1 | A repeat of B. The spread between B and B2 is the noise floor. |

B2 is not redundant. Every comparison in the report is a difference between two runs on a shared
laptop, and a difference smaller than the B-to-B2 spread is drift, not a result.

To run part of the matrix, name the runs:

```bash
MATRIX_RUNS="B C" scripts/run_load_matrix.sh 2026-08-22-scaleout
```

Wall-clock cost of a full matrix: 🟨 _recorded during the M6 measurement run (Task 2)._

## Running one scenario by hand

Use this after a code change, to compare one scenario before and after.

Start the stack yourself, then run the scenario:

```bash
docker compose up -d
docker compose --profile observability up -d
CACHE_ENABLED=true docker compose --profile scale up -d --build --scale app=1
.venv/bin/python scripts/seed_load_dataset.py --articles 10000 --emit-range docs/performance/results/manual-dataset.json
export ARTICLE_ID_MIN=$(.venv/bin/python -c "import json; print(json.load(open('docs/performance/results/manual-dataset.json'))['id_min'])")
scripts/perf_env.sh lock
scripts/perf_env.sh report docs/performance/results/manual-before.json
docker compose --profile load run --rm -e RUN_ID=manual k6 run /scripts/scenarios/steady.js
scripts/perf_env.sh report docs/performance/results/manual-after.json
```

Do not skip the `ARTICLE_ID_MIN` export. Without it the generator draws ids from 1, the reads
return 404, and a 404 is a cheap miss that never populates the cache. The hit ratio then measures
the wrong thing and still looks plausible.

Override any scenario parameter from the command line:

```bash
docker compose --profile load run --rm -e RUN_ID=manual-fast k6 run \
  --env RATE=300 --env DURATION=2m /scripts/scenarios/steady.js
```

The three scenarios and what each is for:

| Script | Executor | Purpose |
|---|---|---|
| `/scripts/scenarios/steady.js` | `constant-arrival-rate` | The pass/fail run. Thresholds come from `tests/load/lib/slo.json` and set the exit code. |
| `/scripts/scenarios/ramp.js` | `ramping-arrival-rate` | Steps the rate up to find the knee. Carries no latency thresholds on purpose. |
| `/scripts/scenarios/spike.js` | `ramping-arrival-rate` | Jumps from a low rate to a high one and back, to measure degradation and recovery. |

To target a uvicorn running on the host instead of the in-compose stack, point the generator at
the host gateway:

```bash
docker compose --profile load run --rm -e RUN_ID=hostapp k6 run \
  --env BASE_URL=http://host.docker.internal:8000 /scripts/scenarios/steady.js
```

To send client-side latency to Prometheus so it shares a time axis with the server-side metrics,
add the remote-write output:

```bash
docker compose --profile load run --rm -e RUN_ID=manual k6 run \
  -o experimental-prometheus-rw /scripts/scenarios/steady.js
```

## Collecting the results

Everything lands in `docs/performance/results/`, named for the run id:

| File | Written by | Contents |
|---|---|---|
| `<run-id>-before.json` | `perf_env.sh report` | Machine state before the run. |
| `<run-id>-after.json` | `perf_env.sh report` | Machine state after the run. |
| `<run-id>-dataset.json` | `seed_load_dataset.py --emit-range` | The seeded id block: `id_min`, `id_max`, and the row count. |
| `steady-<run-id>.json` | k6 `handleSummary` | The full k6 summary, including per-operation percentiles. |
| `ramp-<run-id>.json` | k6 `handleSummary` | The same, per ramp stage. |
| `spike-<run-id>.json` | k6 `handleSummary` | The same, across the spike and the recovery. |

Commit the summary files and both metadata snapshots for every run the report cites. Discard the
warm-up summaries, which carry `-warmup` in the run id and measure a cold cache on purpose.

Take the Grafana screenshots before you stop the stack, because Prometheus keeps its data in a
named volume but the run window is easier to find while it is still recent. Open Grafana at
`http://localhost:3000`, set the time range to the run window, and capture the API Overview,
Cache, and Database dashboards into `docs/performance/images/`.

## Verifying the run is citable

Run every check. A run that fails any one of them is not reported.

The machine-state checks are automated. `scripts/run_load_matrix.sh` prints the verdict after
each run, and you can repeat it for any pair of snapshots:

```bash
.venv/bin/python -c "from scripts.run_metadata import is_citable, load; print(is_citable(load('docs/performance/results/RUN-before.json'), load('docs/performance/results/RUN-after.json')))"
```

The remaining checks are manual:

```bash
curl -s localhost:9090/api/v1/targets | grep -c '"health":"up"'
curl -s 'localhost:9090/api/v1/query?query=sum(rate(cache_hits_total[5m]))/sum(rate(cache_hits_total[5m])%2Brate(cache_misses_total[5m]))'
curl -s 'localhost:9090/api/v1/query?query=sum(rate(http_requests_total[5m]))'
```

| # | Check | Pass band | A failure means |
|---|---|---|---|
| 1 | Prometheus targets up | 🟨 _pending Task 2_ | A replica is unscraped, so the server-side metrics cover only part of the fleet. |
| 2 | Cache hit ratio during run B | 🟨 _pending Task 2_ | The hot-set picker is wrong, not the cache. |
| 3 | k6 `dropped_iterations` | zero | The generator could not hold the offered rate. The run measured the laptop. |
| 4 | k6 `http_reqs` against the Prometheus request rate | 🟨 _pending Task 2_ | Requests die at nginx and never reach the app. |
| 5 | Package throttle delta | 🟨 _pending Task 2_ | The chassis moved during the run. |
| 6 | `powerprofilesctl get` after the last run | `performance` | `power-profiles-daemon` reverted mid-matrix, silently. |

## Teardown and restoring the machine

```bash
docker compose stop app nginx prometheus grafana
scripts/perf_env.sh unlock
powerprofilesctl get
```

The last command must print `balanced`. A laptop left pinned to `performance` runs hot and drains
its battery, so the restore is part of the procedure, not an afterthought. `run_load_matrix.sh`
installs an EXIT trap that does the same thing when a matrix is interrupted.

Leave MySQL and Redis running. **Never bring the stack down with `docker compose down`**: it
removes the MySQL container, which declares no named volume, so the seeded dataset goes with it
and the recovery is a full re-seed rather than a restart.

## When it goes wrong

Symptom, cause, fix — the shape the [alerting runbooks](../observability/alerting-runbooks.md)
already use.

### The generator reports dropped iterations

**Symptom.** The k6 summary prints `WARNING dropped_iterations=N`, and `dropped_iterations` is
above zero in the JSON.

**Cause.** The generator could not start iterations fast enough to hold the offered rate. Either
`maxVUs` is too low for the latency the server is showing, or the eight E-cores are not enough.

**Fix.** Raise `MAX_VUS` first. If the drops persist, widen `K6_CPUSET` and record the change in
the report, because the generator then competes with the system under test. The run that dropped
iterations is void either way.

### Prometheus shows no replica targets

**Symptom.** `curl -s localhost:9090/api/v1/targets` lists only `content-platform` and
`prometheus`, and every server-side panel is empty during the run.

**Cause.** The `scale` profile is not up, so the `app` DNS name resolves to nothing.

**Fix.** Start the scale profile and wait for nginx to answer, then reload Prometheus:

```bash
docker compose --profile scale up -d --build --scale app=3
curl -fsS http://localhost:8080/health/live
curl -X POST http://localhost:9090/-/reload
```

### The cache hit ratio is far from the expected band

**Symptom.** `cache_hits_total` over hits plus misses sits well outside the band the report
records for run B.

**Cause.** The hot-set picker, not the cache. A ratio far below the band means the reads spread
across too many ids for any TTL to help. A ratio far above it means the hot set is so small that
the run is not a workload.

**Fix.** Check `hot_set_share` and `hot_set_traffic` in `tests/load/lib/slo.json`, then run the
selftest, which asserts the picker against those numbers.

### The power profile drifted mid-matrix

**Symptom.** `powerprofilesctl get` prints `balanced` while the matrix is still running, or the
citability verdict names the power profile.

**Cause.** `power-profiles-daemon` reverted the setting. It owns both the governor and the energy
performance preference, and it reverts a raw `sysfs` write without saying anything.

**Fix.** Re-lock and re-run the affected run. Never set the governor through `cpupower`; the
daemon undoes it, and the run still produces numbers that look fine.

```bash
scripts/perf_env.sh lock
```

### The dataset disappeared

**Symptom.** The list endpoint returns an empty page, or the seeder reports it created 10,000
articles on a second run.

**Cause.** The MySQL container was removed. It declares no named volume.

**Fix.** Re-create the schema and re-seed. There is nothing to recover.

```bash
docker compose up -d
.venv/bin/alembic upgrade head
.venv/bin/python scripts/seed_load_dataset.py --articles 10000
```

🟨 _Task 2 adds the failures actually met during the measurement run._

## Parameter reference

Every knob the commands above accept. Defaults are what the scripts use when you set nothing.

| Variable | Default | Effect | Used by |
|---|---|---|---|
| `MATRIX_RUNS` | `A B C B2` | Which runs of the matrix to execute. | `run_load_matrix.sh` |
| `SEED_ARTICLES` | `10000` | Rows the seeder tops the dataset up to. | `run_load_matrix.sh` |
| `SETTLE_BETWEEN_RUNS` | `60` | Seconds of idle between runs, so each starts from the same thermal state. | `run_load_matrix.sh` |
| `WARM_RATE` | `50` | Arrival rate of the discarded warm-up. | `run_load_matrix.sh` |
| `WARM_DURATION` | `30s` | Length of the discarded warm-up. | `run_load_matrix.sh` |
| `RESULTS_DIR` | `docs/performance/results` | Where summaries and snapshots land. Inside the container it is `/results`. | `run_load_matrix.sh`, `summary.js` |
| `PYTHON` | `.venv/bin/python` | Interpreter the matrix uses for the seeder and the verdict. | `run_load_matrix.sh` |
| `CACHE_ENABLED` | `true` | Cache-aside on or off. Run A sets it to `false`. | `docker-compose.yml` |
| `K6_CPUSET` | `12-19` | CPUs the generator runs on. The eight E-cores, so all six P-cores stay with the service. | `docker-compose.yml`, `perf_env.sh` |
| `K6_UID` | `1000` | User the k6 container runs as, so it can write the results bind mount. | `docker-compose.yml` |
| `K6_GID` | `1000` | Group for the same reason. | `docker-compose.yml` |
| `BASE_URL` | `http://nginx:80` | What the generator targets. Use `http://host.docker.internal:8000` for a host uvicorn. | `api.js` |
| `RUN_ID` | `adhoc` | Names every file the run writes. | `summary.js` |
| `ARTICLE_ID_MIN` | `1` | Where the seeded id block begins. `AUTO_INCREMENT` does not restart at 1 after a delete, so a generator that assumes 1 to N reads ids that do not exist. The matrix reads it from the seeder. | `docker-compose.yml`, `run_load_matrix.sh` |
| `RATE` | from `slo.json` | Arrival rate of the steady run, in requests per second. | `steady.js` |
| `DURATION` | from `slo.json` | Length of the steady run. | `steady.js` |
| `STEP_DURATION` | from `slo.json` | How long the ramp holds each step. | `ramp.js` |
| `PRE_ALLOCATED_VUS` | from `slo.json` | Virtual users started before the run, so the first seconds are not a ramp. | all scenarios |
| `MAX_VUS` | from `slo.json` | Ceiling on virtual users. Raise it when the generator drops iterations. | all scenarios |
| `SETTLE_SECONDS` | `3` | Pause after the power profile changes, before the state is read back. | `perf_env.sh` |

## Change control

Four kinds of change, and what each one obliges you to update.

**A new or renamed scenario.** Add the script under `tests/load/scenarios/`, add its configuration
block to `tests/load/lib/slo.json`, name it in the [load test plan](load-test-plan.md), and add a
row to the scenario table above. `tests/unit/test_load_profile.py` fails until all four exist.

**A changed target.** Edit `docs/requirements/non-functional-requirements.md` first, then
`tests/load/lib/slo.json` to match. `tests/unit/test_load_profile.py` compares the two and fails
on any drift, in that direction: the document is the authority.

**A new environment variable.** Add it to the parameter reference above in the same commit that
adds it to a script. `tests/unit/test_load_runbook.py` fails on a variable used in a command block
but not documented, and on a documented variable nothing reads.

**Different hardware.** `K6_CPUSET` names this machine's eight E-cores. On another topology, work
out the core split first, set `K6_CPUSET` accordingly, and record it in the report. The generator
must never share cores with the service under test.
