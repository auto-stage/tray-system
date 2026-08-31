import json
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
HISTORY_PATH = BASE_DIR / "data" / "work_history.json"


def _read_json(path: Path, default: Any):
    if not path.exists():
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )


def get_history():
    data = _read_json(
        HISTORY_PATH,
        {"history": []},
    )

    history = data.get("history", [])

    return sorted(
        history,
        key=lambda item: item.get(
            "completed_at",
            ""
        ),
        reverse=True,
    )


def add_history(
    work_id: str,
    items: list[dict],
    used_trays: list[str],
    result: str = "COMPLETED",
    duration_seconds: int = 0,
):
    """
    완료 작업 1건 저장.
    같은 work_id는 중복 저장하지 않는다.
    """

    if not work_id:
        return {
            "success": False,
            "message": "work_id가 없습니다.",
        }

    data = _read_json(
        HISTORY_PATH,
        {"history": []},
    )

    history = data.get("history", [])

    for record in history:
        if record.get("work_id") == work_id:
            return {
                "success": True,
                "already_exists": True,
                "message": "이미 저장된 작업 이력입니다.",
                "data": record,
            }

    normalized_items = []
    total_quantity = 0

    for item in items:
        quantity = int(
            item.get("quantity", 0)
        )

        total_quantity += quantity

        normalized_items.append(
            {
                "part_no": str(item.get("part_no", "")).strip(),
                "name": str(item.get("name", "")).strip(),
                "spec": str(item.get("spec", "")).strip(),
                "quantity": quantity,
                "tray": str(item.get("tray", "")).strip(),
            }
        )

    record = {
        "work_id": work_id,
        "completed_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "result": result,
        "duration_seconds": max(
            int(duration_seconds),
            0,
        ),
        "item_count": len(normalized_items),
        "total_quantity": total_quantity,
        "used_trays": used_trays,
        "items": normalized_items,
    }

    history.append(record)
    data["history"] = history

    _write_json(
        HISTORY_PATH,
        data,
    )

    return {
        "success": True,
        "already_exists": False,
        "message": "작업 이력 저장 완료",
        "data": record,
    }