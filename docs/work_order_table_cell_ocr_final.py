from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import cv2
import easyocr
import numpy as np
from rapidfuzz import fuzz


# ============================================================
# Ubuntu C920 - 작업지시서 표 전용 OCR (Standalone)
#
# 대상 표:
#   No. | 품번 | 품명 | 규격 / 사양 | 수량 | 단위
#
# 사용 방식:
#   1) 작업지시서는 정상 방향으로 놓는다.
#   2) 카메라를 가까이 가져가서 위 표 부분만 화면에 크게 보이게 한다.
#   3) 화면의 초록색 가이드 박스 안에 표 전체를 맞춘다.
#   4) S를 누르면 표 영역만 잘라 OCR한다.
#
# 기존 backend와 완전히 독립된 테스트 코드.
# ============================================================


# ------------------------------------------------------------
# Camera
# ------------------------------------------------------------

CAMERA_DEVICE = "/dev/video0"
WIDTH = 1920
HEIGHT = 1080
FPS = 30
FOURCC = "MJPG"

AUTOFOCUS = False
FOCUS_ABSOLUTE = 35


# ------------------------------------------------------------
# OCR / output
# ------------------------------------------------------------

OCR_LANGUAGES = ["ko", "en"]

OUTPUT_DIR = Path("work_order_table_ocr_output")
RAW_PATH = OUTPUT_DIR / "01_raw.jpg"
TABLE_PATH = OUTPUT_DIR / "02_table_roi.jpg"
DEBUG_PATH = OUTPUT_DIR / "03_cells_debug.jpg"

# 화면에서 실제 OCR할 표 영역.
# 카메라 화면 안에 표가 더 꽉 차게 놓을수록 좋음.
ROI_LEFT = 0.05
ROI_TOP = 0.05
ROI_RIGHT = 0.95
ROI_BOTTOM = 0.95

# 표 열 비율
# 첨부한 표 구조를 기준으로:
# No. | 품번 | 품명 | 규격/사양 | 수량 | 단위
COLUMN_RATIOS = [
    0.000,  # left
    0.057,  # No. 끝
    0.208,  # 품번 끝
    0.511,  # 품명 끝
    0.736,  # 규격/사양 끝
    0.860,  # 수량 끝
    1.000,  # 단위 끝
]

# 헤더 높이 비율.
# 아래 나머지 영역은 데이터 행으로 나눈다.
HEADER_RATIO = 0.19

# 현재 작업지시서에서 최대 몇 행까지 읽을지
MAX_DATA_ROWS = 6

# 실제 프로젝트 등록 품명
PART_NAMES = [
    "코너 브라켓",
    "L형 브라켓",
    "T 너트",
    "플랜지 너트",
    "렌치 볼트",
    "T 볼트",
]

NAME_MATCH_THRESHOLD = 55.0


# ============================================================
# Camera controls
# ============================================================

def run_v4l2(*args: str) -> None:
    cmd = ["v4l2-ctl", "-d", CAMERA_DEVICE, *args]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )

        if result.stdout.strip():
            print(result.stdout.strip())

        if result.stderr.strip():
            print("[v4l2-ctl]", result.stderr.strip())

    except FileNotFoundError:
        print(
            "[WARNING] v4l2-ctl이 없습니다. "
            "Ubuntu에서 sudo apt install v4l-utils"
        )


def configure_focus() -> None:
    if AUTOFOCUS:
        run_v4l2(
            "--set-ctrl=focus_automatic_continuous=1"
        )
    else:
        run_v4l2(
            "--set-ctrl=focus_automatic_continuous=0"
        )
        run_v4l2(
            f"--set-ctrl=focus_absolute={FOCUS_ABSOLUTE}"
        )

    run_v4l2(
        "--get-ctrl=focus_automatic_continuous"
    )
    run_v4l2(
        "--get-ctrl=focus_absolute"
    )


def open_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(
        CAMERA_DEVICE,
        cv2.CAP_V4L2,
    )

    if not cap.isOpened():
        raise RuntimeError(
            f"카메라를 열 수 없습니다: {CAMERA_DEVICE}"
        )

    cap.set(
        cv2.CAP_PROP_FOURCC,
        cv2.VideoWriter_fourcc(*FOURCC),
    )
    cap.set(
        cv2.CAP_PROP_FRAME_WIDTH,
        WIDTH,
    )
    cap.set(
        cv2.CAP_PROP_FRAME_HEIGHT,
        HEIGHT,
    )
    cap.set(
        cv2.CAP_PROP_FPS,
        FPS,
    )

    print(
        "[CAMERA] "
        f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x"
        f"{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
        f"{cap.get(cv2.CAP_PROP_FPS):.1f} FPS"
    )

    return cap


# ============================================================
# ROI / cells
# ============================================================

def get_table_roi_coords(
    frame: np.ndarray,
) -> tuple[int, int, int, int]:
    h, w = frame.shape[:2]

    return (
        int(w * ROI_LEFT),
        int(h * ROI_TOP),
        int(w * ROI_RIGHT),
        int(h * ROI_BOTTOM),
    )


def crop_table_roi(
    frame: np.ndarray,
) -> np.ndarray:
    x1, y1, x2, y2 = get_table_roi_coords(frame)
    return frame[y1:y2, x1:x2].copy()


def split_table_cells(
    table: np.ndarray,
) -> tuple[list[dict], np.ndarray]:
    """
    표를 열/행 단위로 잘라낸다.

    첫 행 = 헤더
    이후 = 데이터 행

    첨부한 양식처럼 표 전체가 ROI에 들어오는 것을 전제로 한다.
    """
    h, w = table.shape[:2]

    debug = table.copy()

    # 세로 열 경계
    xs = [
        int(w * ratio)
        for ratio in COLUMN_RATIOS
    ]

    # 헤더 + 데이터영역
    header_y = int(h * HEADER_RATIO)

    # 실제 데이터행은 양식에서 거의 동일 높이라고 가정.
    usable_h = h - header_y
    row_h = usable_h / MAX_DATA_ROWS

    ys = [0, header_y]
    for i in range(1, MAX_DATA_ROWS + 1):
        ys.append(
            min(
                h,
                int(header_y + row_h * i),
            )
        )

    # debug lines
    for x in xs:
        cv2.line(
            debug,
            (x, 0),
            (x, h),
            (0, 255, 0),
            2,
        )

    for y in ys:
        cv2.line(
            debug,
            (0, y),
            (w, y),
            (0, 255, 0),
            2,
        )

    headers = [
        "no",
        "part_no",
        "name",
        "spec",
        "quantity",
        "unit",
    ]

    cells: list[dict] = []

    # 데이터 행만 추출
    for row_index in range(MAX_DATA_ROWS):
        y1 = int(header_y + row_h * row_index)
        y2 = int(header_y + row_h * (row_index + 1))

        y1 = max(0, min(y1, h))
        y2 = max(0, min(y2, h))

        if y2 <= y1:
            continue

        for col_index, field in enumerate(headers):
            x1 = xs[col_index]
            x2 = xs[col_index + 1]

            # 표 선 자체를 OCR에서 덜 보이게 약간 안쪽으로 crop
            pad_x = max(2, int((x2 - x1) * 0.05))
            pad_y = max(2, int((y2 - y1) * 0.08))

            cx1 = min(x2, x1 + pad_x)
            cx2 = max(cx1 + 1, x2 - pad_x)
            cy1 = min(y2, y1 + pad_y)
            cy2 = max(cy1 + 1, y2 - pad_y)

            crop = table[
                cy1:cy2,
                cx1:cx2,
            ].copy()

            cells.append(
                {
                    "row": row_index + 1,
                    "field": field,
                    "image": crop,
                    "rect": (
                        cx1,
                        cy1,
                        cx2,
                        cy2,
                    ),
                }
            )

    return cells, debug


# ============================================================
# OCR preprocessing
# ============================================================

def preprocess_cell(
    image: np.ndarray,
) -> np.ndarray:
    if image.size == 0:
        return image

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )
    gray = clahe.apply(gray)

    gray = cv2.resize(
        gray,
        None,
        fx=2.0,
        fy=2.0,
        interpolation=cv2.INTER_CUBIC,
    )

    return gray


def read_cell(
    reader: easyocr.Reader,
    image: np.ndarray,
    *,
    allowlist: str | None = None,
) -> tuple[str, float]:
    if image.size == 0:
        return "", 0.0

    processed = preprocess_cell(image)

    kwargs = {
        "detail": 1,
        "paragraph": False,
        "decoder": "beamsearch",
        "text_threshold": 0.35,
        "low_text": 0.20,
        "link_threshold": 0.25,
        "mag_ratio": 1.5,
    }

    if allowlist:
        kwargs["allowlist"] = allowlist

    results = reader.readtext(
        processed,
        **kwargs,
    )

    if not results:
        return "", 0.0

    # 같은 셀 안에서 여러 OCR box가 나오면 x순 정렬해서 병합
    ordered = sorted(
        results,
        key=lambda item: min(
            p[0] for p in item[0]
        ),
    )

    text = " ".join(
        str(item[1]).strip()
        for item in ordered
        if str(item[1]).strip()
    )

    confidence = float(
        np.mean(
            [
                float(item[2])
                for item in ordered
            ]
        )
    )

    return text.strip(), confidence


# ============================================================
# Field normalization
# ============================================================

def normalize_part_no(text: str) -> str:
    text = (
        text.replace(" ", "")
        .replace("-", "")
        .upper()
    )

    # 품번은 영문 + 숫자만
    return "".join(
        re.findall(
            r"[A-Z0-9]+",
            text,
        )
    )


def normalize_name(text: str) -> str:
    return "".join(
        re.findall(
            r"[가-힣A-Za-z0-9]+",
            text or "",
        )
    ).upper()


def match_part_name(
    text: str,
) -> tuple[str | None, float]:
    target = normalize_name(text)

    if not target:
        return None, 0.0

    best_name = None
    best_score = 0.0

    for name in PART_NAMES:
        candidate = normalize_name(name)

        score = max(
            fuzz.ratio(
                target,
                candidate,
            ),
            fuzz.partial_ratio(
                target,
                candidate,
            ),
        )

        if score > best_score:
            best_score = float(score)
            best_name = name

    if best_score < NAME_MATCH_THRESHOLD:
        return None, best_score

    return best_name, best_score


def normalize_quantity(text: str) -> int | None:
    match = re.search(
        r"\d+",
        text or "",
    )

    if not match:
        return None

    value = int(match.group())

    if value <= 0 or value > 9999:
        return None

    return value


def normalize_spec(text: str) -> str:
    return re.sub(
        r"\s+",
        "",
        text or "",
    ).upper()


# ============================================================
# Table OCR
# ============================================================

def recognize_table(
    reader: easyocr.Reader,
    table: np.ndarray,
) -> list[dict]:
    cells, debug = split_table_cells(table)

    cv2.imwrite(
        str(DEBUG_PATH),
        debug,
    )

    rows: dict[int, dict] = {}

    for cell in cells:
        row_index = cell["row"]
        field = cell["field"]

        if row_index not in rows:
            rows[row_index] = {
                "row": row_index,
                "no": "",
                "part_no": "",
                "name_raw": "",
                "name": None,
                "name_score": 0.0,
                "spec": "",
                "quantity": None,
                "unit": "",
            }

        image = cell["image"]

        if field == "no":
            text, confidence = read_cell(
                reader,
                image,
                allowlist="0123456789",
            )

        elif field == "part_no":
            text, confidence = read_cell(
                reader,
                image,
                allowlist=(
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "abcdefghijklmnopqrstuvwxyz"
                    "0123456789"
                ),
            )

        elif field == "quantity":
            text, confidence = read_cell(
                reader,
                image,
                allowlist="0123456789",
            )

        elif field == "unit":
            text, confidence = read_cell(
                reader,
                image,
                allowlist=(
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "abcdefghijklmnopqrstuvwxyz"
                ),
            )

        else:
            # 품명 / 규격은 한글+영문 모두 허용
            text, confidence = read_cell(
                reader,
                image,
            )

        print(
            f"[ROW {row_index}] "
            f"{field:<8} "
            f"raw={text!r} "
            f"conf={confidence:.3f}"
        )

        if field == "no":
            rows[row_index]["no"] = re.sub(
                r"\D",
                "",
                text,
            )

        elif field == "part_no":
            rows[row_index]["part_no"] = (
                normalize_part_no(text)
            )

        elif field == "name":
            rows[row_index]["name_raw"] = text

            matched, score = match_part_name(
                text
            )

            rows[row_index]["name"] = matched
            rows[row_index]["name_score"] = round(
                score,
                1,
            )

        elif field == "spec":
            rows[row_index]["spec"] = (
                normalize_spec(text)
            )

        elif field == "quantity":
            rows[row_index]["quantity"] = (
                normalize_quantity(text)
            )

        elif field == "unit":
            rows[row_index]["unit"] = (
                text.strip().upper()
            )

    final: list[dict] = []

    for row_index in sorted(rows):
        row = rows[row_index]

        # 빈 행 제거.
        # 품번/품명/규격/수량 중 아무것도 없으면 무시.
        has_content = any(
            [
                row["part_no"],
                row["name_raw"],
                row["spec"],
                row["quantity"] is not None,
            ]
        )

        if not has_content:
            continue

        final.append(row)

    print(
        "\n"
        "============================================================"
    )
    print(
        "FINAL TABLE OCR"
    )
    print(
        "============================================================"
    )

    if not final:
        print("인식된 데이터 행이 없습니다.")
        return []

    for row in final:
        print(
            f"ROW {row['row']:02d} | "
            f"No={row['no'] or '-'} | "
            f"품번={row['part_no'] or '-'} | "
            f"품명={row['name'] or row['name_raw'] or '-'} | "
            f"규격={row['spec'] or '-'} | "
            f"수량={row['quantity'] if row['quantity'] is not None else '-'} | "
            f"단위={row['unit'] or '-'}"
        )

    print(
        "\n[실제 자동조달에서 사용할 핵심 값]"
    )

    for row in final:
        if (
            row["name"] is not None
            and row["quantity"] is not None
        ):
            print(
                f"품명={row['name']} | "
                f"수량={row['quantity']} | "
                f"name_score={row['name_score']}"
            )

    return final


# ============================================================
# Main
# ============================================================

def main() -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    print(
        "[OCR] EasyOCR Korean + English loading..."
    )

    reader = easyocr.Reader(
        OCR_LANGUAGES,
        gpu=False,
    )

    configure_focus()
    cap = open_camera()

    time.sleep(1.5)

    print()
    print(
        "============================================================"
    )
    print(
        "작업지시서를 정상 방향으로 놓으세요."
    )
    print(
        "카메라를 가까이 가져가서"
    )
    print(
        "No. / 품번 / 품명 / 규격·사양 / 수량 / 단위"
    )
    print(
        "표 부분만 초록색 가이드 안에 크게 맞추세요."
    )
    print()
    print(
        "S = 표 촬영 + 셀별 OCR"
    )
    print(
        "Q = 종료"
    )
    print(
        "============================================================"
    )

    try:
        while True:
            ok, frame = cap.read()

            if not ok or frame is None:
                print(
                    "[CAMERA] frame read failed"
                )
                time.sleep(0.1)
                continue

            preview = frame.copy()

            x1, y1, x2, y2 = (
                get_table_roi_coords(frame)
            )

            cv2.rectangle(
                preview,
                (x1, y1),
                (x2, y2),
                (0, 255, 0),
                3,
            )

            cv2.putText(
                preview,
                "Fit ONLY the table inside green box | S: OCR | Q: quit",
                (30, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.85,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(
                "C920 - Work Order Table OCR",
                preview,
            )

            key = cv2.waitKey(1) & 0xFF

            if key in (
                ord("q"),
                ord("Q"),
            ):
                break

            if key in (
                ord("s"),
                ord("S"),
            ):
                table = crop_table_roi(
                    frame
                )

                cv2.imwrite(
                    str(RAW_PATH),
                    frame,
                )

                cv2.imwrite(
                    str(TABLE_PATH),
                    table,
                )

                print(
                    "\n[CAPTURE]"
                )
                print(
                    "raw:",
                    RAW_PATH.resolve(),
                )
                print(
                    "table:",
                    TABLE_PATH.resolve(),
                )

                cv2.imshow(
                    "Captured Table ROI",
                    table,
                )

                recognize_table(
                    reader,
                    table,
                )

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
