// Degradation and recovery: settle at a low rate, jump hard, then drop back.
//
// Two questions, neither answerable from the steady run. Does the service shed or fall over when
// load arrives faster than it can scale? And does latency return to baseline afterwards, or does
// a queue stay full? The recovery leg is the half people forget to measure.
//
//   docker compose --profile load run --rm k6 run /scripts/scenarios/spike.js

import { slo } from '../lib/workload.js';
import { iteration, setup as authSetup } from '../lib/iteration.js';
import { makeHandleSummary } from '../lib/summary.js';

const config = slo.scenarios.spike;

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-arrival-rate',
      startRate: config.base_rps,
      timeUnit: '1s',
      stages: [
        { target: config.base_rps, duration: config.settle_duration },
        // Zero-duration step: the jump is instant, which is the point.
        { target: config.peak_rps, duration: '1s' },
        { target: config.peak_rps, duration: config.spike_duration },
        { target: config.base_rps, duration: '1s' },
        { target: config.base_rps, duration: config.recovery_duration },
      ],
      preAllocatedVUs: Number(__ENV.PRE_ALLOCATED_VUS || config.pre_allocated_vus),
      maxVUs: Number(__ENV.MAX_VUS || config.max_vus),
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export const setup = authSetup;
export default iteration;
export const handleSummary = makeHandleSummary('spike');
