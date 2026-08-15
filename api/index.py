"""Vercel serverless entrypoint for the StockPath FastAPI application."""

import sys
from pathlib import Path


project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from backend.main import app

__all__ = ["app"]
