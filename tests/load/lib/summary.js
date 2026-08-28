// Where a run's numbers land, and under what name.
//
// A summary written to stdout is lost the moment the terminal scrolls. Each run writes one JSON
// file named for the scenario and the run id, beside the two perf_env.sh metadata snapshots, so
// a result can be cited months later without anyone having been in the room.

export const RUN_ID = __ENV.RUN_ID || 'adhoc';
export const RESULTS_DIR = __ENV.RESULTS_DIR || '/results';

function value(metrics, name, field) {
  const metric = metrics[name];
  if (!metric || metric.values[field] === undefined) {
    return null;
  }
  return Math.round(metric.values[field] * 1000) / 1000;
}

// The short line a human reads while the matrix is still running.
export function headline(scenario, data) {
  const metrics = data.metrics;
  const parts = [
    `scenario=${scenario}`,
    `run=${RUN_ID}`,
    `reqs=${value(metrics, 'http_reqs', 'count')}`,
    `rps=${value(metrics, 'http_reqs', 'rate')}`,
    `p95=${value(metrics, 'http_req_duration', 'p(95)')}ms`,
    `p99=${value(metrics, 'http_req_duration', 'p(99)')}ms`,
    `failed=${value(metrics, 'http_req_failed', 'rate')}`,
    `dropped=${value(metrics, 'dropped_iterations', 'count') || 0}`,
    `miss=${value(metrics, 'read_detail_miss', 'rate') || 0}`,
  ];
  return parts.join(' · ');
}

// dropped_iterations above zero means the generator could not hold the offered rate. The run is
// then measuring the laptop, not the service, so say so loudly rather than in a metrics table.
export function warnings(data) {
  const dropped = value(data.metrics, 'dropped_iterations', 'count') || 0;
  return dropped > 0
    ? [`dropped_iterations=${dropped}: the generator fell behind, this run is not citable`]
    : [];
}

export function makeHandleSummary(scenario) {
  return function handleSummary(data) {
    const path = `${RESULTS_DIR}/${scenario}-${RUN_ID}.json`;
    const notes = warnings(data);
    const lines = [headline(scenario, data), ...notes.map((note) => `WARNING ${note}`), ''];
    return {
      stdout: `${lines.join('\n')}\n`,
      [path]: JSON.stringify({ scenario, run_id: RUN_ID, warnings: notes, ...data }, null, 2),
    };
  };
}
