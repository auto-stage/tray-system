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
# X축: 좌/우 2열
# Z축: 상/중/하 3행
SLOT_ANCHORS = {
    1: ("XC1", "ZR1"),
    2: ("XC2", "ZR1"),
    3: ("XC1", "ZR2"),
    4: ("XC2", "ZR2"),
    5: ("XC1", "ZR3"),
    6: ("XC2", "ZR3"),
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
        -> XC/ZR anchor
        -> slot_map.json
        -> X/Z mm

    매핑값이 하나라도 없으면 success=False를 반환하여
    실제 Stage 이동을 금지할 수 있도록 한다.
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

    # JSON 배열 index 0~5
    # 실제 Slot 번호 1~6
    slot_number = (
        slots.index(tray_label) + 1
    )

    x_anchor, z_anchor = (
        SLOT_ANCHORS[slot_number]
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

    x_entry = taught.get(x_anchor)
    z_entry = taught.get(z_anchor)

    missing = []

    if not x_entry:
        missing.append(x_anchor)

    if not z_entry:
        missing.append(z_anchor)

    if missing:
        return {
            "success": False,
            "error": "MAPPING_INCOMPLETE",
            "message": (
                "미매핑 좌표: "
                + ", ".join(missing)
            ),
            "tray_id": tray_id,
            "tray": tray_label,
            "slot_number": slot_number,
            "slot_name": SLOT_NAMES[
                slot_number
            ],
            "x_anchor": x_anchor,
            "z_anchor": z_anchor,
            "missing": missing,
        }

    x_mm = x_entry.get("x_mm")
    z_mm = z_entry.get("z_mm")

    if x_mm is None:
        missing.append(x_anchor)

    if z_mm is None:
        missing.append(z_anchor)

    if missing:
        return {
            "success": False,
            "error": "MAPPING_INCOMPLETE",
            "message": (
                "좌표값 없음: "
                + ", ".join(missing)
            ),
            "tray_id": tray_id,
            "tray": tray_label,
            "slot_number": slot_number,
            "slot_name": SLOT_NAMES[
                slot_number
            ],
            "x_anchor": x_anchor,
            "z_anchor": z_anchor,
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
        "x_anchor": x_anchor,
        "z_anchor": z_anchor,
        "x_mm": float(x_mm),
        "z_mm": float(z_mm),
    }
