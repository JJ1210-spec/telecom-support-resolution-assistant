"""ASGI entrypoint for the API and React app."""

from __future__ import annotations

from .api.app import create_app


def __getattr__(name: str):
    # Built lazily so importing the package (tests, CLI) never connects to external services.
    if name == "app":
        return create_app()
    raise AttributeError(name)
