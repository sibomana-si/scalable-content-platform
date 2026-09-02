# Chaos Test Runbook

> **Status:** ✅ Current · **Owner:** Simon Sibomana · **Last updated:** 2026-09-02

How to inject a fault into this system and measure what it does. The design under test is
[fault-tolerance-design.md](fault-tolerance-design.md); the results live in
[chaos-test-report.md](chaos-test-report.md). This document carries the procedure and no result,
so nothing here can contradict a measurement.

Read the [load test runbook](../performance/load-test-runbook.md) first if you have not run the
load generator before. A chaos experiment is a load run with a fault injected during it, and every
rule there about the machine still applies.

## Before you start

You need a Linux host with Docker, about 90 minutes for the full experiment set, and 2 GB of free
disk. Faults are injected by [Toxiproxy](https://github.com/Shopify/toxiproxy), which sits between
the application and each dependency. Stopping a container is a blunter instrument: it cannot
express "slow", and stopping MySQL loses the data, because that service declares no named volume.

Check the prerequisites in under a minute:

```bash
docker --version
powerprofilesctl get
cat /sys/class/power_supply/AC/online
ss -ltn '( sport = :8474 or sport = :23306 or sport = :26379 )'
```

What each answer must say:

| Check | Required answer | If it is wrong |
|---|---|---|
| `docker --version` | any version that supports `docker compose` | Install Docker Engine with the Compose plugin. |
| `powerprofilesctl get` | `balanced`, `performance`, or `power-saver` | `power-profiles-daemon` is not running. Install it, or the run cannot hold a power policy. |
| `/sys/class/power_supply/AC/online` | `1` | Plug the laptop in. On battery the numbers mean nothing, and `perf_env.sh lock` refuses to run. |
| `ss -ltn` | the column header only, with no rows under it | Another process holds a port the injector needs. Stop it. |

Start the dependencies and the injector, then confirm both proxies came up:

```bash
docker compose --profile chaos up -d
curl -s localhost:8474/proxies
scripts/inject_fault.py --target mysql --list
scripts/inject_fault.py --target redis --list
```

Both `--list` calls must report that no toxics are injected. A toxic left behind by an earlier
session is the single most confusing way to start: every later measurement is wrong by a constant
nobody can see.

Point the application at the proxy ports, not at the dependencies:

```bash
MYSQL_PORT=23306 REDIS_URL=redis://localhost:26379/0 .venv/bin/uvicorn app.main:app --port 8000
```

An application still connected to `3307` and `6379` bypasses the injector completely, and every
experiment then measures a healthy system. Confirm the path before you trust a run:

```bash
scripts/inject_fault.py --target mysql latency --ms 800
curl -s -o /dev/null -w '%{time_total}\n' http://localhost:8000/v1/articles?limit=1
scripts/inject_fault.py --target mysql --clear
```

The timed call must take about 0.8 s longer than an unfaulted one. If it does not, the application
is not going through the proxy.

## Running the experiment set

Hold the machine still for the whole set, exactly as the load matrix does. Three of the four
experiments produce wall-clock figures that reach a document, and a number measured at `balanced`
cannot be compared with one measured at `performance`.

```bash
scripts/perf_env.sh lock
scripts/perf_env.sh report docs/resilience/results/chaos-0-before.json
```

Take run 0 first. It is the same steady scenario through the proxy with **no toxics**, and every
later delta is measured against it. The proxy hop is real, and it belongs in the baseline rather
than in the result:

```bash
docker compose --profile load run --rm -e RUN_ID=chaos-0 k6 run /scripts/scenarios/steady.js
```

Then run each experiment: start the scenario, inject the fault about 60 seconds in, clear it
before the run ends, and take the closing snapshot.

| # | Experiment | Fault to inject |
|---|---|---|
| 0 | Baseline through the proxy | none |
| 1 | MySQL slow | `scripts/inject_fault.py --target mysql latency --ms 2500` |
| 2 | Redis unavailable | `scripts/inject_fault.py --target redis blackhole` |
| 3 | MySQL unreachable | `scripts/inject_fault.py --target mysql blackhole` |
| 4 | MySQL refusing connections | `scripts/inject_fault.py --target mysql reset_peer` |

Size the latency in experiment 1 against the bounds in
[configuration-reference.md](../operations/configuration-reference.md), never against a round
number. A delay under every timeout trips nothing and measures the proxy. 2,500 ms sits **above**
the 2.0 s statement timeout and **below** the 3.0 s call timeout, which is the interesting place
to stand: the delay is on the wire, so the server-side `max_execution_time` never sees a slow
query and cannot fire. One round trip therefore survives, and a cache-aside read that makes
several does not. That measures what the call timeout catches when the statement timeout cannot.

Between experiments, clear the fault and let the machine settle:

```bash
scripts/inject_fault.py --target mysql --clear
SETTLE_SECONDS=60 scripts/perf_env.sh report docs/resilience/results/chaos-1-after.json
```

Repeat at least one experiment at the end of the set. Without a run and its repeat there is no
noise floor, and no reader can tell a real change from drift. The load matrix measured 36.7%
package-throttle drift between one such pair on this chassis, which is why the repeat is not
optional.

## Injecting one fault by hand

Four faults, each a different failure mode in a different place in the request path:

```bash
scripts/inject_fault.py --target mysql latency --ms 3000 --jitter-ms 200
scripts/inject_fault.py --target mysql timeout --ms 5000
scripts/inject_fault.py --target mysql blackhole
scripts/inject_fault.py --target redis reset_peer
```

| Fault | What the dependency does | What it tests |
|---|---|---|
| `latency` | Answers, late | The call timeout, and whether a retry makes the queue worse |
| `timeout` | Answers nothing, then closes the connection after the given wait | Recovery after a mid-query disconnect |
| `blackhole` | Holds the connection open and never answers | The client-side bound, because nothing else ever returns |
| `reset_peer` | Refuses at once | The fast-failure path, and that a refusal is not retried forever |

Watch the effect while a fault is held:

```bash
scripts/inject_fault.py --target mysql --list
curl -i http://localhost:8000/v1/articles/1
curl -i http://localhost:8000/health/ready
curl -s localhost:8000/metrics | grep -E 'circuit_breaker|dependency_timeouts'
```

Then clear it. Always clear a fault before you change to the next one, or the second measurement
carries the first fault as well:

```bash
scripts/inject_fault.py --target mysql --clear
```

## Collecting the results

Every run writes a k6 summary named by its `RUN_ID`, and every run is bracketed by two machine
snapshots. Keep all three together under `docs/resilience/results/`, mirroring
`docs/performance/results/`:

```bash
RESULTS_DIR=docs/resilience/results ls -1 docs/resilience/results
scripts/perf_env.sh report docs/resilience/results/chaos-1-after.json
```

Read the server side from Prometheus over the same window, so the client-side and server-side
views of one fault sit side by side:

```bash
docker compose --profile observability up -d
curl -s 'localhost:9090/api/v1/query?query=circuit_breaker_state'
curl -s 'localhost:9090/api/v1/query?query=sum(rate(degraded_responses_total[5m]))by(reason)'
```

## Verifying the run is citable

A chaos snapshot carries one key the load snapshots do not: `toxiproxy_cpuset`. A claim about
latency under fault needs a record of where the injector ran, because it competes for the same
silicon as the service.

```bash
.venv/bin/python -c "
import scripts.run_metadata as m
before = m.load('docs/resilience/results/chaos-1-before.json', required=m.CHAOS_REQUIRED_KEYS)
after = m.load('docs/resilience/results/chaos-1-after.json', required=m.CHAOS_REQUIRED_KEYS)
print(m.is_citable(before, after, required=m.CHAOS_REQUIRED_KEYS))"
```

The pair must come back `(True, [])`. Then check the repeat against its original, which is the
rule that sets the noise floor:

```bash
.venv/bin/python -c "
import scripts.run_metadata as m
load = lambda p: m.load(p, required=m.CHAOS_REQUIRED_KEYS)
run = (load('docs/resilience/results/chaos-1-before.json'),
       load('docs/resilience/results/chaos-1-after.json'))
repeat = (load('docs/resilience/results/chaos-1r-before.json'),
          load('docs/resilience/results/chaos-1r-after.json'))
print(m.repeat_is_consistent(run, repeat, required=m.CHAOS_REQUIRED_KEYS))"
```

Report a failed check in the chaos report rather than re-running until it passes. The load report
publishes its own failed drift check for the same reason.

## Teardown and restoring the machine

Clear every fault first. A toxic left on a proxy makes the next ordinary test run fail for no
visible reason, hours later:

```bash
scripts/inject_fault.py --target mysql --clear
scripts/inject_fault.py --target redis --clear
docker compose stop toxiproxy k6 prometheus grafana
scripts/perf_env.sh unlock
powerprofilesctl get
```

The last command must print `balanced`. Never run `docker compose down`: it removes the mysql
container, which declares no named volume, and the seeded dataset goes with it.

## When it goes wrong

### Every request succeeds while a fault is held

The application is connected to the dependency and not to the proxy. Check the port it started
with. `MYSQL_PORT` must be `23306` and `REDIS_URL` must name port `26379`.

### The injector cannot be reached

`scripts/inject_fault.py` exits 3 and names the URL. The chaos profile is not up, or `TOXIPROXY_URL`
points somewhere else. Start it with `docker compose --profile chaos up -d`.

### A proxy is missing

Toxiproxy reads its proxies from `toxiproxy/toxiproxy.json` at start-up. A proxy added to that
file reaches the container only after a restart of the service.

### The first request after a fault clears is still slow

A pooled connection opened during the fault can carry its state for a while. Give the pool a few
seconds, or restart the application, before you record a recovery time.

### The chaos tests skip

`pytest -m chaos` skips when port 8474 is closed. That is the same gate the scale tests use for
nginx, and it is why CI stays green without the injector.

### The run and its repeat drifted

The chassis moved between them, so the pair sets no noise floor. Report the drift and say which
figures it applies to, the way the load report does.

## Parameter reference

Every knob the commands above accept. Defaults are what the scripts use when you set nothing.

| Variable | Default | Effect | Used by |
|---|---|---|---|
| `TOXIPROXY_URL` | `http://localhost:8474` | The injector control API. | `inject_fault.py`, `conftest.py` |
| `TOXIPROXY_CPUSET` | `0-11` | CPUs the injector runs on. The six P-cores, beside the service it proxies and away from the E-cores the generator owns. | `docker-compose.yml`, `perf_env.sh` |
| `TOXIPROXY_MYSQL_PORT` | `23306` | Host port of the proxied MySQL. | `conftest.py` |
| `TOXIPROXY_REDIS_PORT` | `26379` | Host port of the proxied Redis. | `conftest.py` |
| `MYSQL_PORT` | `3306` | Where the application looks for MySQL. Set it to the proxy port for a chaos run. | `.env.example` |
| `REDIS_URL` | `redis://localhost:6379/0` | Where the application looks for Redis. Point it at the proxy port for a chaos run. | `.env.example` |
| `K6_CPUSET` | `12-19` | CPUs the generator runs on, held the same as the load matrix so the two sets of figures compare. | `docker-compose.yml`, `perf_env.sh` |
| `SETTLE_SECONDS` | `3` | Pause after the power profile changes, before the state is read back. Raise it between experiments. | `perf_env.sh` |
| `RUN_ID` | `adhoc` | Names every file the run writes. Use `chaos-0`, `chaos-1`, and `chaos-1r` for the repeat. | `summary.js` |
| `RESULTS_DIR` | `docs/performance/results` | Where summaries land. Point it at `docs/resilience/results` for a chaos run. | `run_load_matrix.sh`, `summary.js` |
| `BASE_URL` | `http://nginx:80` | What the generator targets. Use `http://host.docker.internal:8000` for a host uvicorn. | `api.js` |
| `CACHE_ENABLED` | `true` | Cache-aside on or off. Experiment 4 needs it on, because the fallback it tests is the cache. | `docker-compose.yml` |

## Change control

Update this runbook in the same commit as the change it describes. `tests/unit/test_chaos_runbook.py`
fails the build when a command here stops resolving: a missing script, an undeclared compose
profile, an unknown fault, or a knob nobody documented. Those checks prove that a command resolves,
never that a step is enough. Walk the document from a stopped stack after any change to the
injector or the compose profile, and record what you had to work out on the way.
