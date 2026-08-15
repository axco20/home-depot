from __future__ import annotations

import unittest

from backend.clients import StoreDirectoryClient


class StoreDirectoryTests(unittest.TestCase):
    def test_store_number_is_read_from_ref_or_website(self) -> None:
        self.assertEqual(StoreDirectoryClient._store_id({"ref": "Falls Church #4608"}), "4608")
        self.assertEqual(
            StoreDirectoryClient._store_id(
                {"website": "https://www.homedepot.com/l/Frederick/MD/Frederick/21704/2559"}
            ),
            "2559",
        )

    def test_short_store_number_is_zero_padded(self) -> None:
        self.assertEqual(StoreDirectoryClient._store_id({"ref": "68"}), "0068")

    def test_distance_uses_radius_miles(self) -> None:
        distance = StoreDirectoryClient._distance_miles((39.0, -77.0), (39.1, -77.0))
        self.assertGreater(distance, 6)
        self.assertLess(distance, 8)


if __name__ == "__main__":
    unittest.main()
