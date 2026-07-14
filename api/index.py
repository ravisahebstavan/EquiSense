"""Vercel serverless entrypoint — exposes the FastAPI ASGI app.

Vercel's Python runtime detects `app` and serves every route (vercel.json
rewrites all paths here, including /static/* which FastAPI serves itself).
"""
from equisense.api.app import app  # noqa: F401
