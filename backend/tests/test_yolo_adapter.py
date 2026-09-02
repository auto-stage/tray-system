from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from adapters.yolo_part_inspection_adapter import YoloModelError, YoloPartInspectionAdapter
from parts_db import load_parts_catalog


class Array:
    def __init__(self, values): self.values = values
    def cpu(self): return self
    def tolist(self): return self.values


class Boxes:
    xyxy = Array([[10, 20, 80, 90], [100, 10, 150, 60]])
    conf = Array([.91, .12])
    cls = Array([1, 0])


class Result:
    boxes = Boxes()


class FakeModel:
    names = {0: "flange_nut", 1: "t_bolt", 2: "socket_head_bolt", 3: "corner_bracket", 4: "t_nut", 5: "l_bracket"}
    def predict(self, **kwargs): return [Result()]


class YoloAdapterTest(unittest.TestCase):
    def setUp(self):
        self.parts = load_parts_catalog(BACKEND_DIR / "config" / "parts.yaml")
        self.adapter = YoloPartInspectionAdapter(frame_source=lambda copy=True: np.zeros((100, 200, 3), dtype=np.uint8), parts=self.parts, model_loader=lambda path: FakeModel())

    def tearDown(self): self.adapter.close()

    def test_model_not_ready_has_no_fake_detection(self):
        result = self.adapter.latest_result()
        self.assertEqual(result["error"], "YOLO_MODEL_NOT_READY")
        self.assertEqual(result["detections"], [])

    def test_model_mapping_threshold_and_count(self):
        with tempfile.NamedTemporaryFile(suffix=".pt") as weight:
            self.adapter.load_model(weight.name)
            result = self.adapter.infer_frame(np.zeros((100, 200, 3), dtype=np.uint8))
        self.assertEqual(result["counts"]["t_bolt"], 1)
        self.assertEqual(result["counts"]["flange_nut"], 0)
        self.assertEqual(len(result["detections"]), 1)

    def test_mapping_mismatch_is_blocked(self):
        class Wrong(FakeModel): names = {0: "wrong"}
        adapter = YoloPartInspectionAdapter(frame_source=lambda: None, parts=self.parts, model_loader=lambda path: Wrong())
        try:
            with tempfile.NamedTemporaryFile(suffix=".pt") as weight:
                with self.assertRaises(YoloModelError): adapter.validate_model(weight.name)
        finally: adapter.close()


if __name__ == "__main__":
    unittest.main()
