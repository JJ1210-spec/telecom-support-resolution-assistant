"""ASGI entrypoints: `uvicorn telecom_assistant.main:app` (core API + SPA, notification service mounted in
monolith mode) and `uvicorn telecom_assistant.main:notify_app` (notification service on its own)."""

from __future__ import annotations

from .api.app import create_app
from .notify.service import create_notify_app


def __getattr__(name: str):
    # Built lazily so importing the package (tests, CLI) never connects to external services.
    if name == "app":
        return create_app()
    if name == "notify_app":
        return create_notify_app()
    raise AttributeError(name)
