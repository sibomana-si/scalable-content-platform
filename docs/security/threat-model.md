# Threat Model

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

Lightweight [STRIDE](https://en.wikipedia.org/wiki/STRIDE_model) analysis over the trust boundaries from the [C4 diagrams](../architecture/overview.md).

## Trust Boundaries
- Client ↔ API (untrusted input)
- API ↔ MySQL / Redis (internal network)
- API ↔ secrets store

## STRIDE Analysis
| Category | Threat | Asset | Mitigation | Status |
|---|---|---|---|---|
| **S**poofing | Forged/stolen JWT | User identity | Signature + short TTL + HTTPS | 🟥 |
| **T**ampering | Modified request/body | Articles, roles | Validation, parameterized queries | 🟥 |
| **R**epudiation | Denying actions | Audit | Structured logs w/ request_id | 🟥 |
| **I**nfo disclosure | Leaking PII/internals | Users, errors | Error catalog hides internals; TLS | 🟥 |
| **D**oS | Resource exhaustion | Availability | Rate limiting, timeouts, load shedding | 🟥 |
| **E**oP | Privilege escalation | Admin ops | RBAC, ownership checks, IDOR tests | 🟥 |

## Practice Scenarios
- Attempt auth bypass · injection testing · least-privilege validation.
