"""Vercel serverless entrypoint for the StockPath FastAPI application."""

import json
import sys
from pathlib import Path
from typing import Any


class StartupDiagnosticApp:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        body = json.dumps(
            {
                "detail": "API failed to start",
                "errorType": type(self.error).__name__,
                "message": str(self.error),
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})


project_root = str(Path(__file__).resolve().parents[1])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

app = StartupDiagnosticApp(RuntimeError("Backend import did not run"))

try:
    from backend.main import app as backend_app
except BaseException as exc:  # pragma: no cover - only exercised by the deployment runtime
    app = StartupDiagnosticApp(exc)
else:
    app = backend_app

__all__ = ["app"]
