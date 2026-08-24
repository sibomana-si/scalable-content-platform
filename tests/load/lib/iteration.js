// The read/write mix every scenario shares.
//
// Kept in one place because the mix is the workload. Three scenarios that each defined their
// own would compare three different things while appearing to compare arrival rates.

import { authenticate, createArticle, readDetail, readList, updateArticle } from './api.js';
import { createCursorWalk, makeRng, pickArticleId, slo } from './workload.js';

const workload = slo.workload;

// Per-VU state. Seeded from the VU id so each VU walks its own id sequence and the whole run
// still repeats exactly.
let rand = null;
let walk = null;

function vuState() {
  if (rand === null) {
    const vu = (typeof __VU === 'number' ? __VU : 1) + 1;
    rand = makeRng(vu * 2654435761);
    walk = createCursorWalk(workload);
  }
  return { rand, walk };
}

export function setup() {
  return authenticate();
}

// One iteration is one operation, not one session. The arrival rate then means requests per
// second, which is the unit the NFR states its throughput target in.
export function iteration(data) {
  const state = vuState();
  const draw = state.rand();

  if (draw >= workload.read_share) {
    // The write half alternates create and update, so invalidation is exercised both ways.
    if (state.rand() < 0.5) {
      createArticle(data.token, state.rand, workload);
    } else {
      const created = createArticle(data.token, state.rand, workload);
      if (created) {
        updateArticle(data.token, created, state.rand, workload);
      }
    }
    return;
  }

  if (state.rand() < workload.detail_share_of_reads) {
    readDetail(pickArticleId(state.rand, workload));
  } else {
    readList(state.walk, workload);
  }
}

// The pass/fail line, straight from slo.json. Applied to the steady run only: ramp and spike
// exist to find the point where these break, so failing them there is the result, not a fault.
export function sloThresholds() {
  return {
    op_read_detail: [
      `p(50)<${slo.latency_ms.read_p50}`,
      `p(95)<${slo.latency_ms.read_p95}`,
      `p(99)<${slo.latency_ms.read_p99}`,
    ],
    op_read_list: [`p(95)<${slo.latency_ms.read_p95}`],
    op_write_create: [`p(95)<${slo.latency_ms.write_p95}`],
    op_write_update: [`p(95)<${slo.latency_ms.write_p95}`],
    http_req_failed: [`rate<${slo.error_rate}`],
    dropped_iterations: ['count==0'],
  };
}
