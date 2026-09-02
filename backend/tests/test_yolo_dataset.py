from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

import numpy as np
import yaml


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from parts_db import load_parts_catalog
from services.yolo_dataset import YoloDatasetError, YoloDatasetService


class FrameSource:
    def __init__(self):
        self.value = 0

    def __call__(self, copy=True):
        self.value += 1
        return np.full((100, 200, 3), self.value, dtype=np.uint8)


class YoloDatasetTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.parts = load_parts_catalog(BACKEND_DIR / "config" / "parts.yaml")
        self.service = YoloDatasetService(root=Path(self.temporary.name), frame_source=FrameSource(), parts=self.parts)

    def tearDown(self):
        self.temporary.cleanup()

    def test_pixel_normalized_round_trip_and_invalid_box(self):
        item = self.service.capture(suggested_class_key="t_bolt", capture_group="one")
        saved = self.service.save_annotation(item["image_id"], boxes=[{"class_key": "t_bolt", "x": 20, "y": 10, "width": 80, "height": 40}], state="MANUAL")
        yolo = saved["boxes"][0]["yolo"]
        restored = self.service.normalized_to_pixel(yolo, 200, 100)
        for key, expected in {"x": 20, "y": 10, "width": 80, "height": 40}.items():
            self.assertAlmostEqual(restored[key], expected, places=5)
        with self.assertRaises(YoloDatasetError):
            self.service.save_annotation(item["image_id"], boxes=[{"class_key": "t_bolt", "x": 190, "y": 0, "width": 20, "height": 10}], state="MANUAL")

    def test_background_is_not_unlabeled(self):
        item = self.service.capture(capture_group="background")
        self.service.save_annotation(item["image_id"], boxes=[], state="BACKGROUND")
        result = self.service.validate()
        self.assertEqual(result["background_image_count"], 1)
        self.assertEqual(result["unlabeled_image_count"], 0)

    def test_validation_split_yaml_and_export(self):
        keys = list(self.parts)
        for index, key in enumerate(keys):
            for group in ("group_a", "group_b"):
                item = self.service.capture(suggested_class_key=key, capture_group=group)
                self.service.save_annotation(item["image_id"], boxes=[{"class_key": key, "x": 10 + index, "y": 10, "width": 30, "height": 20}], state="REVIEWED")
        validation = self.service.validate()
        self.assertTrue(validation["valid"], validation["errors"])
        first = self.service.create_split(train_ratio=.8, seed=17)
        first_manifest = json.loads((self.service.generated / "manifest.json").read_text())
        second = self.service.create_split(train_ratio=.8, seed=17)
        second_manifest = json.loads((self.service.generated / "manifest.json").read_text())
        self.assertEqual(first_manifest["train_image_ids"], second_manifest["train_image_ids"])
        self.assertTrue(first["train_count"] and second["val_count"])
        dataset = yaml.safe_load((self.service.generated / "dataset.yaml").read_text())
        self.assertEqual(dataset["names"], {index: key for index, key in enumerate(keys)})
        archive_path = self.service.export_zip()
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            self.assertIn("dataset.yaml", names)
            portable = yaml.safe_load(archive.read("dataset.yaml"))
            self.assertEqual(portable["path"], ".")
            self.assertTrue(any(name.startswith("images/train/") for name in names))
            self.assertTrue(any(name.startswith("labels/val/") for name in names))


if __name__ == "__main__":
    unittest.main()
