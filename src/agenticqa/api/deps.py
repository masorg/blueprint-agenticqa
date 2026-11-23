"""Shared dependencies: access to app singletons and API-key auth."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from ..config import Settings
from ..events.bus import EventBus
from ..orchestrator.engine import Engine
from ..store.db import RunRepository


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_repo(request: Request) -> RunRepository:
    return request.app.state.repo


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_github(request: Request):
    """The GitHubService (or None when GitHub isn't configured)."""
    return getattr(request.app.state, "github", None)


def require_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None),
) -> None:
    """Guards the trigger endpoint. When no key is configured, auth is disabled
    (dev/demo only) - callers should set AGENTICQA_APP_API_KEY in any real deploy."""
    if not settings.app_api_key:
        return
    if x_api_key != settings.app_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing X-API-Key"
        )
