# OWASP Top 10 Mitigation Matrix  
### Based on OWASP Top 10 (2021)

> **Status:** ✅ Approved · **Owner:** Simon Sibomana · **Last updated:** 2026-07-10

| # | Risk | Applicable? | Mitigation in this system | Status |
|---|---|---|---|---|
| A01 | Broken Access Control | Yes | RBAC + ownership checks; IDOR tests | 🟥 |
| A02 | Cryptographic Failures | Yes | TLS; password hashing (bcrypt/argon2); signed JWT | 🟥 |
| A03 | Injection | Yes | Parameterized queries / ORM; input validation | 🟥 |
| A04 | Insecure Design | Yes | Threat model; secure defaults | 🟥 |
| A05 | Security Misconfiguration | Yes | Hardened config; no debug in prod; secrets via env | 🟥 |
| A06 | Vulnerable Components | Yes | Dependency scanning in CI | 🟥 |
| A07 | Auth Failures | Yes | Strong auth, lockout/rate limit, short token TTL | 🟥 |
| A08 | Software & Data Integrity | Yes | Pinned deps, signed artifacts | 🟥 |
| A09 | Logging & Monitoring Failures | Yes | Structured logs, metrics, alerts | 🟥 |
| A10 | SSRF | Maybe | Validate/limit outbound requests | 🟥 |
