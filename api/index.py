"""Vercel serverless entrypoint for the StockPath FastAPI application."""

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from backend.main import app
except Exception as exc:  # pragma: no cover - only exercised by the deployment runtime
    startup_error = {
        "errorType": type(exc).__name__,
        "message": str(exc),
    }
    app = FastAPI()

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def report_startup_error(path: str) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": "API failed to start", **startup_error},
        )

__all__ = ["app"]
