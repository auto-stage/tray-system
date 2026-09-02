"""Canonical part catalog shared by OCR, inspection, API, and review UI."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import yaml


PARTS_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "parts.yaml"


def _normalize(value: Any) -> str:
    return re.sub(
        r"[^0-9A-Z가-힣]",
        "",
        str(value or "").strip().upper(),
    )


def load_parts_catalog(
    path: str | Path = PARTS_CONFIG_PATH,
) -> dict[str, dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    parts = raw.get("parts", {})
    if not isinstance(parts, dict):
        raise ValueError("parts.yaml의 parts 항목이 mapping이어야 합니다.")

    catalog: dict[str, dict[str, Any]] = {}
    seen_part_numbers: set[str] = set()
    seen_yolo_ids: set[int] = set()
    for raw_key, raw_config in parts.items():
        class_key = str(raw_key).strip()
        if not class_key:
            raise ValueError("빈 class_key는 사용할 수 없습니다.")
        config = dict(raw_config or {})
        try:
            yolo_class_id = int(config["yolo_class_id"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"{class_key}.yolo_class_id는 0 이상의 정수여야 합니다.") from error
        if yolo_class_id < 0 or yolo_class_id in seen_yolo_ids:
            raise ValueError(f"중복되거나 잘못된 yolo_class_id: {yolo_class_id}")
        seen_yolo_ids.add(yolo_class_id)
        display_name = str(config.get("display_name") or "").strip()
        if not display_name:
            raise ValueError(f"{class_key}.display_name이 비어 있습니다.")
        part_no = str(config.get("part_no") or "").strip().upper() or None
        if part_no:
            if part_no in seen_part_numbers:
                raise ValueError(f"중복 part_no: {part_no}")
            seen_part_numbers.add(part_no)
        aliases = [
            str(value).strip()
            for value in config.get("ocr_aliases", [])
            if str(value).strip()
        ]
        if display_name not in aliases:
            aliases.insert(0, display_name)
        config.update(
            {
                "class_key": class_key,
                "display_name": display_name,
                "part_no": part_no,
                "ocr_aliases": aliases,
                "yolo_class_id": yolo_class_id,
            }
        )
        catalog[class_key] = config
    expected_yolo_ids = set(range(len(catalog)))
    if seen_yolo_ids != expected_yolo_ids:
        raise ValueError(
            "yolo_class_id는 0부터 class 수-1까지 연속이어야 합니다: "
            f"expected={sorted(expected_yolo_ids)}, actual={sorted(seen_yolo_ids)}"
        )
    return catalog


def get_yolo_class_mapping(
    catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[int, str]:
    parts = catalog or load_parts_catalog()
    return dict(sorted(
        (int(config["yolo_class_id"]), class_key)
        for class_key, config in parts.items()
    ))


def _number_text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    number = float(value)
    return str(int(number)) if number.is_integer() else f"{number:g}"


def format_part_spec(config: dict[str, Any]) -> str:
    nominal = str(config.get("nominal_size") or "").strip()
    class_key = str(config.get("class_key") or "")

    if class_key in {"hex_bolt", "socket_head_bolt"}:
        length = _number_text(config.get("length_mm"))
        if nominal and length:
            return f"{nominal} × {length} mm"
        return nominal or "-"
    if class_key == "flange_nut":
        return nominal or "-"

    fields_by_class = {
        "corner_bracket": ("width_mm", "height_mm", "thickness_mm"),
        "straight_connector": ("length_mm", "width_mm", "height_mm"),
    }
    if class_key in fields_by_class:
        values = [_number_text(config.get(field)) for field in fields_by_class[class_key]]
        return f"{' × '.join(values)} mm" if all(values) else "-"
    if class_key == "l_bracket":
        leg = _number_text(config.get("leg_length_mm"))
        width = _number_text(config.get("width_mm"))
        thickness = _number_text(config.get("thickness_mm"))
        return f"{leg} × {width} / t={thickness} mm" if all((leg, width, thickness)) else "-"
    return nominal or "-"


def serialize_part(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    result["spec_display"] = format_part_spec(config)
    weight = config.get("weight_g")
    result["weight_display"] = f"{_number_text(weight)} g" if weight is not None else "-"
    result["reference_registered"] = False
    return result


def find_part_by_identifier(
    identifier: str | None,
    *,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    parts = catalog or load_parts_catalog()
    target = _normalize(identifier)
    if not target:
        return None
    for class_key, config in parts.items():
        values = [class_key, config.get("part_no"), config.get("display_name")]
        values.extend(config.get("ocr_aliases", []))
        if any(_normalize(value) == target for value in values):
            return dict(config)
    return None


def find_part(part_no: str, name: str, spec: str):
    """Legacy review validator backed by parts.yaml."""
    config = find_part_by_identifier(part_no) or find_part_by_identifier(name)
    if config is None:
        return None
    valid_names = {
        _normalize(config.get("display_name")),
        *(_normalize(value) for value in config.get("ocr_aliases", [])),
    }
    if _normalize(name) not in valid_names:
        return None
    expected_spec = format_part_spec(config)
    if _normalize(spec) not in {_normalize(expected_spec), ""}:
        return None
    return {
        "class_key": config["class_key"],
        "part_no": config.get("part_no") or config["class_key"],
        "name": config["display_name"],
        "spec": expected_spec,
        "tray": config.get("tray_id"),
    }


def get_ocr_part_db() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for class_key, config in load_parts_catalog().items():
        code = config.get("part_no") or class_key
        spec_display = format_part_spec(config)
        result[str(code)] = {
            "class_key": class_key,
            "name": config["display_name"],
            "aliases": list(config.get("ocr_aliases", [])),
            # An unmeasured spec must not become a contradictory OCR score.
            "spec": None if spec_display == "-" else spec_display,
            "tray": config.get("tray_id"),
        }
    return result
