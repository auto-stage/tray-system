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
# FINAL standalone Ubuntu C920 -> Work Order OCR test
#
# This file is intentionally independent from the main backend.
# It does NOT import server.py, work_order_ocr.py, material flow,
# or any other tray-system service.
#
# Pipeline:
#   C920
#   -> 1920x1080 MJPG
#   -> autofocus OFF / focus_absolute=35
#   -> user places paper 90° counterclockwise
#   -> automatic document rectangle detection
#   -> perspective transform
#   -> orientation correction (90° clockwise if needed)
#   -> OCR preprocessing
#   -> EasyOCR Korean + English
#   -> known part-name fuzzy matching
#   -> quantity extraction
#
# Controls:
#   S = capture + OCR
#   Q = quit
# ============================================================


# -----------------------------
# Camera configuration
# -----------------------------

CAMERA_DEVICE = "/dev/video0"
WIDTH = 1920
HEIGHT = 1080
FPS = 30
FOURCC = "MJPG"

AUTOFOCUS = False
FOCUS_ABSOLUTE = 35


# -----------------------------
# Output files
# -----------------------------

OUTPUT_DIR = Path("work_order_ocr_output")

RAW_IMAGE_PATH = OUTPUT_DIR / "01_raw_capture.jpg"
DOCUMENT_IMAGE_PATH = OUTPUT_DIR / "02_document_warped.jpg"
UPRIGHT_IMAGE_PATH = OUTPUT_DIR / "03_document_upright.jpg"
PREPROCESSED_IMAGE_PATH = OUTPUT_DIR / "04_ocr_preprocessed.jpg"
DEBUG_CONTOUR_PATH = OUTPUT_DIR / "05_document_detection_debug.jpg"


# -----------------------------
# OCR configuration
# -----------------------------

OCR_LANGUAGES = ["ko", "en"]

# Current project names.
# This standalone test uses name + quantity only.
PART_NAMES = [
    "코너 브라켓",
    "L형 브라켓",
    "T 너트",
    "플랜지 너트",
    "렌치 볼트",
    "T 볼트",
]

NAME_MATCH_THRESHOLD = 58.0

# Ignore contours smaller than this portion of the full camera frame.
MIN_DOCUMENT_AREA_RATIO = 0.18

# Maximum number of OCR candidates kept per row.
MAX_ROW_TOKENS = 20


# ============================================================
# V4L2 camera controls
# ============================================================

def run_v4l2_control(*args: str) -> None:
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
            "[WARNING] v4l2-ctl not found. "
            "Install it with: sudo apt install v4l-utils"
        )


def configure_focus() -> None:
    print("[CAMERA] configuring focus...")

    if AUTOFOCUS:
        run_v4l2_control(
            "--set-ctrl=focus_automatic_continuous=1"
        )
    else:
        run_v4l2_control(
            "--set-ctrl=focus_automatic_continuous=0"
        )
        run_v4l2_control(
            f"--set-ctrl=focus_absolute={FOCUS_ABSOLUTE}"
        )

    run_v4l2_control(
        "--get-ctrl=focus_automatic_continuous"
    )
    run_v4l2_control(
        "--get-ctrl=focus_absolute"
    )


def open_camera() -> cv2.VideoCapture:
    cap = cv2.VideoCapture(
        CAMERA_DEVICE,
        cv2.CAP_V4L2,
    )

    if not cap.isOpened():
        raise RuntimeError(
            f"Could not open camera: {CAMERA_DEVICE}"
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

    actual_width = int(
        cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    )
    actual_height = int(
        cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    )
    actual_fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    print(
        "[CAMERA] OPEN OK | "
        f"{actual_width}x{actual_height} | "
        f"{actual_fps:.1f} FPS"
    )

    return cap


# ============================================================
# Document detection / perspective transform
# ============================================================

def order_points(points: np.ndarray) -> np.ndarray:
    """
    Return points in:
      top-left, top-right, bottom-right, bottom-left
    order.
    """
    pts = np.asarray(
        points,
        dtype=np.float32,
    ).reshape(4, 2)

    rect = np.zeros(
        (4, 2),
        dtype=np.float32,
    )

    sums = pts.sum(axis=1)
    diffs = np.diff(
        pts,
        axis=1,
    ).reshape(-1)

    rect[0] = pts[np.argmin(sums)]
    rect[2] = pts[np.argmax(sums)]
    rect[1] = pts[np.argmin(diffs)]
    rect[3] = pts[np.argmax(diffs)]

    return rect


def four_point_transform(
    image: np.ndarray,
    points: np.ndarray,
) -> np.ndarray:
    rect = order_points(points)

    tl, tr, br, bl = rect

    width_a = np.linalg.norm(
        br - bl
    )
    width_b = np.linalg.norm(
        tr - tl
    )

    max_width = int(
        max(width_a, width_b)
    )

    height_a = np.linalg.norm(
        tr - br
    )
    height_b = np.linalg.norm(
        tl - bl
    )

    max_height = int(
        max(height_a, height_b)
    )

    max_width = max(
        max_width,
        1,
    )
    max_height = max(
        max_height,
        1,
    )

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [
                max_width - 1,
                max_height - 1,
            ],
            [0, max_height - 1],
        ],
        dtype=np.float32,
    )

    matrix = cv2.getPerspectiveTransform(
        rect,
        destination,
    )

    return cv2.warpPerspective(
        image,
        matrix,
        (
            max_width,
            max_height,
        ),
    )


def find_document_quad(
    image: np.ndarray,
) -> tuple[np.ndarray | None, np.ndarray]:
    """
    Detect the largest plausible 4-corner paper contour.

    Returns:
        quad: shape (4, 2), or None
        debug_image: contour visualization
    """
    debug_image = image.copy()

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    # Reduce noise while preserving large paper edges.
    blurred = cv2.GaussianBlur(
        gray,
        (5, 5),
        0,
    )

    edges = cv2.Canny(
        blurred,
        50,
        150,
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (5, 5),
    )

    edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=2,
    )

    contours, _ = cv2.findContours(
        edges,
        cv2.RETR_LIST,
        cv2.CHAIN_APPROX_SIMPLE,
    )

    frame_area = (
        image.shape[0]
        * image.shape[1]
    )

    contours = sorted(
        contours,
        key=cv2.contourArea,
        reverse=True,
    )

    selected = None

    for contour in contours[:30]:
        area = cv2.contourArea(
            contour
        )

        if area < (
            frame_area
            * MIN_DOCUMENT_AREA_RATIO
        ):
            continue

        perimeter = cv2.arcLength(
            contour,
            True,
        )

        approx = cv2.approxPolyDP(
            contour,
            0.02 * perimeter,
            True,
        )

        if len(approx) != 4:
            continue

        if not cv2.isContourConvex(
            approx
        ):
            continue

        selected = approx.reshape(
            4,
            2,
        )
        break

    if selected is not None:
        cv2.polylines(
            debug_image,
            [
                selected.astype(
                    np.int32
                )
            ],
            True,
            (0, 255, 0),
            5,
        )

        for index, point in enumerate(
            order_points(selected)
        ):
            px, py = (
                int(point[0]),
                int(point[1]),
            )

            cv2.circle(
                debug_image,
                (px, py),
                12,
                (0, 0, 255),
                -1,
            )

            cv2.putText(
                debug_image,
                str(index + 1),
                (
                    px + 10,
                    py - 10,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (255, 0, 0),
                3,
                cv2.LINE_AA,
            )

    return selected, debug_image


def extract_document(
    frame: np.ndarray,
) -> tuple[np.ndarray, bool]:
    quad, debug_image = find_document_quad(
        frame
    )

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cv2.imwrite(
        str(DEBUG_CONTOUR_PATH),
        debug_image,
    )

    if quad is None:
        print(
            "[DOCUMENT] paper rectangle not detected. "
            "Using the full frame as fallback."
        )
        return frame.copy(), False

    warped = four_point_transform(
        frame,
        quad,
    )

    print(
        "[DOCUMENT] paper detected and perspective corrected | "
        f"shape={warped.shape}"
    )

    return warped, True


# ============================================================
# Orientation
# ============================================================

def rotate_to_upright(
    document: np.ndarray,
) -> np.ndarray:
    """
    The user physically places the work order
    90 degrees counterclockwise.

    If the extracted document is landscape,
    rotate it 90 degrees clockwise to restore
    the normal portrait orientation.

    If it is already portrait, leave it unchanged.
    """
    height, width = document.shape[:2]

    if width > height:
        return cv2.rotate(
            document,
            cv2.ROTATE_90_CLOCKWISE,
        )

    return document


# ============================================================
# OCR preprocessing
# ============================================================

def preprocess_for_ocr(
    image: np.ndarray,
) -> np.ndarray:
    """
    Conservative preprocessing for printed / handwritten text:
      grayscale
      contrast enhancement
      mild denoise
      adaptive sharpening
      upscale
    """
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    clahe = cv2.createCLAHE(
        clipLimit=2.0,
        tileGridSize=(8, 8),
    )

    contrast = clahe.apply(
        gray
    )

    denoised = cv2.bilateralFilter(
        contrast,
        7,
        40,
        40,
    )

    gaussian = cv2.GaussianBlur(
        denoised,
        (0, 0),
        1.2,
    )

    sharpened = cv2.addWeighted(
        denoised,
        1.5,
        gaussian,
        -0.5,
        0,
    )

    # Keep enough detail for Korean glyphs.
    upscaled = cv2.resize(
        sharpened,
        None,
        fx=1.5,
        fy=1.5,
        interpolation=cv2.INTER_CUBIC,
    )

    return upscaled


# ============================================================
# OCR result utilities
# ============================================================

def normalize_name(
    text: str,
) -> str:
    if not text:
        return ""

    return "".join(
        re.findall(
            r"[가-힣A-Za-z0-9]+",
            text,
        )
    ).upper()


def bbox_points(
    box,
) -> np.ndarray:
    return np.asarray(
        box,
        dtype=np.float32,
    )


def bbox_center(
    box,
) -> tuple[float, float]:
    points = bbox_points(
        box
    )

    return (
        float(
            points[:, 0].mean()
        ),
        float(
            points[:, 1].mean()
        ),
    )


def bbox_height(
    box,
) -> float:
    points = bbox_points(
        box
    )

    return float(
        points[:, 1].max()
        - points[:, 1].min()
    )


def build_detection_list(
    results,
) -> list[dict]:
    detections = []

    for box, text, confidence in results:
        points = bbox_points(
            box
        )

        detections.append(
            {
                "box": box,
                "text": str(text).strip(),
                "confidence": float(confidence),
                "center": bbox_center(box),
                "height": bbox_height(box),
                "left_x": float(
                    points[:, 0].min()
                ),
                "right_x": float(
                    points[:, 0].max()
                ),
                "top_y": float(
                    points[:, 1].min()
                ),
                "bottom_y": float(
                    points[:, 1].max()
                ),
            }
        )

    return detections


def group_rows(
    detections: list[dict],
) -> list[list[dict]]:
    """
    Group OCR boxes into approximate text rows.
    """
    if not detections:
        return []

    sorted_items = sorted(
        detections,
        key=lambda item: (
            item["center"][1],
            item["center"][0],
        ),
    )

    rows: list[list[dict]] = []

    for item in sorted_items:
        placed = False

        for row in rows:
            row_y = float(
                np.mean(
                    [
                        token["center"][1]
                        for token in row
                    ]
                )
            )

            row_height = max(
                np.mean(
                    [
                        max(
                            token["height"],
                            1.0,
                        )
                        for token in row
                    ]
                ),
                1.0,
            )

            if abs(
                item["center"][1]
                - row_y
            ) <= (
                max(
                    item["height"],
                    row_height,
                )
                * 0.75
            ):
                row.append(
                    item
                )
                placed = True
                break

        if not placed:
            rows.append(
                [item]
            )

    for row in rows:
        row.sort(
            key=lambda item: (
                item["center"][0]
            )
        )

        if len(row) > MAX_ROW_TOKENS:
            del row[
                MAX_ROW_TOKENS:
            ]

    return rows


def row_text(
    row: list[dict],
) -> str:
    return " ".join(
        token["text"]
        for token in row
        if token["text"]
    )


def best_part_match(
    text: str,
) -> tuple[str | None, float]:
    normalized = normalize_name(
        text
    )

    if not normalized:
        return None, 0.0

    best_name = None
    best_score = 0.0

    for part_name in PART_NAMES:
        candidate = normalize_name(
            part_name
        )

        score = max(
            fuzz.ratio(
                normalized,
                candidate,
            ),
            fuzz.partial_ratio(
                normalized,
                candidate,
            ),
            fuzz.token_set_ratio(
                normalized,
                candidate,
            ),
        )

        if score > best_score:
            best_name = part_name
            best_score = float(
                score
            )

    return best_name, best_score


def extract_quantity_from_row(
    row: list[dict],
    matched_name: str,
) -> int | None:
    """
    Quantity extraction strategy:
      1) find numeric OCR tokens on the same row
      2) prefer a plausible integer near the right side
      3) ignore obvious row-number values when possible
    """
    numeric_tokens = []

    for token in row:
        raw = token["text"].strip()

        # Accept forms like "3", "03", "3EA", "3 EA".
        match = re.fullmatch(
            r"\s*(\d{1,4})\s*(?:EA)?\s*",
            raw,
            flags=re.IGNORECASE,
        )

        if not match:
            continue

        value = int(
            match.group(1)
        )

        if value <= 0 or value > 999:
            continue

        numeric_tokens.append(
            (
                token["center"][0],
                value,
                raw,
            )
        )

    if not numeric_tokens:
        # Try extracting embedded numbers from tokens.
        for token in row:
            raw = token["text"].strip()

            matches = re.findall(
                r"(?<![A-Za-z가-힣])(\d{1,3})(?![A-Za-z가-힣])",
                raw,
            )

            for found in matches:
                value = int(found)

                if 0 < value <= 999:
                    numeric_tokens.append(
                        (
                            token["center"][0],
                            value,
                            raw,
                        )
                    )

    if not numeric_tokens:
        return None

    # Quantity columns in work orders are commonly toward the right.
    numeric_tokens.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    # If the only rightmost integer is a likely row number 1..6
    # and another numeric value exists, prefer the other value.
    if len(numeric_tokens) >= 2:
        for _, value, _ in numeric_tokens:
            if value not in range(
                1,
                len(PART_NAMES) + 1,
            ):
                return value

    return numeric_tokens[0][1]


def analyze_ocr_results(
    results,
) -> list[dict]:
    detections = build_detection_list(
        results
    )

    print(
        "\n========== RAW OCR =========="
    )

    for item in detections:
        print(
            f"{item['text']!r} "
            f"conf={item['confidence']:.3f}"
        )

    rows = group_rows(
        detections
    )

    print(
        "\n========== OCR ROWS =========="
    )

    for index, row in enumerate(
        rows,
        start=1,
    ):
        print(
            f"ROW {index:02d}: "
            f"{row_text(row)!r}"
        )

    matches: dict[str, dict] = {}

    for row in rows:
        text = row_text(
            row
        )

        part_name, score = best_part_match(
            text
        )

        if (
            part_name is None
            or score < NAME_MATCH_THRESHOLD
        ):
            continue

        quantity = extract_quantity_from_row(
            row,
            part_name,
        )

        current = matches.get(
            part_name
        )

        if (
            current is None
            or score > current["name_score"]
        ):
            matches[
                part_name
            ] = {
                "name": part_name,
                "quantity": quantity,
                "name_score": round(
                    score,
                    1,
                ),
                "ocr_row": text,
            }

    final = list(
        matches.values()
    )

    final.sort(
        key=lambda item: (
            PART_NAMES.index(
                item["name"]
            )
        )
    )

    print(
        "\n========== FINAL NAME + QUANTITY =========="
    )

    if not final:
        print(
            "Known part name was not detected."
        )
        return []

    for item in final:
        print(
            f"name={item['name']} | "
            f"quantity={item['quantity']} | "
            f"name_score={item['name_score']} | "
            f"ocr_row={item['ocr_row']!r}"
        )

    return final


# ============================================================
# EasyOCR
# ============================================================

def run_ocr(
    reader: easyocr.Reader,
    image: np.ndarray,
) -> list[dict]:
    print(
        "\n[OCR] EasyOCR Korean + English..."
    )

    results = reader.readtext(
        image,
        detail=1,
        paragraph=False,
        decoder="beamsearch",
        text_threshold=0.45,
        low_text=0.25,
        link_threshold=0.25,
        mag_ratio=1.5,
    )

    return analyze_ocr_results(
        results
    )


# ============================================================
# Capture + full pipeline
# ============================================================

def process_capture(
    reader: easyocr.Reader,
    frame: np.ndarray,
) -> None:
    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    cv2.imwrite(
        str(RAW_IMAGE_PATH),
        frame,
    )

    document, detected = extract_document(
        frame
    )

    cv2.imwrite(
        str(DOCUMENT_IMAGE_PATH),
        document,
    )

    upright = rotate_to_upright(
        document
    )

    cv2.imwrite(
        str(UPRIGHT_IMAGE_PATH),
        upright,
    )

    preprocessed = preprocess_for_ocr(
        upright
    )

    cv2.imwrite(
        str(PREPROCESSED_IMAGE_PATH),
        preprocessed,
    )

    print(
        "\n========== CAPTURE =========="
    )
    print(
        f"document_detected={detected}"
    )
    print(
        f"raw={RAW_IMAGE_PATH.resolve()}"
    )
    print(
        f"warped={DOCUMENT_IMAGE_PATH.resolve()}"
    )
    print(
        f"upright={UPRIGHT_IMAGE_PATH.resolve()}"
    )
    print(
        f"preprocessed={PREPROCESSED_IMAGE_PATH.resolve()}"
    )
    print(
        f"debug={DEBUG_CONTOUR_PATH.resolve()}"
    )

    cv2.imshow(
        "Detected / Warped Document",
        document,
    )

    cv2.imshow(
        "OCR Input Upright",
        upright,
    )

    run_ocr(
        reader,
        preprocessed,
    )


# ============================================================
# Main
# ============================================================

def main() -> None:
    print(
        "[OCR] loading EasyOCR model: Korean + English"
    )

    reader = easyocr.Reader(
        OCR_LANGUAGES,
        gpu=False,
    )

    configure_focus()

    cap = open_camera()

    # Allow exposure / stream to settle.
    time.sleep(
        1.5
    )

    print()
    print(
        "============================================================"
    )
    print(
        "WORK ORDER PLACEMENT"
    )
    print(
        "- Rotate the paper 90 degrees COUNTERCLOCKWISE."
    )
    print(
        "- Keep the entire paper visible inside the camera frame."
    )
    print(
        "- It does NOT need to perfectly fill the frame."
    )
    print(
        "- Leave a small margin around the paper."
    )
    print(
        "- Avoid strong shadows / reflections."
    )
    print()
    print(
        "S : capture -> document detect -> perspective -> OCR"
    )
    print(
        "Q : quit"
    )
    print(
        "============================================================"
    )

    try:
        while True:
            ok, frame = cap.read()

            if (
                not ok
                or frame is None
            ):
                print(
                    "[CAMERA] frame read failed"
                )
                time.sleep(
                    0.1
                )
                continue

            preview = frame.copy()

            # Simple center guide only.
            h, w = preview.shape[:2]

            margin_x = int(
                w * 0.08
            )
            margin_y = int(
                h * 0.08
            )

            cv2.rectangle(
                preview,
                (
                    margin_x,
                    margin_y,
                ),
                (
                    w - margin_x,
                    h - margin_y,
                ),
                (255, 255, 255),
                2,
            )

            cv2.putText(
                preview,
                "Place paper inside guide | S: OCR | Q: quit",
                (
                    30,
                    50,
                ),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.9,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(
                "C920 Work Order Camera",
                preview,
            )

            key = cv2.waitKey(
                1
            ) & 0xFF

            if key in (
                ord("q"),
                ord("Q"),
            ):
                break

            if key in (
                ord("s"),
                ord("S"),
            ):
                process_capture(
                    reader,
                    frame,
                )

    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
