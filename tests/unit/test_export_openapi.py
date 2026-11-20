"""The OpenAPI exporter and its drift check, against files in a temporary directory.

These tests never read the committed ``docs/api/openapi.json``. The smoke test
``tests/smoke/test_openapi_snapshot.py`` owns that comparison, so a stale snapshot fails one
named test instead of every test here.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.main import create_app
from scripts.export_openapi import FIX_COMMAND, main, render_schema

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_rendered_schema_is_the_app_schema_with_one_trailing_newline() -> None:
    rendered = render_schema()

    assert rendered.endswith("}\n")
    assert json.loads(rendered) == create_app().openapi()


def test_two_renders_are_byte_identical() -> None:
    """The check compares bytes, so the output must not depend on dict order or on the run."""

    assert render_schema() == render_schema()


def test_the_export_writes_the_rendered_schema(tmp_path: Path) -> None:
    target = tmp_path / "openapi.json"

    assert main(["--path", str(target)]) == 0
    assert target.read_text(encoding="utf-8") == render_schema()


def test_the_check_passes_when_the_file_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"
    target.write_text(render_schema(), encoding="utf-8")

    assert main(["--check", "--path", str(target)]) == 0
    assert FIX_COMMAND not in capsys.readouterr().out


def test_the_check_fails_with_a_diff_when_the_file_drifts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"
    target.write_text(render_schema().replace('"3.1.0"', '"3.0.0"'), encoding="utf-8")

    assert main(["--check", "--path", str(target)]) == 1

    out = capsys.readouterr().out
    assert '-  "openapi": "3.0.0",' in out
    assert '+  "openapi": "3.1.0",' in out
    assert FIX_COMMAND in out


def test_the_check_fails_when_the_file_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "openapi.json"

    assert main(["--check", "--path", str(target)]) == 1
    assert FIX_COMMAND in capsys.readouterr().out
    assert not target.exists()


def test_the_fix_command_is_the_documented_command() -> None:
    assert FIX_COMMAND == "python scripts/export_openapi.py"


def test_the_script_runs_as_a_command_from_the_repository_root() -> None:
    """The docs and CI run ``python scripts/export_openapi.py``.

    Run that way, ``sys.path[0]`` is ``scripts/``, not the repository root, so ``import app``
    fails unless the script fixes the path. ``pytest`` hides the problem because
    ``pythonpath = ["."]`` puts the root on the path for it.
    """

    result = subprocess.run(
        [sys.executable, "scripts/export_openapi.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "--check" in result.stdout
