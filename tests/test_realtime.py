import unittest

from backend.realtime import event_envelope


class RealtimeEventTests(unittest.TestCase):
    def test_event_envelope_contains_event_and_payload(self):
        message = event_envelope("incident_created", {"id": "report-1"})
        self.assertEqual(message["event"], "incident_created")
        self.assertEqual(message["data"]["id"], "report-1")
        self.assertTrue(message["created_at"])


if __name__ == "__main__":
    unittest.main()
