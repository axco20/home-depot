"""Vercel serverless entrypoint for the StockPath FastAPI application."""

from backend.main import app

__all__ = ["app"]
