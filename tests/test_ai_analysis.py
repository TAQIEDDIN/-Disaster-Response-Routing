import os
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi import HTTPException

from backend import analysis_store
from backend.ai_engine import InferenceServiceError, _infer_single
from backend.upload_utils import validate_geo_bounds as _validate_geo_bounds
from backend.upload_utils import save_image_content


class AiAnalysisTests(unittest.TestCase):
    def test_rejects_invalid_or_reversed_geo_bounds(self):
        with self.assertRaises(HTTPException):
            _validate_geo_bounds(36, 36, 36.1, 36.1)
        with self.assertRaises(HTTPException):
            _validate_geo_bounds(91, 36, 35, 37)

    def test_validates_image_content_and_saves_decodable_upload(self):
        ok, encoded = cv2.imencode(".png", np.zeros((12, 15, 3), dtype=np.uint8))
        self.assertTrue(ok)

        path, width, height = save_image_content(encoded.tobytes())
        try:
            self.assertEqual((width, height), (15, 12))
            self.assertIsNotNone(cv2.imread(path))
        finally:
            os.unlink(path)

        with self.assertRaises(HTTPException) as invalid:
            save_image_content(b"not an image")
        self.assertEqual(invalid.exception.status_code, 400)

    def test_rejects_oversized_upload_before_decoding(self):
        with self.assertRaises(HTTPException) as oversized:
            save_image_content(b"1234", max_bytes=3)
        self.assertEqual(oversized.exception.status_code, 413)

    def test_inference_errors_are_not_reported_as_zero_detections(self):
        class BrokenClient:
            def configure(self, _configuration):
                return self

            def infer(self, *_args, **_kwargs):
                raise OSError("network unavailable")

        with patch("backend.ai_engine._get_client", return_value=BrokenClient()):
            with self.assertRaises(InferenceServiceError):
                _infer_single("unused.jpg")

    def test_analysis_results_are_persisted_and_retrievable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "analyses.sqlite3")
            with patch.object(analysis_store, "DATABASE_PATH", path):
                saved = analysis_store.save_analysis(
                    source="unit_test", filename="scene.png", image_width=20,
                    image_height=10, bounds={"nw_lat": 36, "nw_lon": 36,
                                              "se_lat": 35.9, "se_lon": 36.1},
                    detections=[{"confidence": 0.9, "class": "debris"}],
                )
                self.assertEqual(analysis_store.get_analysis(saved["analysis_id"]), saved)
                self.assertEqual(analysis_store.list_analyses(1), [saved])


if __name__ == "__main__":
    unittest.main()
