from __future__ import annotations

from pathlib import Path
import sys
import unittest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from parts_db import find_part_by_identifier, get_ocr_part_db, load_parts_catalog


class PartsDatabaseTest(unittest.TestCase):
    def test_catalog_has_only_the_six_approved_class_keys(self) -> None:
        catalog = load_parts_catalog(BACKEND_DIR / "config" / "parts.yaml")
        self.assertEqual(
            set(catalog),
            {
                "flange_nut",
                "t_bolt",
                "socket_head_bolt",
                "corner_bracket",
                "t_nut",
                "l_bracket",
            },
        )
        self.assertTrue(all(item["weight_g"] is None for item in catalog.values()))

    def test_part_number_and_alias_resolve_to_canonical_class(self) -> None:
        self.assertEqual(find_part_by_identifier("B001")["class_key"], "t_bolt")
        self.assertEqual(find_part_by_identifier("육각렌치볼트")["class_key"], "socket_head_bolt")

    def test_unmeasured_spec_is_not_scored_as_real_ocr_text(self) -> None:
        database = get_ocr_part_db()
        self.assertIsNone(database["B001"]["spec"])
        self.assertIsNone(database["W002"]["spec"])


if __name__ == "__main__":
    unittest.main()
