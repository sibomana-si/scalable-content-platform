"""Consistency checks on the committed Grafana dashboards and Prometheus rules.

Dashboards and alerts are configuration, so they are verified rather than TDD'd in the usual
red-green sense — but the one failure mode that matters is silent: a panel or an alert that
references a metric the app does not export renders "No data" forever and an alert that can
never fire is worse than no alert. These tests close that drift by checking every PromQL
expression in the repo against the metric names the application actually registers.
"""

import json
import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]
from prometheus_client import REGISTRY

import app.observability.metrics  # noqa: F401(registers the collectors on import)

REPO_ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_DIR = REPO_ROOT / "grafana" / "dashboards"
ALERT_RULES = REPO_ROOT / "prometheus" / "alerts.yml"
PROMETHEUS_CONFIG = REPO_ROOT / "prometheus" / "prometheus.yml"
RUNBOOKS = REPO_ROOT / "docs" / "observability" / "alerting-runbooks.md"

# Series Prometheus produces about itself and its targets, which the app cannot export.
PROMETHEUS_META_METRICS = {"up", "scrape_duration_seconds", "scrape_samples_scraped"}

# PromQL functions, aggregation keywords and operators — identifiers that are not metrics.
PROMQL_RESERVED = {
    "abs",
    "absent",
    "absent_over_time",
    "and",
    "avg",
    "avg_over_time",
    "bool",
    "bottomk",
    "by",
    "ceil",
    "changes",
    "clamp",
    "clamp_max",
    "clamp_min",
    "count",
    "count_over_time",
    "count_values",
    "delta",
    "deriv",
    "floor",
    "group_left",
    "group_right",
    "histogram_quantile",
    "holt_winters",
    "idelta",
    "ignoring",
    "increase",
    "irate",
    "label_join",
    "label_replace",
    "last_over_time",
    "max",
    "max_over_time",
    "min",
    "min_over_time",
    "offset",
    "on",
    "or",
    "predict_linear",
    "quantile",
    "quantile_over_time",
    "rate",
    "resets",
    "round",
    "sort",
    "sort_desc",
    "stddev",
    "stddev_over_time",
    "stdvar",
    "sum",
    "sum_over_time",
    "time",
    "timestamp",
    "topk",
    "unless",
    "vector",
    "scalar",
    "without",
    "le",
    "inf",
    "nan",
}

_STRINGS = re.compile(r"""(['"])(?:\\.|(?!\1).)*\1""")
_LABEL_BLOCKS = re.compile(r"\{[^}]*\}")
_GROUPING = re.compile(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)")
_RANGES = re.compile(r"\[[^\]]*\]")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def metric_names_in(expression: str) -> set[str]:
    """The metric names an expression selects, with labels, ranges and functions stripped."""
    cleaned = _STRINGS.sub(" ", expression)
    cleaned = _GROUPING.sub(" ", cleaned)
    cleaned = _LABEL_BLOCKS.sub(" ", cleaned)
    cleaned = _RANGES.sub(" ", cleaned)
    return {name for name in _IDENTIFIER.findall(cleaned) if name not in PROMQL_RESERVED}


def exported_metric_names() -> set[str]:
    """Every series name a scrape of this app can yield."""
    names: set[str] = set()
    for metric in REGISTRY.collect():
        names.add(metric.name)
        if metric.type == "counter":
            names.add(f"{metric.name}_total")
        elif metric.type in ("histogram", "summary"):
            names |= {f"{metric.name}_{s}" for s in ("bucket", "count", "sum")}
        names |= {sample.name for sample in metric.samples}
    return names | PROMETHEUS_META_METRICS


def dashboards() -> list[tuple[str, dict]]:
    return sorted(
        (path.name, json.loads(path.read_text())) for path in DASHBOARD_DIR.glob("*.json")
    )


def panel_expressions(dashboard: dict) -> list[tuple[str, str]]:
    """``(panel title, expr)`` for every query in the dashboard, including inside rows."""
    found = []
    stack = list(dashboard.get("panels", []))
    while stack:
        panel = stack.pop()
        stack.extend(panel.get("panels", []))
        for target in panel.get("targets", []):
            if expr := target.get("expr"):
                found.append((panel.get("title", "<untitled>"), expr))
    return found


def alert_rules() -> list[dict]:
    document = yaml.safe_load(ALERT_RULES.read_text())
    return [rule for group in document["groups"] for rule in group["rules"]]


def runbook_anchors() -> set[str]:
    """Anchors mkdocs/GitHub generate for the headings in the runbook document."""
    anchors = set()
    for line in RUNBOOKS.read_text().splitlines():
        if match := re.match(r"^#{2,6}\s+(.*)$", line):
            slug = re.sub(r"[^a-z0-9\s-]", "", match.group(1).lower())
            anchors.add(re.sub(r"\s+", "-", slug.strip()))
    return anchors


# --- the files exist and parse ---------------------------------------------------------------


def test_the_dashboards_named_in_the_catalog_are_all_committed() -> None:
    assert {name for name, _ in dashboards()} == {
        "api-overview.json",
        "cache.json",
        "database.json",
        "resilience.json",
    }


def test_prometheus_config_parses_and_loads_the_rule_file() -> None:
    config = yaml.safe_load(PROMETHEUS_CONFIG.read_text())

    assert "alerts.yml" in " ".join(config["rule_files"])
    assert any(job["job_name"] for job in config["scrape_configs"])


def test_prometheus_scrapes_the_apps_metrics_path() -> None:
    config = yaml.safe_load(PROMETHEUS_CONFIG.read_text())
    app_job = next(j for j in config["scrape_configs"] if j["job_name"] == "content-platform")

    # The default is /metrics; being explicit keeps this honest if the path ever moves.
    assert app_job.get("metrics_path", "/metrics") == "/metrics"


# --- dashboards ------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "dashboard"), dashboards())
def test_every_dashboard_panel_queries_a_metric_the_app_exports(name: str, dashboard: dict) -> None:
    exported = exported_metric_names()

    for title, expr in panel_expressions(dashboard):
        unknown = metric_names_in(expr) - exported
        assert not unknown, f"{name} panel {title!r} queries unknown metric(s) {sorted(unknown)}"


def test_dashboard_uids_and_titles_are_unique() -> None:
    uids = [d["uid"] for _, d in dashboards()]
    titles = [d["title"] for _, d in dashboards()]

    # A duplicate uid silently overwrites the other dashboard when provisioned.
    assert len(set(uids)) == len(uids)
    assert len(set(titles)) == len(titles)


@pytest.mark.parametrize(("name", "dashboard"), dashboards())
def test_every_dashboard_is_provisionable(name: str, dashboard: dict) -> None:
    assert dashboard["uid"]
    assert dashboard["title"]
    assert isinstance(dashboard["panels"], list)
    assert dashboard.get("schemaVersion")


def test_the_api_overview_dashboard_covers_rate_errors_and_duration() -> None:
    dashboard = dict(dashboards())["api-overview.json"]
    expressions = " ".join(expr for _, expr in panel_expressions(dashboard))

    # RED, in full: without all three the dashboard does not answer "is the service healthy".
    assert "rate(http_requests_total" in expressions
    assert 'status=~"5.."' in expressions
    assert "histogram_quantile" in expressions
    for quantile in ("0.5", "0.95", "0.99"):
        assert quantile in expressions


def test_the_pending_dashboards_declare_why_they_are_empty() -> None:
    for name in ("cache.json", "resilience.json"):
        dashboard = dict(dashboards())[name]
        text = json.dumps(dashboard).lower()

        assert panel_expressions(dashboard) == []
        assert "pending" in text


# --- alert rules -----------------------------------------------------------------------------


def test_every_alert_expression_queries_a_metric_the_app_exports() -> None:
    exported = exported_metric_names()

    for rule in alert_rules():
        unknown = metric_names_in(rule["expr"]) - exported
        assert not unknown, f"alert {rule['alert']!r} queries unknown metric(s) {sorted(unknown)}"


def test_every_alert_waits_before_firing() -> None:
    for rule in alert_rules():
        # No `for:` means a single scrape blip pages someone.
        assert rule.get("for"), f"alert {rule['alert']!r} has no `for:` duration"


def test_every_alert_carries_a_severity() -> None:
    for rule in alert_rules():
        assert rule["labels"]["severity"] in {"critical", "warning", "info"}


def test_every_alert_links_to_a_runbook_section_that_exists() -> None:
    anchors = runbook_anchors()

    for rule in alert_rules():
        url = rule["annotations"]["runbook_url"]
        anchor = url.partition("#")[2]
        assert anchor, f"alert {rule['alert']!r} has a runbook_url with no anchor"
        assert anchor in anchors, (
            f"alert {rule['alert']!r} points at #{anchor}, which is not a heading in "
            f"alerting-runbooks.md (have: {sorted(anchors)})"
        )


def test_every_alert_says_what_is_wrong() -> None:
    for rule in alert_rules():
        assert rule["annotations"]["summary"]
        assert rule["annotations"]["description"]


def test_alert_names_are_unique() -> None:
    names = [rule["alert"] for rule in alert_rules()]

    assert len(set(names)) == len(names)


def test_the_error_budget_is_alerted_on_two_burn_rate_windows() -> None:
    # Multi-window burn-rate alerting: a fast window catches an outage
    # in minutes, a slow one catches the steady drip that would exhaust the budget anyway.
    burn_alerts = [r for r in alert_rules() if "burn" in r["alert"].lower()]

    assert len(burn_alerts) >= 2
    windows = {re.search(r"\[(\w+)\]", rule["expr"]).group(1) for rule in burn_alerts}  # type: ignore[union-attr]
    assert len(windows) >= 2


def test_the_documented_runbooks_all_have_a_rule() -> None:
    alerted = {rule["alert"] for rule in alert_rules()}

    assert {"HighReadLatencyP95", "ElevatedErrorRate", "TargetDown"} <= alerted
