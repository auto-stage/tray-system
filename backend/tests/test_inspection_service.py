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


if __name__ == "__main__":
    unittest.main()
