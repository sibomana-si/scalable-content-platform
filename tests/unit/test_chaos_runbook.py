"""The chaos runbook, made executable.

`tests/unit/test_load_runbook.py` applied to the fault-injection procedure. A runbook is typed,
not read, so every command in it must resolve on a cold machine: the scripts exist and run, the
compose profiles are declared, the injector targets and faults are real, and every environment
variable a command sets appears in the parameter reference.

The M6 rehearsal found four defects a passing test suite had not: the checks below prove the
commands resolve, never that they are sufficient. Walk the document from a stopped stack too.
"""

import os
import re
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from scripts.inject_fault import FAULTS, TARGETS

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "resilience" / "chaos-test-runbook.md"
COMPOSE = REPO_ROOT / "docker-compose.yml"
LOAD_DIR = REPO_ROOT / "tests" / "load"

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
    text = RUNBOOK.read_text()
    section = re.search(r"\n##\s+Parameter reference\n(.*?)(?=\n##\s|\Z)", text, re.DOTALL)
    assert section, "the runbook has no 'Parameter reference' section"
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`", section.group(1), re.MULTILINE))


@pytest.fixture(autouse=True)
def _the_runbook_exists() -> None:
    assert RUNBOOK.is_file(), f"{RUNBOOK.relative_to(REPO_ROOT)} does not exist"


# --- the document is a runbook ------------------------------------------------------------------


def test_the_runbook_carries_commands_to_check() -> None:
    assert len(command_lines()) >= 20


def test_the_runbook_covers_every_section_a_reader_needs() -> None:
    headings = re.findall(r"^##\s+(.*)$", RUNBOOK.read_text(), re.MULTILINE)

    expected = [
        "Before you start",
        "Running the experiment set",
        "Injecting one fault by hand",
        "Collecting the results",
        "Verifying the run is citable",
        "Teardown and restoring the machine",
        "When it goes wrong",
        "Parameter reference",
        "Change control",
    ]
    assert headings == expected


# --- every command resolves ---------------------------------------------------------------------


def test_every_script_the_runbook_names_exists() -> None:
    named = {
        path
        for line in command_lines()
        for path in re.findall(r"(?<![\w/])(scripts/[\w./-]+)", line)
    }

    assert named, "the runbook names no repository script"
    for path in sorted(named):
        assert (REPO_ROOT / path).is_file(), f"the runbook calls {path}, which does not exist"


def test_every_script_the_runbook_calls_directly_is_executable() -> None:
    """A documented `scripts/x.py` that needs `python scripts/x.py` is a defect in the runbook."""

    named = {
        path
        for line in command_lines()
        if line.startswith("scripts/")
        for path in re.findall(r"^(scripts/[\w./-]+)", line)
    }

    assert named, "the runbook calls no script directly"
    for path in sorted(named):
        assert os.access(REPO_ROOT / path, os.X_OK), f"{path} is not executable"


def test_every_compose_profile_the_runbook_uses_is_declared() -> None:
    used = {
        profile for line in command_lines() for profile in re.findall(r"--profile\s+(\S+)", line)
    }
    declared = declared_profiles()

    assert "chaos" in used, "a chaos runbook that never starts the chaos profile injects nothing"
    assert used <= declared, (
        f"undeclared profile(s) {sorted(used - declared)}, have {sorted(declared)}"
    )


def test_every_injector_target_the_runbook_names_is_real() -> None:
    used = {target for line in command_lines() for target in re.findall(r"--target\s+(\S+)", line)}

    assert used, "the runbook injects nothing"
    assert used <= set(TARGETS), f"unknown target(s) {sorted(used - set(TARGETS))}"


def test_every_fault_the_runbook_names_is_real() -> None:
    used = set()  # type: ignore[var-annotated]
    for line in command_lines():
        if "inject_fault.py" not in line:
            continue
        used.update(word for word in line.split() if word in FAULTS)

    assert used, "the runbook names no fault"
    assert used <= set(FAULTS)


def test_the_runbook_exercises_every_fault_the_injector_offers() -> None:
    """A fault nobody documents is a capability nobody reaches for during an incident."""

    used = set()  # type: ignore[var-annotated]
    for line in command_lines():
        used.update(word for word in line.split() if word in FAULTS)

    assert set(FAULTS) <= used, f"never shown in the runbook: {sorted(set(FAULTS) - used)}"


def test_every_k6_script_argument_is_a_real_file() -> None:
    used = {arg for line in command_lines() for arg in re.findall(r"(/scripts/[\w./-]+)", line)}

    assert used, "the runbook runs no k6 script, so nothing measures the fault"
    for arg in sorted(used):
        target = LOAD_DIR / arg[len("/scripts/") :]
        assert target.is_file(), f"the runbook runs {arg}, which is not a file under tests/load/"


def test_every_environment_variable_a_command_sets_is_documented() -> None:
    used = set()
    for line in command_lines():
        used.update(re.findall(r"(?:^|\s)([A-Z][A-Z0-9_]{2,})=", line))
        used.update(re.findall(r"--env\s+([A-Z][A-Z0-9_]{2,})=", line))
    used -= AMBIENT_VARIABLES

    undocumented = used - documented_parameters()
    assert not undocumented, f"used but not in the parameter reference: {sorted(undocumented)}"


def test_the_parameter_reference_documents_nothing_that_does_not_exist() -> None:
    sources = "\n".join(
        path.read_text()
        for path in [
            REPO_ROOT / "scripts" / "inject_fault.py",
            REPO_ROOT / "scripts" / "perf_env.sh",
            REPO_ROOT / "scripts" / "run_load_matrix.sh",
            REPO_ROOT / "tests" / "conftest.py",
            REPO_ROOT / ".env.example",
            COMPOSE,
            LOAD_DIR / "lib" / "api.js",
            LOAD_DIR / "lib" / "summary.js",
            *sorted((LOAD_DIR / "scenarios").glob("*.js")),
        ]
    )

    for name in sorted(documented_parameters()):
        assert name in sources, f"the parameter reference documents {name}, which nothing reads"


def test_the_injector_cpuset_is_documented() -> None:
    """A chaos number with no record of where the injector ran is not citable."""

    assert "TOXIPROXY_CPUSET" in documented_parameters()


# --- the traps this repository has already paid for ---------------------------------------------


def test_the_runbook_never_tells_anyone_to_bring_the_stack_down() -> None:
    offenders = [
        line
        for line in RUNBOOK.read_text().splitlines()
        if re.search(r"docker\s+compose\b.*\bdown\b", line) and not line.lstrip().startswith(">")
    ]

    for line in offenders:
        assert "never" in line.lower() or "not" in line.lower(), (
            f"the runbook contains an unqualified `docker compose down`: {line.strip()}"
        )


def test_the_teardown_clears_every_toxic() -> None:
    """A fault left on a proxy makes the next day's ordinary test run fail for no visible reason."""

    section = re.search(
        r"\n##\s+Teardown and restoring the machine\n(.*?)(?=\n##\s|\Z)",
        RUNBOOK.read_text(),
        re.DOTALL,
    )
    assert section

    for target in TARGETS:
        assert f"--target {target} --clear" in section.group(1), (
            f"the teardown never clears the {target} proxy"
        )


def test_the_teardown_restores_the_power_profile() -> None:
    section = re.search(
        r"\n##\s+Teardown and restoring the machine\n(.*?)(?=\n##\s|\Z)",
        RUNBOOK.read_text(),
        re.DOTALL,
    )
    assert section

    assert "perf_env.sh unlock" in section.group(1)
    assert "balanced" in section.group(1)


def test_the_runbook_links_to_the_design_and_the_report() -> None:
    """The three resilience documents divide the work. A runbook that stands alone drifts."""

    text = RUNBOOK.read_text()

    assert "fault-tolerance-design.md" in text
    assert "chaos-test-report.md" in text
