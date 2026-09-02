from __future__ import annotations

import unittest
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from adapters.mock_loadcell_adapter import MockLoadCellAdapter
from adapters.mock_part_inspection_adapter import MockPartInspectionAdapter
from services.inspection_service import InspectionService


class SingleObjectOnlyVision:
    def get_status(self):
        return {
            "mock": False,
            "sample_counts": {},
            "counting_validated": False,
        }

    def inspect(self, *, class_key, part_config, expected_count=None):
        return {
            "success": True,
            "mock": False,
            "status": "classified",
            "class_key": class_key,
            "detected_class": class_key,
            "detected_count": 1,
            "count": 1,
            "counting_validated": False,
            "parts": [],
        }


class InspectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = InspectionService(
            loadcell=MockLoadCellAdapter(),
            part_vision=MockPartInspectionAdapter(),
            parts_config_path=BACKEND_DIR / "config" / "parts.yaml",
        )

    def test_mock_pass_keeps_legacy_fields(self) -> None:
        result = self.service.run(
            part_no="B001",
            expected_quantity=10,
        )
        self.assertTrue(result["success"])
        self.assertTrue(result["passed"])
        self.assertTrue(result["matched"])
        self.assertEqual(result["detected_quantity"], 10)
        self.assertEqual(result["loadcell"]["estimated_quantity"], 10)
        self.assertEqual(result["vision"]["detected_part_no"], "B001")
        self.assertTrue(result["mock"])

    def test_unknown_part_is_blocked(self) -> None:
        result = self.service.run(
            part_no="UNKNOWN",
            expected_quantity=1,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "PART_NOT_CONFIGURED")

    def test_invalid_quantity_is_blocked(self) -> None:
        result = self.service.run(
            part_no="B001",
            expected_quantity=0,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "INVALID_EXPECTED_QUANTITY")

    def test_mock_cannot_register_reference_or_validation_trial(self) -> None:
        result = self.service.debug_action(
            action="capture_reference",
            class_key="t_bolt",
        )
        self.assertFalse(result["success"])
        self.assertTrue(result["mock"])
        self.assertEqual(result["error"], "REAL_CAMERA_REQUIRED")
        self.assertFalse(
            self.service.get_debug_status()["inspection"]["validation"][
                "mock_results_included"
            ]
        )

    def test_multi_part_count_is_unknown_until_counting_is_validated(self) -> None:
        service = InspectionService(
            loadcell=MockLoadCellAdapter(),
            part_vision=SingleObjectOnlyVision(),
            parts_config_path=BACKEND_DIR / "config" / "parts.yaml",
        )
        result = service.run(part_no="B001", expected_quantity=5)
        self.assertTrue(result["success"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "unknown")
        self.assertIn("COUNTING_NOT_VALIDATED", result["reasons"])


if __name__ == "__main__":
    unittest.main()
