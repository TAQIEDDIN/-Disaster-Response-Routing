import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from backend import report_store


class IncidentReportStoreTests(unittest.TestCase):
    def test_report_starts_reported_and_moves_one_status_at_a_time(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "reports.sqlite3")
            with patch.object(report_store, "DATABASE_PATH", database):
                report = report_store.create_report(
                    report_type="blocked_road", latitude=36.2, longitude=36.16,
                    description="Road blocked by debris", severity="high",
                )
                self.assertEqual(report["status"], "Reported")
                self.assertEqual(report_store.get_report(report["id"])["type"], "blocked_road")
                with self.assertRaises(ValueError):
                    report_store.update_report_status(report["id"], "Verified")
                self.assertEqual(report_store.update_report_status(report["id"], "Under Review")["status"], "Under Review")
                self.assertEqual(report_store.update_report_status(report["id"], "Verified")["status"], "Verified")
                self.assertEqual(report_store.update_report_status(report["id"], "Resolved")["status"], "Resolved")
                self.assertEqual(report_store.list_reports(status="Resolved")[0]["id"], report["id"])

    def test_optional_report_image_is_saved_under_generated_id(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(report_store, "INCIDENT_IMAGE_DIR", directory):
                image_path = report_store.save_report_image(
                    np.zeros((8, 9, 3), dtype=np.uint8), "generated-id"
                )
                self.assertTrue(os.path.isfile(image_path))
                self.assertEqual(os.path.basename(image_path), "generated-id.jpg")

    def test_dashboard_metrics_use_persisted_incident_filters(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(report_store, "DATABASE_PATH", os.path.join(directory, "reports.sqlite3")):
                blocked = report_store.create_report(
                    report_type="blocked_road", latitude=36.2, longitude=36.16,
                    description="Blocked", severity="high",
                )
                report_store.update_report_status(blocked["id"], "Under Review")
                report_store.update_report_status(blocked["id"], "Verified")
                report_store.create_report(
                    report_type="fire", latitude=36.21, longitude=36.17,
                    description="Fire", severity="critical",
                )
                stats = report_store.report_metrics(severity="critical")
                self.assertEqual(stats["total_reports"], 1)
                self.assertEqual(stats["active_incidents"], 1)
                self.assertEqual(stats["verified_blocked_roads"], 0)


if __name__ == "__main__":
    unittest.main()
