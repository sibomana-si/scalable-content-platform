"""Unit tests for JWT signing configuration.

The signing key is a secret: it is sourced only from the environment, defaults are
safe, an unset key fails loudly at signing time, and it never leaks through ``repr``.
Settings are constructed with ``_env_file=None`` so these tests are hermetic and do
not depend on a developer's local ``.env``.
"""

import pytest

from app.config import Settings


def test_jwt_algorithm_defaults_to_hs256() -> None:
    assert Settings(_env_file=None).jwt_algorithm == "HS256"  # type: ignore[call-arg]


def test_jwt_expire_defaults_to_900_seconds() -> None:
    assert Settings(_env_file=None).jwt_expire_seconds == 900  # type: ignore[call-arg]


def test_jwt_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "env-signing-key")
    monkeypatch.setenv("JWT_ALGORITHM", "HS512")
    monkeypatch.setenv("JWT_EXPIRE_SECONDS", "60")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.require_signing_key() == "env-signing-key"
    assert settings.jwt_algorithm == "HS512"
    assert settings.jwt_expire_seconds == 60


def test_require_signing_key_raises_when_secret_is_empty() -> None:
    # jwt_secret defaults to ""
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    with pytest.raises(RuntimeError):
        settings.require_signing_key()


def test_require_signing_key_returns_configured_secret() -> None:
    settings = Settings(_env_file=None, jwt_secret="configured-key")  # type: ignore[call-arg, arg-type]

    assert settings.require_signing_key() == "configured-key"


def test_repr_does_not_expose_secret_value() -> None:
    settings = Settings(_env_file=None, jwt_secret="top-secret-value")  # type: ignore[call-arg, arg-type]

    assert "top-secret-value" not in repr(settings)
    assert "top-secret-value" not in str(settings)
