// Every request the scenarios make, tagged so each operation gets its own latency series.
//
// Tagging is not cosmetic. The NFR sets a read P95 of 200 ms and a write P95 of 500 ms; one
// undifferentiated `http_req_duration` cannot be judged against either, because a 5% write mix
// drags the read number and hides the write number.

import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { buildBody, buildTitle } from './workload.js';

export const BASE_URL = (__ENV.BASE_URL || 'http://nginx:80').replace(/\/$/, '');

// One Trend per operation, so the summary carries four independent percentile sets.
export const trends = {
  read_detail: new Trend('op_read_detail', true),
  read_list: new Trend('op_read_list', true),
  write_create: new Trend('op_write_create', true),
  write_update: new Trend('op_write_update', true),
};

function record(op, response) {
  trends[op].add(response.timings.duration);
  return response;
}

// Registration and login run once, in setup(). Argon2id is deliberately expensive, so a
// per-iteration login would measure the hash function rather than the API.
export function authenticate() {
  const email = `k6-writer-${Date.now()}-${Math.floor(Math.random() * 1e6)}@loadtest.example`;
  const password = 'k6-load-test-writer-passphrase';
  const headers = { 'Content-Type': 'application/json' };

  const registered = http.post(`${BASE_URL}/v1/auth/register`, JSON.stringify({ email, password }), {
    headers,
    tags: { op: 'setup_register' },
  });
  if (registered.status !== 201) {
    throw new Error(`registration failed: ${registered.status} ${registered.body}`);
  }

  const loggedIn = http.post(`${BASE_URL}/v1/auth/login`, JSON.stringify({ email, password }), {
    headers,
    tags: { op: 'setup_login' },
  });
  if (loggedIn.status !== 200) {
    throw new Error(`login failed: ${loggedIn.status} ${loggedIn.body}`);
  }

  return { token: loggedIn.json('access_token'), email };
}

export function readDetail(id) {
  const response = record(
    'read_detail',
    http.get(`${BASE_URL}/v1/articles/${id}`, { tags: { op: 'read_detail' } })
  );
  // 404 is a correct answer for an id the seeder never wrote, so it is not an error here.
  check(response, { 'detail read answered': (r) => r.status === 200 || r.status === 404 });
  return response;
}

export function readList(walk, workload) {
  const query = walk.cursor
    ? `?limit=${workload.page_size}&cursor=${encodeURIComponent(walk.cursor)}`
    : `?limit=${workload.page_size}`;
  const response = record(
    'read_list',
    http.get(`${BASE_URL}/v1/articles${query}`, { tags: { op: 'read_list' } })
  );
  const ok = check(response, { 'list read answered 200': (r) => r.status === 200 });
  if (ok) {
    walk.advance(response.json('next_cursor'));
    if (walk.done) {
      walk.reset();
    }
  } else {
    walk.reset();
  }
  return response;
}

export function createArticle(token, rand, workload) {
  const payload = JSON.stringify({
    title: buildTitle(rand),
    body: buildBody(rand, workload.body_bytes_min, workload.body_bytes_max),
  });
  const response = record(
    'write_create',
    http.post(`${BASE_URL}/v1/articles`, payload, {
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      tags: { op: 'write_create' },
    })
  );
  check(response, { 'create answered 201': (r) => r.status === 201 });
  return response.status === 201 ? response.json() : null;
}

// Read, modify, write. The GET is part of the operation: If-Match carries the article's current
// updated_at, so a client cannot update without first reading. The pair also exercises
// invalidation, which a bare POST never touches.
export function updateArticle(token, article, rand, workload) {
  const current = http.get(`${BASE_URL}/v1/articles/${article.id}`, {
    tags: { op: 'write_update_read' },
  });
  if (current.status !== 200) {
    return null;
  }
  const payload = JSON.stringify({
    title: buildTitle(rand),
    body: buildBody(rand, workload.body_bytes_min, workload.body_bytes_max),
  });
  const response = record(
    'write_update',
    http.put(`${BASE_URL}/v1/articles/${article.id}`, payload, {
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
        'If-Match': current.json('updated_at'),
      },
      tags: { op: 'write_update' },
    })
  );
  // 409 means another iteration updated the same row first, which is the concurrency control
  // working, not a failure.
  check(response, { 'update answered 200 or 409': (r) => r.status === 200 || r.status === 409 });
  return response;
}
