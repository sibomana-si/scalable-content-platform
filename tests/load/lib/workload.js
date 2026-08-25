// Workload shape: the hot-set id picker, the cursor walk, and the payload builder.
//
// The picker is the single most load-bearing function in this tree. The >= 90% cache hit
// ratio target holds only under hot-set dominated access (capacity-scaling-model.md). Draw ids
// uniformly across 10,000 rows against a 300 s TTL and the run measures a cache that cannot
// work, then reports that the design failed. selftest.js exercises every function here.

export const slo = JSON.parse(open('./slo.json'));

// mulberry32: a small, fast, seedable PRNG. k6 gives no seed control over Math.random, and a
// distribution test needs a repeatable draw sequence.
export function makeRng(seed) {
  let state = seed >>> 0;
  return function rand() {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// The seeder writes one contiguous block of ids, but MySQL `AUTO_INCREMENT` does not restart at
// 1 after a delete, so the block rarely begins there. `seed_load_dataset.py --emit-range` records
// where it does begin, and the matrix passes that in. Drawing from 1..N against a block that
// starts at 5001 reads ids that do not exist, and every 404 is a cheap miss that never populates
// the cache — the hit ratio then measures the wrong thing and still looks plausible.
export const ARTICLE_ID_BASE = Number(__ENV.ARTICLE_ID_MIN || 1);

export function hotSetSize(articles, hotSetShare) {
  return Math.floor(articles * hotSetShare);
}

// Draw an article id. `hotSetTraffic` of the draws land in the first `hotSetShare` of the ids;
// the rest spread over the cold tail. A hot set of zero gives a flat distribution.
export function pickArticleId(rand, workload, idBase) {
  const base = idBase === undefined ? ARTICLE_ID_BASE : idBase;
  const articles = workload.articles;
  const hot = hotSetSize(articles, workload.hot_set_share);
  if (hot > 0 && rand() < workload.hot_set_traffic) {
    return base + Math.floor(rand() * hot);
  }
  return base + hot + Math.floor(rand() * (articles - hot));
}

// Walk keyset pages, and stop. Two things end the walk: the server returns no next cursor, or
// `maxPages` is reached. The bound is not politeness — an unbounded walk on a 10,000-row table
// would spend the whole run on page 400, where no real client ever goes.
export function createCursorWalk(workload) {
  let cursor = null;
  let pages = 0;
  return {
    get cursor() {
      return cursor;
    },
    get pages() {
      return pages;
    },
    get done() {
      return cursor === null && pages > 0;
    },
    // Feed it whatever `next_cursor` the response carried. Returns true while more remain.
    advance(nextCursor) {
      pages += 1;
      cursor = pages >= workload.max_pages ? null : nextCursor || null;
      return !this.done;
    },
    reset() {
      cursor = null;
      pages = 0;
    },
  };
}

const FILLER = 'the quick brown fox jumps over the lazy dog and files a report about it ';

// ASCII only, so characters and bytes agree and the Redis memory model measures what it says.
export function buildBody(rand, minBytes, maxBytes) {
  const size = minBytes + Math.floor(rand() * (maxBytes - minBytes + 1));
  return FILLER.repeat(Math.ceil(size / FILLER.length)).slice(0, size);
}

export function buildTitle(rand) {
  return `k6 article ${Math.floor(rand() * 1e9)}`;
}
