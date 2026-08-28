from __future__ import annotations

import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"

RACK_LAYOUT_PATH = DATA_DIR / "rack_layout.json"
SLOT_MAP_PATH = DATA_DIR / "slot_map.json"


# 실제 Rack의 물리 위치
#
# Slot 1 : 상단 좌측
# Slot 2 : 상단 우측
# Slot 3 : 중단 좌측
# Slot 4 : 중단 우측
# Slot 5 : 하단 좌측
# Slot 6 : 하단 우측
#
# 오늘 최종 Teaching한 6개 좌표 키와 직접 연결한다.
SLOT_MAPPING_KEYS = {
    1: "XC1ZR1",
    2: "XC2ZR1",
    3: "XC1ZR2",
    4: "XC2ZR2",
    5: "XC1ZR3",
    6: "XC2ZR3",
}


SLOT_NAMES = {
    1: "상단 좌측",
    2: "상단 우측",
    3: "중단 좌측",
    4: "중단 우측",
    5: "하단 좌측",
    6: "하단 우측",
}


def _load_json(path: Path) -> dict:
    with path.open(
        "r",
        encoding="utf-8",
    ) as f:
        return json.load(f)


def resolve_tray_target(
    tray_id: int,
) -> dict:
    """
    Tray ID를 실제 물리 Slot과 X/Z 목표좌표로 변환한다.

    흐름:
        Tray ID
        -> rack_layout.json
        -> 물리 Slot
        -> 6점 직접 Mapping Key
        -> slot_map.json
        -> X/Z mm

    예:
        TRAY 01
        -> Slot 5
        -> XC1ZR3
        -> x_mm / z_mm
    """

    if tray_id not in range(1, 7):
        return {
            "success": False,
            "error": "INVALID_TRAY_ID",
            "message": f"잘못된 Tray ID: {tray_id}",
        }

    tray_label = f"TRAY {tray_id:02d}"

    try:
        rack_data = _load_json(
            RACK_LAYOUT_PATH
        )
    except Exception as exc:
        return {
            "success": False,
            "error": "RACK_LAYOUT_LOAD_ERROR",
            "message": str(exc),
        }

    slots = rack_data.get("slots", [])

    if tray_label not in slots:
        return {
            "success": False,
            "error": "TRAY_NOT_IN_RACK",
            "message": (
                f"{tray_label}가 rack_layout에 없습니다."
            ),
        }

    # JSON index 0~5 -> 실제 Slot 1~6
    slot_number = (
        slots.index(tray_label) + 1
    )

    mapping_key = (
        SLOT_MAPPING_KEYS[slot_number]
    )

    try:
        map_data = _load_json(
            SLOT_MAP_PATH
        )
    except Exception as exc:
        return {
            "success": False,
            "error": "SLOT_MAP_LOAD_ERROR",
            "message": str(exc),
        }

    taught = map_data.get(
        "slots",
        {},
    )

    entry = taught.get(mapping_key)

    if not entry:
        return {
            "success": False,
            "error": "MAPPING_INCOMPLETE",
            "message": (
                f"미매핑 좌표: {mapping_key}"
            ),
            "tray_id": tray_id,
            "tray": tray_label,
            "slot_number": slot_number,
            "slot_name": SLOT_NAMES[
                slot_number
            ],
            "mapping_key": mapping_key,
        }

    x_mm = entry.get("x_mm")
    z_mm = entry.get("z_mm")

    missing = []

    if x_mm is None:
        missing.append("x_mm")

    if z_mm is None:
        missing.append("z_mm")

    if missing:
        return {
            "success": False,
            "error": "MAPPING_INCOMPLETE",
            "message": (
                f"{mapping_key} 좌표값 없음: "
                + ", ".join(missing)
            ),
            "tray_id": tray_id,
            "tray": tray_label,
            "slot_number": slot_number,
            "slot_name": SLOT_NAMES[
                slot_number
            ],
            "mapping_key": mapping_key,
            "missing": missing,
        }

    return {
        "success": True,
        "tray_id": tray_id,
        "tray": tray_label,
        "slot_number": slot_number,
        "slot_name": SLOT_NAMES[
            slot_number
        ],
        "mapping_key": mapping_key,
        "x_mm": float(x_mm),
        "z_mm": float(z_mm),
    }
