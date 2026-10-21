import unittest

from fastapi import HTTPException

from backend.app import _parse_routing_options


class RoutingApiOptionsTests(unittest.TestCase):
    def test_defaults_to_balanced_without_status_overrides(self):
        self.assertEqual(_parse_routing_options("", None), ("balanced", None))

    def test_parses_supported_mode_and_status_records(self):
        mode, statuses = _parse_routing_options(
            "FASTEST", '[{"osmid": 123, "status": "blocked"}]'
        )
        self.assertEqual(mode, "fastest")
        self.assertEqual(statuses, [{"osmid": 123, "status": "blocked"}])

    def test_rejects_invalid_mode_or_status_json(self):
        with self.assertRaises(HTTPException) as mode_error:
            _parse_routing_options("risky", None)
        self.assertEqual(mode_error.exception.status_code, 422)

        with self.assertRaises(HTTPException) as json_error:
            _parse_routing_options("balanced", "not-json")
        self.assertEqual(json_error.exception.status_code, 422)

    def test_rejects_unsupported_status(self):
        with self.assertRaises(HTTPException) as status_error:
            _parse_routing_options("balanced", '[{"osmid": 123, "status": "maybe"}]')
        self.assertEqual(status_error.exception.status_code, 422)

    def test_rejects_status_without_edge_identity(self):
        with self.assertRaises(HTTPException) as identity_error:
            _parse_routing_options("balanced", '[{"status": "blocked"}]')
        self.assertEqual(identity_error.exception.status_code, 422)


if __name__ == "__main__":
    unittest.main()
