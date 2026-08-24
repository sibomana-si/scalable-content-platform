"""The load-test runbook, made executable.

A runbook is the one document that gets typed rather than read. Every command in it must resolve
on a cold machine, so this parses the fenced `bash` blocks and checks each one against the repo:
the scripts exist and run, the compose profiles are declared, the k6 arguments are real files,
and every environment variable a command sets is documented in the parameter reference.

The `test_capacity_model.py` precedent, applied to a procedure: a command that cannot run fails
the build, not the reader.
"""

import os
import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "performance" / "load-test-runbook.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"
LOAD_DIR = REPO_ROOT / "tests" / "load"

# Set by the shell or by compose itself, not by this repo's scripts.
AMBIENT_VARIABLES = {"PATH", "HOME", "PWD", "UID", "GID"}


def bash_blocks() -> list[str]:
    return re.findall(r"^```bash\n(.*?)^```", RUNBOOK.read_text(), re.MULTILINE | re.DOTALL)


def command_lines() -> list[str]:
    lines = []
    for block in bash_blocks():
        for line in block.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                lines.append(line)
    return lines


def declared_profiles() -> set[str]:
    compose = yaml.safe_load(COMPOSE.read_text())
    return {
        profile
        for service in compose["services"].values()
        for profile in service.get("profiles", [])
    }


def documented_parameters() -> set[str]:
    """The `NAME` cells in the parameter reference table."""

    text = RUNBOOK.read_text()
    section = re.search(r"\n##\s+Parameter reference\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    assert section, "the runbook has no 'Parameter reference' section"
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`", section.group(1), re.MULTILINE))


@pytest.fixture(autouse=True)
def _the_runbook_exists() -> None:
    assert RUNBOOK.is_file(), f"{RUNBOOK.relative_to(REPO_ROOT)} does not exist"


# --- the document is a runbook ----------------------------------------------------------------


def test_the_runbook_carries_commands_to_check() -> None:
    """A runbook with no commands is a description, and this whole file would pass vacuously."""

    assert len(command_lines()) >= 20


def test_the_runbook_covers_every_section_a_reader_needs() -> None:
    headings = re.findall(r"^##\s+(.*)$", RUNBOOK.read_text(), re.MULTILINE)

    expected = [
        "Before you start",
        "Replicating the full matrix",
        "Running one scenario by hand",
        "Collecting the results",
        "Verifying the run is citable",
        "Teardown and restoring the machine",
        "When it goes wrong",
        "Parameter reference",
        "Change control",
    ]
    assert headings == expected


# --- every command resolves -------------------------------------------------------------------


def test_every_script_the_runbook_names_exists() -> None:
    named = {
        path
        for line in command_lines()
        for path in re.findall(r"(?<![\w/])(scripts/[\w./-]+)", line)
    }

    assert named, "the runbook names no repository script"
    for path in sorted(named):
        assert (REPO_ROOT / path).is_file(), f"the runbook calls {path}, which does not exist"


def test_every_shell_script_the_runbook_names_is_executable() -> None:
    """A documented `scripts/x.sh` that needs `bash scripts/x.sh` is a defect in the runbook."""

    named = {
        path
        for line in command_lines()
        for path in re.findall(r"(?<![\w/])(scripts/[\w./-]+\.sh)", line)
    }

    for path in sorted(named):
        assert os.access(REPO_ROOT / path, os.X_OK), f"{path} is not executable"


def test_every_compose_profile_the_runbook_uses_is_declared() -> None:
    used = {
        profile for line in command_lines() for profile in re.findall(r"--profile\s+(\S+)", line)
    }
    declared = declared_profiles()

    assert used, "the runbook uses no compose profile"
    assert used <= declared, (
        f"undeclared profile(s) {sorted(used - declared)}, have {sorted(declared)}"
    )


def test_every_k6_script_argument_is_a_real_file() -> None:
    """`/scripts` is the container path for `tests/load`. A typo there fails 90 seconds in."""

    used = {arg for line in command_lines() for arg in re.findall(r"(/scripts/[\w./-]+)", line)}

    assert used, "the runbook runs no k6 script"
    for arg in sorted(used):
        target = LOAD_DIR / arg[len("/scripts/") :]
        assert target.is_file(), f"the runbook runs {arg}, which is not a file under tests/load/"


def test_every_environment_variable_a_command_sets_is_documented() -> None:
    """Section 8 is the reader's only list of the knobs. A variable missing from it is invisible."""

    used = set()
    for line in command_lines():
        used.update(re.findall(r"(?:^|\s)([A-Z][A-Z0-9_]{2,})=", line))
        used.update(re.findall(r"--env\s+([A-Z][A-Z0-9_]{2,})=", line))
    used -= AMBIENT_VARIABLES

    undocumented = used - documented_parameters()
    assert not undocumented, f"used but not in the parameter reference: {sorted(undocumented)}"


def test_the_parameter_reference_documents_nothing_that_does_not_exist() -> None:
    """The reverse drift: a documented knob no script reads."""

    sources = "\n".join(
        path.read_text()
        for path in [
            REPO_ROOT / "scripts" / "run_load_matrix.sh",
            REPO_ROOT / "scripts" / "perf_env.sh",
            COMPOSE,
            LOAD_DIR / "lib" / "api.js",
            LOAD_DIR / "lib" / "summary.js",
            *sorted((LOAD_DIR / "scenarios").glob("*.js")),
        ]
    )

    for name in sorted(documented_parameters()):
        assert name in sources, f"the parameter reference documents {name}, which nothing reads"


# --- the traps this repository has already paid for ---------------------------------------------


def test_the_runbook_never_tells_anyone_to_bring_the_stack_down() -> None:
    """`docker compose down` removes the mysql container, which declares no named volume.

    The seeded dataset goes with it, and the recovery is a re-seed rather than a restart. This
    has happened once already, which is why it is a test and not a note.
    """

    text = RUNBOOK.read_text()
    offenders = [
        line
        for line in text.splitlines()
        if re.search(r"docker\s+compose\b.*\bdown\b", line) and not line.lstrip().startswith(">")
    ]

    for line in offenders:
        assert "never" in line.lower() or "not" in line.lower(), (
            f"the runbook contains an unqualified `docker compose down`: {line.strip()}"
        )


def test_the_teardown_restores_the_power_profile() -> None:
    text = RUNBOOK.read_text()
    section = re.search(
        r"\n##\s+Teardown and restoring the machine\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL
    )
    assert section

    assert "perf_env.sh unlock" in section.group(1)
    assert "balanced" in section.group(1)


def test_the_runbook_links_to_the_plan_and_the_report() -> None:
    """The three performance documents divide the work. A runbook that stands alone drifts."""

    text = RUNBOOK.read_text()

    assert "load-test-plan.md" in text
    assert "load-test-report.md" in text
