"""Auth request/response schemas.

``RegisterIn`` enforces email format + normalization and the password policy (length +
breached-list screening) at the wire boundary, so services receive already-valid input.
``UserOut`` deliberately never carries ``password_hash``, credentials are never returned.
"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.security.password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, screen_password

# Matches the users.email column bound.
MAX_EMAIL_LENGTH = 254


class RegisterIn(BaseModel):
    email: EmailStr = Field(max_length=MAX_EMAIL_LENGTH)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower()

    @field_validator("password")
    @classmethod
    def _screen_password(cls, value: str) -> str:
        # PasswordPolicyError is a ValueError, so Pydantic surfaces it as a 422 field error.
        return screen_password(value)


class LoginIn(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower()


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: int
    email: str
    role: str
