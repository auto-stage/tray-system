from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import cv2
import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from adapters.opencv_part_inspection_adapter import OpenCVPartInspectionAdapter
from parts_db import load_parts_catalog


class MutableFrameSource:
    def __init__(self, frame=None) -> None:
        self.frame = frame

    def __call__(self, copy: bool = True):
        if self.frame is None:
            return None
        return self.frame.copy() if copy else self.frame


class OpenCVPartInspectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.source = MutableFrameSource()
        self.adapter = OpenCVPartInspectionAdapter(
            frame_source=self.source,
            parts=load_parts_catalog(BACKEND_DIR / "config" / "parts.yaml"),
            state_path=root / "profile.json",
            capture_root=root / "captures",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def background():
        return np.full((240, 320, 3), 230, dtype=np.uint8)

    def single_object(self):
        frame = self.background()
        cv2.rectangle(frame, (90, 80), (230, 160), (20, 20, 20), thickness=-1)
        return frame

    def synthetic_class_frames(self):
        frames = {}
        frame = self.background()
        cv2.circle(frame, (160, 120), 42, (10, 10, 10), thickness=-1)
        cv2.circle(frame, (160, 120), 16, (230, 230, 230), thickness=-1)
        frames["flange_nut"] = frame

        frame = self.background()
        cv2.rectangle(frame, (60, 108), (260, 132), (10, 10, 10), thickness=-1)
        frames["t_bolt"] = frame

        frame = self.background()
        cv2.rectangle(frame, (80, 95), (240, 145), (10, 10, 10), thickness=-1)
        frames["socket_head_bolt"] = frame

        frame = self.background()
        cv2.rectangle(frame, (80, 60), (115, 180), (10, 10, 10), thickness=-1)
        cv2.rectangle(frame, (80, 145), (220, 180), (10, 10, 10), thickness=-1)
        frames["corner_bracket"] = frame

        frame = self.background()
        cv2.rectangle(frame, (90, 75), (230, 165), (10, 10, 10), thickness=-1)
        frames["t_nut"] = frame

        frame = self.background()
        cv2.rectangle(frame, (105, 50), (130, 190), (10, 10, 10), thickness=-1)
        cv2.rectangle(frame, (105, 165), (195, 190), (10, 10, 10), thickness=-1)
        frames["l_bracket"] = frame
        return frames

    def test_real_frame_reference_workflow_and_unknown_gate(self) -> None:
        self.source.frame = self.background()
        background = self.adapter.capture_background()
        self.assertTrue(background["success"])

        self.source.frame = self.single_object()
        reference = self.adapter.capture_reference(
            "t_bolt",
            {"rotation_deg": "0", "position": "center"},
        )
        self.assertTrue(reference["success"])
        self.assertEqual(reference["status"], "reference_registered")
        self.assertGreater(reference["features"]["contour_area"], 0)
        self.assertEqual(reference["sample_count"], 1)

        prediction = self.adapter.classify_current()
        self.assertEqual(prediction["status"], "unknown")
        self.assertIsNone(prediction["class_key"])
        self.assertEqual(prediction["unknown_reason"], "REFERENCE_SAMPLES_INCOMPLETE")

        trial = self.adapter.run_classification_test("t_bolt")
        self.assertEqual(trial["outcome"], "unknown")
        self.assertFalse(trial["validation"]["mock_results_included"])
        self.assertEqual(trial["validation"]["by_class"]["t_bolt"]["unknown"], 1)
        self.assertIsNotNone(self.adapter.get_debug_jpeg())

        cleared = self.adapter.clear_class("t_bolt")
        self.assertEqual(cleared["removed_samples"], 1)
        self.assertEqual(self.adapter.get_status()["sample_counts"]["t_bolt"], 0)

    def test_no_camera_frame_never_creates_reference(self) -> None:
        result = self.adapter.capture_reference("flange_nut")
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "CAMERA_FRAME_UNAVAILABLE")
        self.assertEqual(self.adapter.get_status()["sample_counts"]["flange_nut"], 0)

    def test_multiple_objects_are_unknown_in_single_object_mode(self) -> None:
        self.source.frame = self.background()
        self.adapter.capture_background()
        frame = self.background()
        cv2.rectangle(frame, (30, 70), (120, 160), (10, 10, 10), thickness=-1)
        cv2.rectangle(frame, (200, 70), (290, 160), (10, 10, 10), thickness=-1)
        self.source.frame = frame

        result = self.adapter.classify_current()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["unknown_reason"], "MULTIPLE_OBJECTS_IN_SINGLE_MODE")
        self.assertEqual(result["object_count"], 2)

    def test_six_class_pipeline_only_becomes_ready_from_collected_samples(self) -> None:
        self.source.frame = self.background()
        self.adapter.capture_background()
        frames = self.synthetic_class_frames()

        for class_key, frame in frames.items():
            self.source.frame = frame
            self.adapter.capture_reference(class_key, {"rotation_deg": "0"})
            self.adapter.capture_reference(class_key, {"rotation_deg": "0"})

        status = self.adapter.get_status()
        self.assertTrue(status["classifier_ready"])
        self.assertGreater(len(status["selected_features"]), 0)
        self.assertTrue(all(count == 2 for count in status["sample_counts"].values()))

        for class_key, frame in frames.items():
            self.source.frame = frame
            result = self.adapter.run_classification_test(class_key)
            self.assertEqual(result["outcome"], "correct")
            self.assertEqual(result["predicted_class_key"], class_key)
            self.assertFalse(result["confidence_is_probability"])

        validation = self.adapter.get_validation_summary()
        self.assertFalse(validation["mock_results_included"])
        self.assertTrue(
            all(values["correct"] == 1 for values in validation["by_class"].values())
        )


if __name__ == "__main__":
    unittest.main()
