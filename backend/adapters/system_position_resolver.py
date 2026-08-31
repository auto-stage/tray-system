import json
from pathlib import Path


DATA_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "system_positions.json"
)


def resolve_system_position(
    position_name: str,
):
    """
    CONVEYOR_HANDOFF 같은 시스템 고정 위치를
    X/Z 절대좌표로 변환한다.
    """

    try:
        with DATA_PATH.open(
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

    except Exception as error:
        return {
            "success": False,
            "error": "SYSTEM_POSITION_FILE_ERROR",
            "message": (
                "시스템 위치 파일을 읽을 수 없습니다: "
                f"{error}"
            ),
        }

    positions = data.get(
        "positions",
        {},
    )

    target = positions.get(
        position_name
    )

    if not target:
        return {
            "success": False,
            "error": "SYSTEM_POSITION_NOT_FOUND",
            "message": (
                f"시스템 위치를 찾을 수 없습니다: "
                f"{position_name}"
            ),
        }

    x_mm = target.get("x_mm")
    z_mm = target.get("z_mm")

    if not isinstance(
        x_mm,
        (int, float),
    ) or not isinstance(
        z_mm,
        (int, float),
    ):
        return {
            "success": False,
            "error": "INVALID_SYSTEM_POSITION",
            "message": (
                f"{position_name}의 X/Z 좌표가 "
                "올바르지 않습니다."
            ),
        }

    return {
        "success": True,
        "position_name": position_name,
        "x_mm": float(x_mm),
        "z_mm": float(z_mm),
        "temporary": bool(
            target.get(
                "temporary",
                False,
            )
        ),
        "note": target.get(
            "note",
            "",
        ),
    }
