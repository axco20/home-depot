from __future__ import annotations

import json
import unittest
from pathlib import Path

from api.index import app as vercel_app
from backend.main import app as backend_app


class VercelEntrypointTests(unittest.TestCase):
    def test_entrypoint_exports_backend_application(self) -> None:
        self.assertIs(vercel_app, backend_app)
        self.assertTrue(
            any(route.path == "/api/health" for route in vercel_app.routes),
        )

    def test_vercel_rewrites_api_paths_to_fastapi_entrypoint(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        config = json.loads((project_root / "vercel.json").read_text())
        self.assertIn(
            {
                "source": "/api/(.*)",
                "destination": "/api",
            },
            config["rewrites"],
        )


if __name__ == "__main__":
    unittest.main()
