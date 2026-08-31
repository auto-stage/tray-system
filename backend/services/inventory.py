import json
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent

INVENTORY_PATH = BASE_DIR / "data" / "inventory.json"
TRANSACTION_PATH = BASE_DIR / "data" / "inventory_transactions.json"

MAX_STOCK = 500


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


def load_inventory():
    """
    inventory.json 전체 재고를 읽는다.
    """
    return _read_json(
        INVENTORY_PATH,
        {},
    )


def save_inventory(data):
    """
    전체 재고를 inventory.json에 저장한다.
    """
    _write_json(
        INVENTORY_PATH,
        data,
    )


def get_all_inventory():
    """
    UI용 전체 재고 조회.

    - 최대 재고: 500개
    - 500개 = 100%
    - 100개 이하 = LOW STOCK
    """
    inventory = load_inventory()

    result = {}

    for part_no, item in inventory.items():
        stock = int(
            item.get("stock", 0)
        )

        # 비정상 값 방어
        stock = max(
            0,
            min(
                stock,
                MAX_STOCK,
            )
        )

        result[part_no] = {
            **item,
            "stock": stock,
            "max_stock": MAX_STOCK,
            "percent": min(
                round(
                    (
                        stock
                        / MAX_STOCK
                    )
                    * 100
                ),
                100,
            ),
            "status": (
                "LOW STOCK"
                if stock <= 100
                else "READY"
            ),
        }

    return result


def get_item(part_no):
    """
    특정 품번 조회.
    """
    return get_all_inventory().get(
        part_no
    )


def consume_inventory(
    work_id: str,
    items: list[dict],
):
    """
    작업이 최종 완료된 시점에 실제 재고를 1회만 차감한다.

    이번 버전의 안전장치:

    1. 같은 work_id 중복 차감 방지
    2. 같은 품번이 요청에 두 번 들어오면 차감 중단
    3. 모든 품목 검증 완료 후에만 실제 재고 변경
    4. before / used / after를 transaction 파일에 기록
    5. 차감 시각까지 기록

    예:
    W001 : 470 -> 459 (-11)
    """

    if not work_id:
        return {
            "success": False,
            "message": "work_id가 없습니다.",
        }

    transactions = _read_json(
        TRANSACTION_PATH,
        {
            "completed_work_ids": [],
            "transactions": [],
        },
    )

    completed_ids = transactions.get(
        "completed_work_ids",
        [],
    )

    transaction_history = transactions.get(
        "transactions",
        [],
    )

    # --------------------------------------------------------
    # 같은 작업이 다시 들어오면 절대 다시 차감하지 않음
    # --------------------------------------------------------
    if work_id in completed_ids:
        previous = next(
            (
                transaction
                for transaction
                in transaction_history
                if transaction.get(
                    "work_id"
                ) == work_id
            ),
            None,
        )

        return {
            "success": True,
            "already_processed": True,
            "message": "이미 재고 차감이 완료된 작업입니다.",
            "transaction": previous,
            "data": get_all_inventory(),
        }

    inventory = load_inventory()

    normalized_items = []
    seen_part_numbers = set()

    # --------------------------------------------------------
    # 실제 차감 전에 모든 요청 검증
    # --------------------------------------------------------
    for request_item in items:

        part_no = str(
            request_item.get(
                "part_no",
                "",
            )
        ).strip()

        try:
            quantity = int(
                request_item.get(
                    "quantity",
                    0,
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            quantity = 0

        if not part_no:
            return {
                "success": False,
                "message": "품번이 없는 항목이 있습니다.",
            }

        # 같은 품번이 한 요청 안에 중복되어 있으면
        # 의도치 않은 이중 차감을 막기 위해 중단
        if part_no in seen_part_numbers:
            return {
                "success": False,
                "message": (
                    f"중복 품번이 감지되었습니다: {part_no}. "
                    "재고 차감을 중단합니다."
                ),
            }

        seen_part_numbers.add(
            part_no
        )

        if quantity <= 0:
            return {
                "success": False,
                "message": (
                    f"{part_no}의 차감 수량이 "
                    "올바르지 않습니다."
                ),
            }

        if part_no not in inventory:
            return {
                "success": False,
                "message": (
                    f"등록되지 않은 품번입니다: "
                    f"{part_no}"
                ),
            }

        current_stock = int(
            inventory[
                part_no
            ].get(
                "stock",
                0,
            )
        )

        if current_stock < 0:
            return {
                "success": False,
                "message": (
                    f"{part_no}의 현재 재고값이 "
                    "비정상입니다."
                ),
            }

        if quantity > current_stock:
            return {
                "success": False,
                "message": (
                    f"{part_no} 재고 부족: "
                    f"현재 {current_stock}개 / "
                    f"요청 {quantity}개"
                ),
            }

        normalized_items.append(
            {
                "part_no": part_no,
                "quantity": quantity,
            }
        )

    # --------------------------------------------------------
    # 검증이 전부 끝난 뒤 실제 차감
    # --------------------------------------------------------
    changes = []

    for item in normalized_items:

        part_no = item[
            "part_no"
        ]

        quantity = item[
            "quantity"
        ]

        before = int(
            inventory[
                part_no
            ][
                "stock"
            ]
        )

        after = (
            before
            - quantity
        )

        inventory[
            part_no
        ][
            "stock"
        ] = after

        changes.append(
            {
                "part_no": part_no,
                "before": before,
                "used": quantity,
                "after": after,
            }
        )

    # --------------------------------------------------------
    # 실제 inventory.json 저장
    # --------------------------------------------------------
    save_inventory(
        inventory
    )

    # --------------------------------------------------------
    # 이 작업이 정확히 무엇을 차감했는지 기록
    # --------------------------------------------------------
    transaction = {
        "work_id": work_id,
        "processed_at": datetime.now().isoformat(
            timespec="seconds"
        ),
        "changes": changes,
    }

    completed_ids.append(
        work_id
    )

    transaction_history.append(
        transaction
    )

    transactions[
        "completed_work_ids"
    ] = completed_ids

    transactions[
        "transactions"
    ] = transaction_history

    _write_json(
        TRANSACTION_PATH,
        transactions,
    )

    # 터미널에서도 바로 확인 가능
    print()
    print("======================================")
    print("재고 차감 완료")
    print("작업 ID :", work_id)

    for change in changes:
        print(
            f"{change['part_no']} : "
            f"{change['before']} "
            f"-> {change['after']} "
            f"(-{change['used']})"
        )

    print("======================================")
    print()

    return {
        "success": True,
        "already_processed": False,
        "message": "재고 차감 완료",
        "transaction": transaction,
        "changes": changes,
        "data": get_all_inventory(),
    }