# Chaos / Failure-Injection Report
### Validates [fault-tolerance-design.md](fault-tolerance-design.md).

> **Status:** 🟥 Not started — template only; results pending the Week-7 (M7) fault-injection tests · **Owner:** Simon Sibomana

## Method
_How failures were injected (e.g. toolkit, `tc` for latency, stopping containers, fault proxies)._

## Experiments
| # | Injected failure | Hypothesis | Observed behavior | Pass? |
|---|---|---|---|---|
| 1 | DB latency spike (+500ms) | Timeouts trip, retries bounded | _..._ | 🟥 |
| 2 | Redis unavailable | Fall back to DB, elevated latency | _..._ | 🟥 |
| 3 | Downstream dependency failure | Circuit breaker opens, fast-fail | _..._ | 🟥 |
| 4 | DB unavailable | Cached reads served; writes rejected cleanly | _..._ | 🟥 |

## Evidence
_Dashboards/metrics screenshots showing degradation and recovery._

## Findings & Improvements
- _What broke vs. expectation; tuning changes made; re-test results._

## Conclusion
_System behavior vs. "graceful degradation" NFR._
