"""The committed OpenAPI snapshot matches the schema the app serves.

``docs/api/openapi.json`` is the API contract under review. A route or schema change that does
not regenerate it fails here on a laptop, before the ``lint`` job fails the same check in CI.
"""

from scripts.export_openapi import DEFAULT_PATH, FIX_COMMAND, render_schema


def test_the_committed_snapshot_matches_the_app() -> None:
    assert DEFAULT_PATH.exists(), f"{DEFAULT_PATH} is missing. Run `{FIX_COMMAND}` and commit it."

    committed = DEFAULT_PATH.read_text(encoding="utf-8")
    assert committed == render_schema(), (
        "docs/api/openapi.json differs from the app schema. "
        f"Run `{FIX_COMMAND}`, review the diff, and commit it."
    )
