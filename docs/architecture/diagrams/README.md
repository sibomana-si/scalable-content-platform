# Diagrams (source-controlled)

> **Status:** 🟧 In progress · **Owner:** Simon Sibomana · **Last updated:** 2026-06-10

Diagrams are kept as **code** (Mermaid) so they are diffable and reviewed in PRs. The `.mmd` files in
this directory are the canonical sources; this page embeds the same Mermaid for rendering in the docs
site. When a diagram changes, update the `.mmd` source and the embedded copy together. Export rendered
images only when needed for reports.

## Diagram inventory

| # | Diagram (required by the project brief) | Source | Rendered |
|---|---|---|---|
| 1 | High-level system architecture (C4 L1/L2) | — (maintained inline) | [overview.md](../overview.md) |
| 2 | Request flow — read path (cache-aside) | [`read-path.mmd`](read-path.mmd) | below |
| 3 | Authentication & authorization flow | [`auth-flow.mmd`](auth-flow.mmd) | below |
| 4 | Observability signal flow | [`observability-flow.mmd`](observability-flow.mmd) | below |
| 5 | Failure handling (resilience) | [`failure-handling.mmd`](failure-handling.mmd) | below |

## 2. Read path (cache-aside)

The performance-critical journey ([PRD G1](../../requirements/product-requirements.md): read P95 < 200 ms).
Cache-aside with TTL + jitter and single-flight loading guards against stampedes
([caching-strategy](../../data/caching-strategy.md)); a cold or unavailable Redis degrades latency,
never correctness.

```mermaid
sequenceDiagram
    participant C as Client (anonymous)
    participant A as FastAPI
    participant R as Redis
    participant D as MySQL

    C->>A: GET /v1/articles/{id}
    Note over A: timeout budgets on every hop<br/>(see fault-tolerance-design)
    A->>R: GET article:{id}
    alt cache hit (target ≥ 90%)
        R-->>A: cached JSON
    else cache miss
        Note over A: single-flight lock — only one<br/>loader per key under concurrency
        A->>D: SELECT … WHERE id = ? AND deleted_at IS NULL
        D-->>A: row
        A->>R: SET article:{id} EX ttl+jitter
    end
    A-->>C: 200 OK
    Note over C,A: request_id correlates logs / metrics / traces

    rect rgb(255, 240, 240)
        Note over A,R: Redis unavailable → skip cache,<br/>read MySQL directly (degraded latency, correct data)
    end
```

## 3. Authentication & authorization flow

Registration ([D6 password policy](../../requirements/product-requirements.md)), login issuing a
15-minute JWT with no refresh token ([D1](../../requirements/product-requirements.md)), and the
middleware order on authenticated writes: JWT validation (`401`) before RBAC (`403`). Public reads
bypass both — see [authn-authz](../../security/authn-authz.md).

```mermaid
sequenceDiagram
    participant C as Client
    participant A as FastAPI (middleware → router)
    participant D as MySQL

    Note over C,D: Registration (FR-001)
    C->>A: POST /v1/auth/register {email, password}
    A->>A: validate input (Pydantic)<br/>password policy: ≥ 12 chars, breached-list screen (D6)
    A->>D: INSERT user (bcrypt/argon2 hash, role=user)
    A-->>C: 201 Created (no PII echo)

    Note over C,D: Login (FR-002)
    C->>A: POST /v1/auth/login {email, password}
    A->>D: SELECT user + role
    A->>A: verify hash
    alt valid credentials
        A-->>C: 200 {access_token} — JWT, 15-min TTL, no refresh token (D1)
    else invalid
        A-->>C: 401 (audit-logged with request_id)
    end

    Note over C,D: Authenticated write (FR-003/004)
    C->>A: PUT /v1/articles/{id} + Authorization: Bearer
    A->>A: JWT middleware — verify signature & expiry
    alt token missing / expired
        A-->>C: 401 Unauthorized (re-authenticate)
    else token valid
        A->>A: RBAC middleware — owner or admin?
        alt role/ownership check fails
            A-->>C: 403 Forbidden
        else authorized
            A->>D: UPDATE … (optimistic concurrency, D11)
            A-->>C: 200 OK
        end
    end

    Note over C,A: Public reads (GET article/list) bypass<br/>JWT and RBAC entirely — no auth required
```

## 4. Observability signal flow

Every request emits the three signals — JSON logs, RED metrics, OTEL traces — correlated by
`request_id`. Telemetry export is **best-effort**: collector loss must never fail or slow request
handling ([PRD §10](../../requirements/product-requirements.md)). Details in the
[observability guide](../../observability/observability-guide.md).

```mermaid
graph LR
    req[Incoming request] --> rid[Request-ID middleware<br/>assign request_id]

    rid --> logs[Structured JSON logs<br/>100% of requests]
    rid --> metrics[RED metrics<br/>rate · errors · duration P50/P95/P99]
    rid --> traces[OTEL spans<br/>HTTP + DB + cache spans]

    logs --> agg[Log aggregation]
    metrics --> exp["/metrics endpoint"]
    exp -- scrape --> prom[Prometheus]
    traces -- "OTLP (best-effort, async)" --> otel[OTEL Collector]
    otel --> prom

    prom --> graf[Grafana dashboards<br/>latency · error rate · throughput · cache hit ratio]
    prom --> alert[Alerting<br/>SLO error-budget burn · saturation]
    alert --> oncall[Operator / SRE<br/>+ runbooks]
    graf --> oncall
    agg --> oncall

    style otel stroke-dasharray: 5 5
```

## 5. Failure handling (resilience)

The degradation ladder when dependencies misbehave: Redis loss falls through to MySQL; MySQL
impairment is bounded by timeouts → bounded retries with backoff → circuit breaker → load shedding
with an explicit `503 + Retry-After`, never a hang or cascade. Policies and budgets in
[fault-tolerance-design](../../resilience/fault-tolerance-design.md).

```mermaid
graph TD
    req[Request] --> path{Dependency call<br/>with explicit timeout}

    path -- "Redis down / timeout" --> fallthrough[Fall through to MySQL<br/>degraded latency, correct data]
    fallthrough --> ok1[200 OK]

    path -- "MySQL slow / error" --> retry{Retry with backoff + jitter<br/>idempotent reads only}
    retry -- recovered --> ok2[200 OK]
    retry -- "failures exceed threshold" --> cb[Circuit breaker OPENS<br/>fail fast, no queue pile-up]

    cb --> shed[Load shedding / fallback<br/>503 + Retry-After<br/>no hanging, no cascade]
    cb -- "cool-off elapsed" --> half[HALF-OPEN<br/>limited probe requests]
    half -- "probe succeeds" --> closed[Breaker CLOSES<br/>normal service resumes]
    half -- "probe fails" --> cb

    shed -.-> alert[Alert: error-budget burn<br/>operator + runbook]
```

**Invariants encoded above:** every external call has a timeout (nothing waits forever); retries are
bounded and backed off (no retry storms); Redis loss is a latency event, never a correctness event;
MySQL impairment produces fast, explicit errors — not cascading hangs.
