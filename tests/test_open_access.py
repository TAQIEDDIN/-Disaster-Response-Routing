import unittest

from backend.app import app, _public_report


class OpenAccessTests(unittest.TestCase):
    def test_operational_api_endpoints_do_not_require_login(self):
        protected_paths = {
            "/api/otonom-analiz", "/api/goruntu-analiz", "/api/drone-analiz",
            "/api/analyses", "/api/dashboard", "/api/reports",
            "/api/reports/{report_id}/status",
        }
        routes = {route.path: route for route in app.routes}
        for path in protected_paths:
            with self.subTest(path=path):
                self.assertIn(path, routes)
                self.assertEqual(routes[path].dependant.dependencies, [])
        self.assertFalse(any(route.path.startswith("/api/auth/") for route in app.routes))

    def test_public_report_response_removes_personal_contact_fields(self):
        report = {
            "id": "example", "reporter_name": "Private Name",
            "contact": "555-0100", "health_status": "injured",
            "type": "building_damage",
        }
        public_report = _public_report(report)
        self.assertEqual(public_report, {"id": "example", "type": "building_damage"})
        self.assertIn("contact", report)  # Sanitizing a response does not mutate stored data.


if __name__ == "__main__":
    unittest.main()
