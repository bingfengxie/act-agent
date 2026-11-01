from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    tenant: str = "default"
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class SessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionOut(BaseModel):
    id: str
    title: str
    status: str
    skill: str | None = None
    token_usage: int = 0
    created_at: datetime
    last_active_at: datetime

    model_config = {"from_attributes": True}


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)


class ResumeRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2_000)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["viewer"])
