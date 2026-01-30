"""
RDST API Module

Provides a FastAPI-based HTTP API for the RDST web client.
This is optional - the CLI works without the server.

Install with: pip install rdst[server]
Start with: rdst web
"""

from .app import create_app

__all__ = ["create_app"]
