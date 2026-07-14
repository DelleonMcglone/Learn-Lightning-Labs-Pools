"""Local web UI: recommendation views + grounded chat (`advisor serve`)."""

from .server import DataProvider, create_app, serve

__all__ = ["DataProvider", "create_app", "serve"]
