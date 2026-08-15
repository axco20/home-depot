from __future__ import annotations

import unittest

from api.index import app as vercel_app
from backend.main import app as backend_app


class VercelEntrypointTests(unittest.TestCase):
    def test_entrypoint_exports_backend_application(self) -> None:
        self.assertIs(vercel_app, backend_app)
        self.assertTrue(
            any(route.path == "/api/health" for route in vercel_app.routes),
        )


if __name__ == "__main__":
    unittest.main()
