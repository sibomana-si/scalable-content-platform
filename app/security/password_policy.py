"""Password strength policy: length bounds + breached-password screening.

The breached-password check sits behind a small :class:`BreachedPasswordChecker` interface so
the default offline :class:`LocalBlocklistChecker` can be swapped for a live HIBP k-anonymity
checker (``HibpChecker``) without touching callers. Screening is applied at the schema boundary
(``app/schemas/auth.py``), so services receive already-valid passwords.
"""

from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

MIN_PASSWORD_LENGTH = 12
# The maximum bounds request size (a large-payload DoS control) and sits under Argon2's
# practical input limits.
MAX_PASSWORD_LENGTH = 128

_BLOCKLIST_PATH = Path(__file__).with_name("common_passwords.txt")


class PasswordPolicyError(ValueError):
    """A password failed the policy. The message is safe to surface to the caller.

    Subclasses ``ValueError`` so it is captured cleanly by Pydantic field validators.
    """


@runtime_checkable
class BreachedPasswordChecker(Protocol):
    """Strategy for deciding whether a password is known-breached."""

    def is_breached(self, password: str) -> bool: ...


@lru_cache(maxsize=1)
def _bundled_blocklist() -> frozenset[str]:
    lines = _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines()
    return frozenset(
        line.strip().lower() for line in lines if line.strip() and not line.startswith("#")
    )


class LocalBlocklistChecker:
    """Offline checker backed by a bundled list of common/known-breached passwords.

    Fully CI-safe (no network). ``HibpChecker`` (live k-anonymity range query) is the
    intended drop-in replacement behind :class:`BreachedPasswordChecker`.
    """

    def __init__(self, blocklist: set[str] | frozenset[str] | None = None) -> None:
        self._blocklist = (
            frozenset(p.lower() for p in blocklist)
            if blocklist is not None
            else _bundled_blocklist()
        )

    def is_breached(self, password: str) -> bool:
        return password.lower() in self._blocklist


_default_checker: BreachedPasswordChecker = LocalBlocklistChecker()


def screen_password(password: str, *, checker: BreachedPasswordChecker | None = None) -> str:
    """Return ``password`` if it satisfies the policy, else raise :class:`PasswordPolicyError`.

    Enforces the length bounds, then rejects known-breached passwords via ``checker``
    (defaulting to the bundled local blocklist).
    """

    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if (checker or _default_checker).is_breached(password):
        raise PasswordPolicyError(
            "This password appears in a known-breached-password list; choose a different one."
        )
    return password
