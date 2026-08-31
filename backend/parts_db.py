"""
부품 마스터 데이터베이스.
REVIEW 화면 수정값 검증용.

재고(stock)는 여기서 관리하지 않고,
품번/품명/규격/Tray의 고정 매칭만 관리한다.
"""

PARTS_DB = [
    {"part_no": "B001", "name": "육각볼트",     "spec": "M6X20", "tray": 1},
    {"part_no": "B002", "name": "육각렌치볼트", "spec": "M5X15", "tray": 2},
    {"part_no": "S001", "name": "십자머리나사", "spec": "M4X12", "tray": 3},
    {"part_no": "N001", "name": "육각너트",     "spec": "M6",    "tray": 4},
    {"part_no": "W001", "name": "평와셔",       "spec": "M6",    "tray": 5},
    {"part_no": "W002", "name": "스프링와셔",   "spec": "M6",    "tray": 6},
]


def _normalize(value: str) -> str:
    return (
        str(value)
        .strip()
        .upper()
        .replace(" ", "")
    )


def find_part(part_no: str, name: str, spec: str):
    target = (
        _normalize(part_no),
        _normalize(name),
        _normalize(spec),
    )

    for part in PARTS_DB:
        current = (
            _normalize(part["part_no"]),
            _normalize(part["name"]),
            _normalize(part["spec"]),
        )

        if current == target:
            return part.copy()

    return None
