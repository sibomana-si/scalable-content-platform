// Find the knee: step the arrival rate up until latency leaves the target band.
//
// No SLO thresholds here on purpose. The run climbs past the point where the targets hold, so a
// threshold failure would say only "the ramp ramped". The result is the step at which P95
// crosses the line, read off the per-stage numbers.
//
//   docker compose --profile load run --rm k6 run /scripts/scenarios/ramp.js

import { slo } from '../lib/workload.js';
import { iteration, setup as authSetup } from '../lib/iteration.js';
import { makeHandleSummary } from '../lib/summary.js';

const config = slo.scenarios.ramp;

function stages() {
  const built = [];
  for (let step = 1; step <= config.steps; step += 1) {
    built.push({
      target: config.start_rps + (step - 1) * config.step_rps,
      duration: __ENV.STEP_DURATION || config.step_duration,
    });
  }
  return built;
}

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate',
      startRate: config.start_rps,
      timeUnit: '1s',
      // Each stage holds a flat rate: `target` equal to the previous target would ramp, and a
      // sloped rate gives no window at which any single number is true.
      stages: stages(),
      preAllocatedVUs: Number(__ENV.PRE_ALLOCATED_VUS || config.pre_allocated_vus),
      maxVUs: Number(__ENV.MAX_VUS || config.max_vus),
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export const setup = authSetup;
export default iteration;
export const handleSummary = makeHandleSummary('ramp');
