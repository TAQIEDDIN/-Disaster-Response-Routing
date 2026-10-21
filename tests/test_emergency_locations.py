import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from backend import emergency_store


class EmergencyLocationTests(unittest.TestCase):
    def test_maps_osm_categories_to_supported_location_types(self):
        self.assertEqual(emergency_store.classify_osm_feature({"amenity": "hospital"}), "hospital")
        self.assertEqual(emergency_store.classify_osm_feature({"emergency": "assembly_point"}), "assembly_point")
        self.assertEqual(emergency_store.classify_osm_feature({"amenity": "cafe"}), None)

    def test_cached_locations_are_returned_without_claiming_live_status(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "locations.sqlite3")
            with patch.object(emergency_store, "DATABASE_PATH", database):
                fetched_at = datetime.now(timezone.utc).isoformat()
                connection = emergency_store._connect()
                try:
                    connection.execute(
                        "INSERT INTO emergency_locations VALUES (?, ?, ?, ?, ?, ?)",
                        ("node/1", "hospital", "Test Hospital", 36.2, 36.16, fetched_at),
                    )
                    connection.execute(
                        "INSERT INTO emergency_cache_meta VALUES (1, ?)", (fetched_at,)
                    )
                    connection.commit()
                finally:
                    connection.close()
                result = emergency_store.get_locations(None)
                self.assertEqual(result["locations"][0]["name"], "Test Hospital")
                self.assertFalse(result["stale"])
                self.assertEqual(result["source"], "OpenStreetMap")

    def test_missing_graph_and_cache_returns_empty_with_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(emergency_store, "DATABASE_PATH", os.path.join(directory, "empty.sqlite3")):
                result = emergency_store.get_locations(None, force_refresh=True)
        self.assertEqual(result["locations"], [])
        self.assertTrue(result["stale"])
        self.assertIsNotNone(result["warning"])


if __name__ == "__main__":
    unittest.main()
