// The read/write mix every scenario shares.
//
// Kept in one place because the mix is the workload. Three scenarios that each defined their
// own would compare three different things while appearing to compare arrival rates.

import {
  authenticate,
  createArticle,
  probeArticle,
  readDetail,
  readList,
  updateArticle,
} from './api.js';
import { ARTICLE_ID_BASE, createCursorWalk, makeRng, pickArticleId, slo } from './workload.js';

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

// How many ids the probe draws, and how many of them may answer 404 before setup() gives up.
// One miss is a row a write deleted or an id the seeder skipped. Several are a picker aimed at
// the wrong block.
const PROBE_DRAWS = 20;
const PROBE_MISS_LIMIT = 1;

export function setup() {
  const data = authenticate();

  // Read a sample of the ids the run will read, before the run starts. A picker aimed outside
  // the seeded block answers 404 to everything, and no threshold catches it until the run ends:
  // `http_req_failed` stays 0 and the latency series looks plausible. One second of probing
  // costs less than five minutes of fiction. `ARTICLE_ID_MIN` defaults to 1, so a forgotten
  // export gives a quiet wrong answer, not an error.
  const rand = makeRng(0x5eed1d);
  const drawn = [];
  let missing = 0;
  for (let i = 0; i < PROBE_DRAWS; i += 1) {
    const id = pickArticleId(rand, workload);
    drawn.push(id);
    if (probeArticle(id).status === 404) {
      missing += 1;
    }
  }

  if (missing > PROBE_MISS_LIMIT) {
    const low = Math.min(...drawn);
    const high = Math.max(...drawn);
    throw new Error(
      `the picker drew ids ${low}-${high} but ${missing} of ${PROBE_DRAWS} are missing ` +
        `(ARTICLE_ID_BASE=${ARTICLE_ID_BASE}); reseed, or export ARTICLE_ID_MIN from ` +
        '`seed_load_dataset.py --emit-range`'
    );
  }

  return data;
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
    // A detail read that answers 404 costs nothing and caches nothing, so a storm of them
    // passes every latency threshold while measuring an empty table.
    read_detail_miss: ['rate<0.01'],
    dropped_iterations: ['count==0'],
  };
}
