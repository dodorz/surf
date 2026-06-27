"""WSGI entry point for production deployment behind nginx.

Usage:
    gunicorn wsgi:app
"""
import os

os.environ["SURF_ROOT_PATH"] = os.environ.get("SURF_ROOT_PATH", "/surf")

# Import after env var is set — _ROOT_PATH and _apply_root_path() run at import time
from surf_web import app  # noqa: E402, F401
