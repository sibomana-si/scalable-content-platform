// Unit tests for lib/workload.js, in k6, with no infrastructure.
//
// A distribution bug here is silent and expensive: the picker still returns valid ids, the run
// still completes, and the hit ratio it reports is fiction. Seconds spent here save a matrix.
//
//   docker compose --profile load run --rm k6 run /scripts/selftest.js
//
// The `checks` threshold sets the exit code, so a failure stops a script that calls this first.

import { check } from 'k6';
import {
  buildBody,
  createCursorWalk,
  hotSetSize,
  makeRng,
  pickArticleId,
  slo,
} from './lib/workload.js';

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: { checks: ['rate==1.0'] },
};

const DRAWS = 100000;
const workload = slo.workload;

function draw(count, override) {
  const rand = makeRng(20260822);
  const shape = Object.assign({}, workload, override || {});
  const ids = new Array(count);
  for (let i = 0; i < count; i += 1) {
    ids[i] = pickArticleId(rand, shape);
  }
  return ids;
}

export default function selftest() {
  // --- the picker stays inside the dataset ---
  const ids = draw(DRAWS);
  check(ids, {
    'every id is inside the seeded range': (drawn) =>
      drawn.every((id) => Number.isInteger(id) && id >= 1 && id <= workload.articles),
  });

  // --- the hot set takes the documented share of the traffic ---
  const hot = hotSetSize(workload.articles, workload.hot_set_share);
  const hits = ids.filter((id) => id <= hot).length / ids.length;
  check(hits, {
    'the hot set takes its documented share of the reads': (rate) =>
      Math.abs(rate - workload.hot_set_traffic) < 0.02,
  });

  // --- the cold tail is reached, or the run never misses ---
  const coldIds = new Set(ids.filter((id) => id > hot));
  check(coldIds, {
    'the cold tail is drawn from too': (set) => set.size > 100,
  });

  // --- a hot share of zero is the flat control case ---
  const flat = draw(DRAWS, { hot_set_share: 0 });
  const firstFifth = flat.filter((id) => id <= workload.articles / 5).length / flat.length;
  check(firstFifth, {
    'a hot share of zero spreads the draws evenly': (rate) => Math.abs(rate - 0.2) < 0.02,
  });

  // --- the cursor walk terminates, even against a server that always offers another page ---
  const walk = createCursorWalk(workload);
  let guard = 0;
  while (!walk.done && guard < 1000) {
    walk.advance('an-endless-cursor');
    guard += 1;
  }
  check(walk, {
    'the cursor walk stops at max_pages': (w) => w.done && w.pages === workload.max_pages,
  });

  // --- and a walk that runs out of pages stops early ---
  const shortWalk = createCursorWalk(workload);
  shortWalk.advance('page-two');
  shortWalk.advance(null);
  check(shortWalk, {
    'the cursor walk stops when the server offers no next page': (w) => w.done && w.pages === 2,
  });

  // --- reset makes a walk reusable ---
  shortWalk.reset();
  check(shortWalk, {
    'a reset walk starts over': (w) => !w.done && w.pages === 0 && w.cursor === null,
  });

  // --- the payload builder respects its byte bounds ---
  const rand = makeRng(7);
  let inBounds = true;
  let ascii = true;
  for (let i = 0; i < 2000; i += 1) {
    const body = buildBody(rand, workload.body_bytes_min, workload.body_bytes_max);
    inBounds =
      inBounds && body.length >= workload.body_bytes_min && body.length <= workload.body_bytes_max;
    ascii = ascii && !/[^\x00-\x7F]/.test(body);
  }
  check(null, {
    'every body stays inside its byte bounds': () => inBounds,
    'every body is ascii, so characters and bytes agree': () => ascii,
  });

  // --- an exact-size request returns exactly that size ---
  check(buildBody(makeRng(1), 16, 16), {
    'a fixed byte bound returns exactly that many bytes': (body) => body.length === 16,
  });

  // --- the same seed replays the same draws ---
  check(draw(1000), {
    'the same seed replays the same draws': (again) =>
      again.every((id, index) => id === ids[index]),
  });
}
