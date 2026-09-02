from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
from typing import Any, Callable
import uuid
import zipfile

import cv2
import yaml


ANNOTATION_STATES = {"UNLABELED", "MANUAL", "AUTO_UNREVIEWED", "REVIEWED", "BACKGROUND"}
TRAINABLE_STATES = {"MANUAL", "REVIEWED", "BACKGROUND"}


class YoloDatasetError(ValueError):
    pass


class YoloDatasetService:
    """Filesystem dataset edited by the UI; OpenCV reference data is untouched."""

    def __init__(self, *, root: str | Path, frame_source: Callable[..., Any], parts: dict[str, dict[str, Any]]) -> None:
        self.root = Path(root).resolve()
        self.frame_source = frame_source
        self.parts = parts
        self.source = self.root / "source"
        self.images = self.source / "images"
        self.labels = self.source / "labels"
        self.meta = self.source / "meta"
        self.generated = self.root / "generated"
        self.exports = self.root / "exports"
        self.id_to_key = dict(sorted(
            (int(config["yolo_class_id"]), key) for key, config in parts.items()
        ))
        self.key_to_id = {key: class_id for class_id, key in self.id_to_key.items()}
        self._validation_cache: dict[str, Any] | None = None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure(self) -> None:
        for path in (self.images, self.labels, self.meta, self.exports):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_json(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _image_id(value: str) -> str:
        value = str(value).strip().lower()
        if len(value) != 32 or any(char not in "0123456789abcdef" for char in value):
            raise YoloDatasetError("잘못된 image_id입니다.")
        return value

    def _paths(self, image_id: str) -> tuple[Path, Path, Path]:
        image_id = self._image_id(image_id)
        return self.images / f"{image_id}.jpg", self.labels / f"{image_id}.txt", self.meta / f"{image_id}.json"

    def _load(self, image_id: str) -> dict[str, Any]:
        image_path, _, meta_path = self._paths(image_id)
        if not image_path.is_file() or not meta_path.is_file():
            raise YoloDatasetError("이미지를 찾을 수 없습니다.")
        try:
            value = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise YoloDatasetError(f"metadata가 손상되었습니다: {error}") from error
        if not isinstance(value, dict):
            raise YoloDatasetError("metadata 형식이 잘못되었습니다.")
        return value

    def capture(self, *, suggested_class_key: str | None = None, capture_group: str | None = None) -> dict[str, Any]:
        if suggested_class_key and suggested_class_key not in self.key_to_id:
            raise YoloDatasetError("지원하지 않는 촬영 class입니다.")
        try:
            frame = self.frame_source(copy=True)
        except TypeError:
            frame = self.frame_source()
        if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
            raise YoloDatasetError("CAMERA_FRAME_UNAVAILABLE: C920 frame을 읽을 수 없습니다.")
        height, width = frame.shape[:2]
        if width <= 0 or height <= 0:
            raise YoloDatasetError("CAMERA_FRAME_UNAVAILABLE: frame 크기가 잘못되었습니다.")
        self._ensure()
        image_id = uuid.uuid4().hex
        image_path, label_path, meta_path = self._paths(image_id)
        if not cv2.imwrite(str(image_path), frame):
            raise YoloDatasetError("C920 frame 저장에 실패했습니다.")
        item = {
            "image_id": image_id,
            "file_name": image_path.name,
            "width": int(width),
            "height": int(height),
            "sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "captured_at": self._now(),
            "capture_group": str(capture_group or datetime.now().strftime("%Y%m%d_%H%M"))[:80],
            "suggested_class_key": suggested_class_key,
            "annotation_state": "UNLABELED",
            "boxes": [],
        }
        label_path.write_text("", encoding="utf-8")
        self._atomic_json(meta_path, item)
        self._validation_cache = None
        return item

    def list_images(self) -> list[dict[str, Any]]:
        if not self.meta.is_dir():
            return []
        result = []
        for path in self.meta.glob("*.json"):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                image_path, _, _ = self._paths(str(item.get("image_id", "")))
                if image_path.is_file():
                    result.append(item)
            except (OSError, json.JSONDecodeError, YoloDatasetError):
                continue
        return sorted(result, key=lambda item: str(item.get("captured_at", "")), reverse=True)

    def image_path(self, image_id: str) -> Path:
        image_path, _, _ = self._paths(image_id)
        if not image_path.is_file():
            raise YoloDatasetError("이미지를 찾을 수 없습니다.")
        return image_path

    def delete(self, image_id: str) -> None:
        paths = self._paths(image_id)
        if not any(path.exists() for path in paths):
            raise YoloDatasetError("이미지를 찾을 수 없습니다.")
        for path in paths:
            if path.is_file():
                path.unlink()
        self._validation_cache = None

    def save_annotation(self, image_id: str, *, boxes: list[dict[str, Any]], state: str) -> dict[str, Any]:
        state = str(state).strip().upper()
        if state not in ANNOTATION_STATES or state == "UNLABELED":
            raise YoloDatasetError("저장 가능한 annotation 상태가 아닙니다.")
        item = self._load(image_id)
        width, height = int(item["width"]), int(item["height"])
        if state == "BACKGROUND" and boxes:
            raise YoloDatasetError("Intentional Background에는 Box를 저장할 수 없습니다.")
        if state != "BACKGROUND" and not boxes:
            raise YoloDatasetError("Box가 없으면 Intentional Background를 선택하세요.")
        normalized_boxes: list[dict[str, Any]] = []
        lines: list[str] = []
        for index, box in enumerate(boxes):
            class_key = str(box.get("class_key", ""))
            if class_key not in self.key_to_id:
                raise YoloDatasetError(f"Box {index + 1}: invalid class")
            try:
                x, y = float(box["x"]), float(box["y"])
                box_width, box_height = float(box["width"]), float(box["height"])
            except (KeyError, TypeError, ValueError) as error:
                raise YoloDatasetError(f"Box {index + 1}: invalid coordinate") from error
            values = (x, y, box_width, box_height)
            if not all(math.isfinite(value) for value in values):
                raise YoloDatasetError(f"Box {index + 1}: NaN/Infinity 좌표")
            if box_width <= 0 or box_height <= 0:
                raise YoloDatasetError(f"Box {index + 1}: width/height는 0보다 커야 합니다.")
            if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
                raise YoloDatasetError(f"Box {index + 1}: 이미지 경계를 벗어났습니다.")
            yolo = ((x + box_width / 2) / width, (y + box_height / 2) / height, box_width / width, box_height / height)
            class_id = self.key_to_id[class_key]
            normalized_boxes.append({
                "class_key": class_key,
                "class_id": class_id,
                "x": round(x, 4), "y": round(y, 4),
                "width": round(box_width, 4), "height": round(box_height, 4),
                "yolo": [round(value, 8) for value in yolo],
                "confidence": box.get("confidence"),
            })
            lines.append(f"{class_id} " + " ".join(f"{value:.8f}" for value in yolo))
        _, label_path, meta_path = self._paths(image_id)
        label_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        item.update({"annotation_state": state, "boxes": normalized_boxes, "updated_at": self._now()})
        self._atomic_json(meta_path, item)
        self._validation_cache = None
        return item

    @staticmethod
    def normalized_to_pixel(yolo: list[float], width: int, height: int) -> dict[str, float]:
        center_x, center_y, box_width, box_height = map(float, yolo)
        pixel_width, pixel_height = box_width * width, box_height * height
        return {
            "x": center_x * width - pixel_width / 2,
            "y": center_y * height - pixel_height / 2,
            "width": pixel_width,
            "height": pixel_height,
        }

    def validate(self, *, force: bool = False) -> dict[str, Any]:
        if self._validation_cache is not None and not force:
            return dict(self._validation_cache)
        self._ensure()
        items = self.list_images()
        errors: list[dict[str, str]] = []
        counts = Counter({key: 0 for key in self.key_to_id})
        states = Counter()
        seen_hashes: dict[str, str] = {}
        known_ids = {str(item["image_id"]) for item in items}
        for meta_path in self.meta.glob("*.json"):
            if meta_path.stem not in known_ids:
                errors.append({"image_id": meta_path.stem, "message": "image/annotation mismatch: 손상 metadata 또는 image 없음"})
        for image_path in self.images.glob("*.jpg"):
            if image_path.stem not in known_ids:
                errors.append({"image_id": image_path.stem, "message": "image/annotation mismatch: metadata 없음"})
        for label_path in self.labels.glob("*.txt"):
            if label_path.stem not in known_ids:
                errors.append({"image_id": label_path.stem, "message": "image/annotation mismatch: image 없음"})
        for item in items:
            image_id = str(item["image_id"])
            state = str(item.get("annotation_state", "UNLABELED"))
            states[state] += 1
            image_path, label_path, _ = self._paths(image_id)
            decoded = cv2.imread(str(image_path))
            if decoded is None:
                errors.append({"image_id": image_id, "message": "손상 이미지"})
            if not label_path.is_file():
                errors.append({"image_id": image_id, "message": "label 파일 없음"})
            else:
                try:
                    label_rows = [row.split() for row in label_path.read_text(encoding="utf-8").splitlines() if row.strip()]
                    if len(label_rows) != len(item.get("boxes", [])):
                        errors.append({"image_id": image_id, "message": "image/annotation mismatch: label Box 수 불일치"})
                    for row_index, row in enumerate(label_rows):
                        if len(row) != 5:
                            raise ValueError(f"label row {row_index + 1} field count")
                        class_id = int(row[0]); values = [float(value) for value in row[1:]]
                        if class_id not in self.id_to_key or not all(math.isfinite(value) and 0 <= value <= 1 for value in values) or values[2] <= 0 or values[3] <= 0:
                            raise ValueError(f"label row {row_index + 1} value")
                except (OSError, ValueError) as error:
                    errors.append({"image_id": image_id, "message": f"잘못된 YOLO label: {error}"})
            digest = str(item.get("sha256", ""))
            if digest in seen_hashes:
                errors.append({"image_id": image_id, "message": f"중복 이미지: {seen_hashes[digest]}"})
            elif digest:
                seen_hashes[digest] = image_id
            if state == "AUTO_UNREVIEWED":
                errors.append({"image_id": image_id, "message": "Auto Label 검토 필요"})
            elif state == "UNLABELED":
                errors.append({"image_id": image_id, "message": "미라벨 이미지"})
                continue
            elif state not in TRAINABLE_STATES:
                errors.append({"image_id": image_id, "message": f"invalid annotation state: {state}"})
            boxes = item.get("boxes", [])
            if state == "BACKGROUND" and boxes:
                errors.append({"image_id": image_id, "message": "Background에 Box 존재"})
            if state in {"MANUAL", "REVIEWED"} and not boxes:
                errors.append({"image_id": image_id, "message": "라벨 완료 이미지에 Box 없음"})
            for box_index, box in enumerate(boxes):
                class_key = str(box.get("class_key", ""))
                yolo = box.get("yolo", [])
                if class_key not in counts:
                    errors.append({"image_id": image_id, "message": f"Box {box_index + 1}: invalid class"})
                    continue
                if len(yolo) != 4 or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in yolo):
                    errors.append({"image_id": image_id, "message": f"Box {box_index + 1}: invalid normalized coordinate"})
                    continue
                if not all(0 <= float(value) <= 1 for value in yolo) or float(yolo[2]) <= 0 or float(yolo[3]) <= 0:
                    errors.append({"image_id": image_id, "message": f"Box {box_index + 1}: normalized coordinate out of range"})
                    continue
                counts[class_key] += 1
        missing = [key for key, count in counts.items() if count == 0]
        if missing:
            errors.append({"image_id": "dataset", "message": "학습 object가 없는 class: " + ", ".join(missing)})
        unlabeled = states["UNLABELED"]
        valid = bool(items) and not errors and unlabeled == 0
        result = {
            "valid": valid,
            "image_count": len(items),
            "labeled_image_count": sum(states[state] for state in TRAINABLE_STATES),
            "unlabeled_image_count": unlabeled,
            "background_image_count": states["BACKGROUND"],
            "auto_unreviewed_count": states["AUTO_UNREVIEWED"],
            "class_object_counts": dict(counts),
            "invalid_annotation_count": len(errors),
            "errors": errors,
        }
        self._validation_cache = result
        return dict(result)

    def create_split(self, *, train_ratio: float = 0.8, seed: int = 42) -> dict[str, Any]:
        if not 0.5 <= float(train_ratio) < 1:
            raise YoloDatasetError("Train 비율은 0.5 이상 1.0 미만이어야 합니다.")
        validation = self.validate(force=True)
        if not validation["valid"]:
            raise YoloDatasetError("Dataset validation을 통과해야 합니다.")
        items = self.list_images()
        groups: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            groups.setdefault(str(item.get("capture_group") or item["image_id"]), []).append(item)
        if len(groups) < 2:
            raise YoloDatasetError("Leakage 방지를 위해 서로 다른 capture group이 최소 2개 필요합니다.")
        group_names = sorted(groups)
        random.Random(int(seed)).shuffle(group_names)
        target = max(1, min(len(items) - 1, round(len(items) * float(train_ratio))))
        train_groups: set[str] = set()
        count = 0
        for group in group_names[:-1]:
            if count < target:
                train_groups.add(group)
                count += len(groups[group])
        split = {item["image_id"]: ("train" if group in train_groups else "val") for group, values in groups.items() for item in values}
        if not any(value == "train" for value in split.values()) or not any(value == "val" for value in split.values()):
            raise YoloDatasetError("Train/Validation 양쪽에 이미지가 필요합니다.")
        staging = self.root / "generated.tmp"
        if staging.exists():
            shutil.rmtree(staging)
        for name in ("train", "val"):
            (staging / "images" / name).mkdir(parents=True, exist_ok=True)
            (staging / "labels" / name).mkdir(parents=True, exist_ok=True)
        for item in items:
            image_path, label_path, _ = self._paths(item["image_id"])
            name = split[item["image_id"]]
            shutil.copy2(image_path, staging / "images" / name / image_path.name)
            shutil.copy2(label_path, staging / "labels" / name / label_path.name)
        names = {class_id: key for class_id, key in self.id_to_key.items()}
        yaml_value = {"path": str(self.generated.resolve()), "train": "images/train", "val": "images/val", "names": names}
        (staging / "dataset.yaml").write_text(yaml.safe_dump(yaml_value, allow_unicode=True, sort_keys=False), encoding="utf-8")
        manifest = {
            "created_at": self._now(), "seed": int(seed), "train_ratio": float(train_ratio),
            "train_image_ids": [key for key, value in split.items() if value == "train"],
            "val_image_ids": [key for key, value in split.items() if value == "val"],
            "class_mapping": names,
            "source_signature": self._source_signature(items),
        }
        self._atomic_json(staging / "manifest.json", manifest)
        if self.generated.exists():
            shutil.rmtree(self.generated)
        staging.replace(self.generated)
        return {"success": True, "dataset_yaml": str(self.generated / "dataset.yaml"), "train_count": len(manifest["train_image_ids"]), "val_count": len(manifest["val_image_ids"]), **manifest}

    def split_status(self) -> dict[str, Any]:
        path = self.generated / "manifest.json"
        if not path.is_file() or not (self.generated / "dataset.yaml").is_file():
            return {"ready": False, "dataset_yaml": None}
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("source_signature") != self._source_signature(self.list_images()):
                return {"ready": False, "stale": True, "dataset_yaml": None, **manifest}
            return {"ready": True, "stale": False, "dataset_yaml": str(self.generated / "dataset.yaml"), **manifest}
        except (OSError, json.JSONDecodeError):
            return {"ready": False, "dataset_yaml": None}

    @staticmethod
    def _source_signature(items: list[dict[str, Any]]) -> str:
        value = [
            {
                "image_id": item.get("image_id"),
                "updated_at": item.get("updated_at") or item.get("captured_at"),
                "state": item.get("annotation_state"),
                "boxes": item.get("boxes", []),
            }
            for item in sorted(items, key=lambda entry: str(entry.get("image_id", "")))
        ]
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def export_zip(self) -> Path:
        split = self.split_status()
        if not split["ready"]:
            raise YoloDatasetError("Train/Validation split을 먼저 생성하세요.")
        self.exports.mkdir(parents=True, exist_ok=True)
        target = self.exports / "part_yolo_dataset.zip"
        temporary = target.with_suffix(".zip.tmp")
        portable_yaml = {"path": ".", "train": "images/train", "val": "images/val", "names": self.id_to_key}
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("dataset.yaml", yaml.safe_dump(portable_yaml, allow_unicode=True, sort_keys=False))
            for folder in ("images/train", "images/val", "labels/train", "labels/val"):
                for path in (self.generated / folder).glob("*"):
                    if path.is_file():
                        archive.write(path, f"{folder}/{path.name}")
        temporary.replace(target)
        return target
