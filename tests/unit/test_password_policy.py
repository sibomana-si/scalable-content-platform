"""Unit tests for the password policy: length bounds + breached-list screening.

``screen_password`` enforces min/max length and rejects known-breached passwords via a
pluggable ``BreachedPasswordChecker`` (a local bundled blocklist by default; a live HIBP
checker is a documented seam).
"""

import pytest

from app.security.password_policy import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    LocalBlocklistChecker,
    PasswordPolicyError,
    screen_password,
)


def test_below_min_length_is_rejected():
    with pytest.raises(PasswordPolicyError):
        screen_password("a" * (MIN_PASSWORD_LENGTH - 1))


def test_exactly_min_length_is_allowed():
    pw = "aB3" + "x" * (MIN_PASSWORD_LENGTH - 3)  # 12 chars, not breached
    assert screen_password(pw) == pw


def test_above_max_length_is_rejected():
    with pytest.raises(PasswordPolicyError):
        screen_password("a" * (MAX_PASSWORD_LENGTH + 1))


def test_bundled_breached_password_is_rejected():
    # A common password long enough to clear the length gate must still be blocked.
    with pytest.raises(PasswordPolicyError):
        screen_password("password123456")


def test_strong_unlisted_password_is_allowed():
    pw = "Tr0ubadour-&-3xtra-Long-Phrase"
    assert screen_password(pw) == pw


def test_local_blocklist_is_case_insensitive():
    checker = LocalBlocklistChecker(blocklist={"password123456"})
    assert checker.is_breached("PASSWORD123456") is True
    assert checker.is_breached("a-totally-unique-passphrase") is False


def test_checker_seam_is_pluggable():
    class AlwaysBreached:
        def is_breached(self, password: str) -> bool:
            return True

    with pytest.raises(PasswordPolicyError):
        screen_password("a-sufficiently-long-password", checker=AlwaysBreached())
