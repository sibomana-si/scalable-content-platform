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

// The shape is overridable so the scale-out run can climb past where one replica bends.
// A run that overrides it says so in its results.
const startRate = Number(__ENV.START_RPS || config.start_rps);
const stepRate = Number(__ENV.STEP_RPS || config.step_rps);
const stepCount = Number(__ENV.STEPS || config.steps);

function stages() {
  const built = [];
  for (let step = 1; step <= stepCount; step += 1) {
    built.push({
      target: startRate + (step - 1) * stepRate,
      duration: __ENV.STEP_DURATION || config.step_duration,
    });
  }
  return built;
}

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate',
      startRate: startRate,
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
