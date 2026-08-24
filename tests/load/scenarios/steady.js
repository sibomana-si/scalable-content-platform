// The pass/fail run: a 95/5 read/write mix held at a constant arrival rate.
//
// Constant arrival rate, not constant VUs. A closed model backs off when the server slows, so
// throughput flattens into a line that looks like a ceiling and is really the generator waiting.
// An open model holds the offered rate and lets the queue grow, which is what saturation is.
//
//   docker compose --profile load run --rm k6 run /scripts/scenarios/steady.js
//   RATE=300 DURATION=2m docker compose --profile load run --rm k6 run /scripts/scenarios/steady.js

import { slo } from '../lib/workload.js';
import { iteration, setup as authSetup, sloThresholds } from '../lib/iteration.js';
import { makeHandleSummary } from '../lib/summary.js';

const config = slo.scenarios.steady;

export const options = {
  scenarios: {
    steady: {
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || config.rate_rps),
      timeUnit: '1s',
      duration: __ENV.DURATION || config.duration,
      preAllocatedVUs: Number(__ENV.PRE_ALLOCATED_VUS || config.pre_allocated_vus),
      maxVUs: Number(__ENV.MAX_VUS || config.max_vus),
    },
  },
  thresholds: sloThresholds(),
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export const setup = authSetup;
export default iteration;
export const handleSummary = makeHandleSummary('steady');
