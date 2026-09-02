import json
import os
import re
import warnings
import sys


import numpy as np
import pymupdf
import easyocr

from PIL import Image
from rapidfuzz import fuzz
from parts_db import get_ocr_part_db


warnings.filterwarnings("ignore")


# ============================================================
# UI 실제 진행상태 전달용
# server.py가 이 특수 로그를 읽어서 React에 전달한다.
# ============================================================
def emit_progress(step, message):
    print(
        f"__PROGRESS__:{step}:{message}",
        flush=True
    )



# ============================================================
# 1. 입력 파일
# ============================================================

# PDF 또는 이미지 파일명을 넣으면 됨
if len(sys.argv) >= 2:
    INPUT_PATH = sys.argv[1]
else:
    INPUT_PATH = "work_order.pdf"

# PDF를 OCR해야 할 경우 임시로 만드는 이미지
RENDERED_IMAGE = "work_order_page1.png"


# ============================================================
# 2. 부품 DB
# ============================================================

part_db = get_ocr_part_db()


# EasyOCR 모델은 필요할 때만 불러옴
reader = None


def get_reader():

    global reader

    if reader is None:

        print("EasyOCR 모델 로딩...")

        reader = easyocr.Reader(
            ['ko', 'en'],
            gpu=False
        )

    return reader


# ============================================================
# 3. 공통 OCR item 생성
# ============================================================

def make_item(
    text,
    x_left,
    y_top,
    x_right,
    y_bottom,
    confidence=1.0
):

    return {

        "text": str(text).strip(),

        "confidence": confidence,

        "x_left": float(x_left),
        "x_right": float(x_right),

        "y_top": float(y_top),
        "y_bottom": float(y_bottom),

        "x_center": (
            float(x_left) + float(x_right)
        ) / 2,

        "y_center": (
            float(y_top) + float(y_bottom)
        ) / 2
    }


# ============================================================
# 4. PDF 텍스트 직접 추출
# ============================================================

def extract_pdf_text(pdf_path):

    doc = pymupdf.open(pdf_path)

    page = doc[0]

    words = page.get_text(
        "words",
        sort=True
    )

    items = []


    for word in words:

        x0 = word[0]
        y0 = word[1]
        x1 = word[2]
        y1 = word[3]

        text = word[4]


        items.append(
            make_item(
                text,
                x0,
                y0,
                x1,
                y1,
                confidence=1.0
            )
        )


    width = page.rect.width
    height = page.rect.height

    doc.close()


    return (
        items,
        width,
        height
    )


# ============================================================
# 5. PDF → 이미지
# ============================================================

def render_pdf(pdf_path):

    doc = pymupdf.open(pdf_path)

    page = doc[0]


    # 3배 확대
    matrix = pymupdf.Matrix(
        3.0,
        3.0
    )


    pix = page.get_pixmap(
        matrix=matrix,
        alpha=False
    )


    pix.save(
        RENDERED_IMAGE
    )


    doc.close()


    return RENDERED_IMAGE


# ============================================================
# 6. EasyOCR 실행
# ============================================================

def run_easyocr(image_path):

    ocr_reader = get_reader()


    result = ocr_reader.readtext(

        image_path,

        detail=1,

        paragraph=False,

        # 작은 글자 검출을 조금 더 허용
        min_size=5,

        text_threshold=0.55,

        low_text=0.30,

        link_threshold=0.30
    )


    items = []


    for box, text, confidence in result:

        x_values = [
            point[0]
            for point in box
        ]

        y_values = [
            point[1]
            for point in box
        ]


        items.append(
            make_item(
                text,

                min(x_values),
                min(y_values),

                max(x_values),
                max(y_values),

                confidence
            )
        )


    image = Image.open(
        image_path
    )


    width, height = image.size


    return (
        items,
        width,
        height
    )


# ============================================================
# 7. 헤더 문자열 정리
# ============================================================

def normalize_header(text):

    text = text.strip()

    text = text.replace(
        " ",
        ""
    )

    text = text.replace(
        "/",
        ""
    )

    text = text.replace(
        ".",
        ""
    )

    return text


# ============================================================
# 8. 헤더 찾기
# ============================================================
def find_headers(items):

    headers = {}

    for item in items:

        text = normalize_header(
            item["text"]
        )

        text_lower = text.lower()


        # =========================================
        # No.
        # 처음 찾은 것만 사용
        # =========================================

        if (
            text_lower == "no"
            and
            "no" not in headers
        ):

            headers["no"] = item


        # =========================================
        # 품번
        # =========================================

        elif (
            fuzz.ratio(text, "품번") >= 70
            and
            "part_no" not in headers
        ):

            headers["part_no"] = item


        # =========================================
        # 품명
        # =========================================

        elif (
            fuzz.ratio(text, "품명") >= 70
            and
            "name" not in headers
        ):

            headers["name"] = item


        # =========================================
        # 규격 / 사양
        # =========================================

        elif (
            (
                fuzz.ratio(text, "규격") >= 70
                or
                fuzz.ratio(text, "사양") >= 70
                or
                fuzz.ratio(text, "규격사양") >= 70
            )
            and
            "spec" not in headers
        ):

            headers["spec"] = item


        # =========================================
        # 수량
        # =========================================

        elif (
            fuzz.ratio(text, "수량") >= 70
            and
            "quantity" not in headers
        ):

            headers["quantity"] = item


        # =========================================
        # 단위
        # =========================================

        elif (
            fuzz.ratio(text, "단위") >= 70
            and
            "unit" not in headers
        ):

            headers["unit"] = item


    return headers

# ============================================================
# 9. 열 경계 계산
# ============================================================

def make_column_boundaries(
    headers,
    page_width
):

    order = [

        "no",
        "part_no",
        "name",
        "spec",
        "quantity",
        "unit"
    ]


    columns = []


    for key in order:

        if key in headers:

            columns.append(
                (
                    key,
                    headers[key]["x_center"]
                )
            )


    columns.sort(
        key=lambda x: x[1]
    )


    boundaries = {}


    for i, (
        key,
        center
    ) in enumerate(columns):


        if i == 0:

            left = 0

        else:

            left = (
                columns[i - 1][1]
                +
                center
            ) / 2


        if i == len(columns) - 1:

            right = page_width

        else:

            right = (
                center
                +
                columns[i + 1][1]
            ) / 2


        boundaries[key] = (
            left,
            right
        )


    return boundaries


# ============================================================
# 10. 비고 시작 위치
# ============================================================

def find_footer_y(
    items,
    page_height
):

    candidates = []


    for item in items:

        text = normalize_header(
            item["text"]
        )


        if fuzz.ratio(
            text,
            "비고"
        ) >= 75:

            candidates.append(
                item["y_center"]
            )


    if candidates:

        return min(
            candidates
        )


    return page_height


# ============================================================
# 11. x 좌표가 어떤 열인지 확인
# ============================================================

def get_column(
    x,
    boundaries
):

    for key, (
        left,
        right
    ) in boundaries.items():

        if left <= x < right:

            return key


    return None


# ============================================================
# 12. No. 열을 이용해서 각 행 위치 찾기
# ============================================================
def find_row_anchors(
    items,
    headers,
    boundaries,
    footer_y
):

    # 실제 표 헤더들의 Y 위치
    header_y = max(
        headers[key]["y_center"]
        for key in [
            "no",
            "part_no",
            "name",
            "spec",
            "quantity",
            "unit"
        ]
        if key in headers
    )


    anchors = []


    for item in items:

        y = item["y_center"]


        # 표 헤더 아래 ~ 비고 위만 검사
        if not (
            header_y < y < footer_y
        ):

            continue


        column = get_column(
            item["x_center"],
            boundaries
        )


        # No. 열만 검사
        if column != "no":

            continue


        # 01, 02, 03...
        clean_text = re.sub(
            r'[^0-9]',
            '',
            item["text"]
        )


        if not clean_text:

            continue


        if re.fullmatch(
            r'\d{1,2}',
            clean_text
        ):

            number = int(
                clean_text
            )


            # 실제 품목 번호 범위
            if 1 <= number <= 99:

                anchors.append(
                    (
                        number,
                        y
                    )
                )


    anchors.sort(
        key=lambda x: x[1]
    )


    # 디버깅용
    print()
    print("========== 데이터 행 위치 ==========")

    for number, y in anchors:

        print(
            f"{number:02d}행",
            "| Y:",
            round(y, 1)
        )


    return anchors

# ============================================================
# 13. 행의 위/아래 범위 만들기
# ============================================================

def make_row_windows(
    anchors,
    headers,
    footer_y
):

    header_y = max(

        item["y_center"]

        for item in headers.values()
    )


    windows = []


    for i, (
        row_number,
        y
    ) in enumerate(anchors):


        if i == 0:

            top = (
                header_y + y
            ) / 2

        else:

            top = (
                anchors[i - 1][1]
                +
                y
            ) / 2


        if i == len(anchors) - 1:

            bottom = (
                y + footer_y
            ) / 2

        else:

            bottom = (
                y
                +
                anchors[i + 1][1]
            ) / 2


        windows.append({

            "no": row_number,

            "center_y": y,

            "top": top,

            "bottom": bottom
        })


    return windows


# ============================================================
# 14. 한 행을 각 열로 분리
# ============================================================

def extract_row_data(
    items,
    row_window,
    boundaries
):

    fields = {

        "part_no": [],
        "name": [],
        "spec": [],
        "quantity": [],
        "unit": []
    }


    for item in items:

        y = item["y_center"]


        if not (
            row_window["top"]
            <= y
            <
            row_window["bottom"]
        ):

            continue


        column = get_column(
            item["x_center"],
            boundaries
        )


        if column in fields:

            fields[column].append(
                item
            )


    result = {}


    for key, values in fields.items():

        values.sort(
            key=lambda x: x["x_left"]
        )


        result[key] = " ".join(

            value["text"]

            for value in values

        ).strip()


    return result


# ============================================================
# 15. 수량 파싱
# ============================================================

def parse_quantity(text):

    if not text:

        return None


    text = text.upper()


    # 숫자 영역에서는
    # O를 0으로 봄
    text = text.replace(
        "O",
        "0"
    )


    match = re.search(
        r'\d+',
        text
    )


    if match is None:

        return None


    quantity = int(
        match.group()
    )


    if quantity <= 0:

        return None


    return quantity


# ============================================================
# 16. ★ 수량 칸만 다시 OCR
# ============================================================
#
# 전체 EasyOCR에서 '8'을 놓쳤을 경우
#
# 수량칸 위치를 이미 알고 있으므로
# 그 셀만 잘라서 숫자 전용으로 다시 인식
#
# ============================================================

def retry_quantity_cell(
    image_path,
    row_window,
    boundaries
):

    if image_path is None:

        return None


    if "quantity" not in boundaries:

        return None


    x1, x2 = boundaries[
        "quantity"
    ]


    y1 = row_window["top"]
    y2 = row_window["bottom"]


    image = Image.open(
        image_path
    ).convert("RGB")


    width, height = image.size


    x1 = max(
        0,
        int(x1)
    )

    x2 = min(
        width,
        int(x2)
    )

    y1 = max(
        0,
        int(y1)
    )

    y2 = min(
        height,
        int(y2)
    )


    # 표 테두리를 피하기 위해
    # 셀 안쪽으로 여백을 줌

    cell_width = x2 - x1
    cell_height = y2 - y1


    margin_x = int(
        cell_width * 0.08
    )

    margin_y = int(
        cell_height * 0.12
    )


    x1 += margin_x
    x2 -= margin_x

    y1 += margin_y
    y2 -= margin_y


    if (
        x2 <= x1
        or
        y2 <= y1
    ):

        return None


    crop = image.crop(
        (
            x1,
            y1,
            x2,
            y2
        )
    )


    # 작은 숫자 대응을 위해 2배 확대
    crop = crop.resize(

        (
            crop.width * 2,
            crop.height * 2
        ),

        Image.Resampling.LANCZOS
    )


    crop_array = np.array(
        crop
    )


    ocr_reader = get_reader()


    # 중요한 부분:
    #
    # readtext()가 아니라 recognize()
    #
    # 이미 '수량 칸'이라는 것을 알고 있으므로
    # 다시 글자 위치를 검출할 필요 없이
    # 셀 전체를 문자 영역으로 인식시킴

    result = ocr_reader.recognize(

        crop_array,

        detail=1,

        allowlist="0123456789"
    )


    texts = []


    for value in result:

        if (
            isinstance(
                value,
                (list, tuple)
            )

            and

            len(value) >= 2
        ):

            texts.append(
                str(value[1])
            )


    combined = " ".join(
        texts
    )


    return parse_quantity(
        combined
    )


# ============================================================
# 17. 품번 정리
# ============================================================

def normalize_part_no(text):

    return re.sub(

        r'[^A-Z0-9]',

        '',

        text.upper()
    )


# ============================================================
# 18. 품명 정리
# ============================================================

def normalize_name(text):

    return ''.join(

        re.findall(
            r'[가-힣]+',
            text
        )
    )


# ============================================================
# 19. 규격 정리
# ============================================================

def normalize_spec(text):

    if not text:

        return ""


    text = text.upper()

    text = text.replace(
        "×",
        "X"
    )

    text = text.replace(
        " ",
        ""
    )


    # M6X2O → M6X20
    text = re.sub(

        r'(?<=\d)O|O(?=\d)',

        '0',

        text
    )


    # M4XT2 → M4X12
    text = re.sub(

        r'(?<=X)[TIL](?=\d)',

        '1',

        text
    )


    return text


# ============================================================
# 20. DB 후보 점수 계산
# ============================================================

def calculate_candidate(
    row_data,
    code,
    db_part
):

    scores = {}


    ocr_code = normalize_part_no(
        row_data["part_no"]
    )


    ocr_name = normalize_name(
        row_data["name"]
    )


    ocr_spec = normalize_spec(
        row_data["spec"]
    )


    # -----------------------------
    # 품번
    # -----------------------------

    if ocr_code:

        scores["part_no"] = fuzz.ratio(
            ocr_code,
            code
        )

    else:

        scores["part_no"] = None


    # -----------------------------
    # 품명
    # -----------------------------

    if ocr_name:

        names = [
            db_part["name"],
            *db_part.get("aliases", []),
        ]
        scores["name"] = max(
            fuzz.ratio(ocr_name, name)
            for name in names
        )

    else:

        scores["name"] = None


    # -----------------------------
    # 규격
    # -----------------------------

    if (
        db_part["spec"]
        and
        ocr_spec
    ):

        scores["spec"] = fuzz.ratio(

            ocr_spec,

            normalize_spec(
                db_part["spec"]
            )
        )

    else:

        scores["spec"] = None


    # 각 항목의 중요도
    weights = {

        "part_no": 0.50,

        "name": 0.30,

        "spec": 0.20
    }


    numerator = 0
    denominator = 0


    for key, score in scores.items():

        if score is not None:

            numerator += (
                score
                *
                weights[key]
            )

            denominator += (
                weights[key]
            )


    if denominator == 0:

        total = 0

    else:

        total = (
            numerator
            /
            denominator
        )


    return {

        "code": code,

        "part": db_part,

        "scores": scores,

        "total": total
    }


# ============================================================
# 21. 한 행의 부품 결정
# ============================================================

def match_part(
    row_data
):

    candidates = []


    for code, db_part in part_db.items():

        candidates.append(

            calculate_candidate(
                row_data,
                code,
                db_part
            )
        )


    candidates.sort(

        key=lambda x: x["total"],

        reverse=True
    )


    best = candidates[0]


    if len(candidates) >= 2:

        second = candidates[1]

        margin = (
            best["total"]
            -
            second["total"]
        )

    else:

        margin = 100


    available_scores = [

        score

        for score in best[
            "scores"
        ].values()

        if score is not None
    ]


    strong_count = sum(

        1

        for score in available_scores

        if score >= 70
    )


    contradiction = any(

        score < 40

        for score in available_scores
    )


    # 안전 기준
    safe = (

        best["total"] >= 70

        and

        margin >= 8

        and

        strong_count >= 2

        and

        not contradiction
    )


    return (
        best,
        margin,
        safe
    )


# ============================================================
# 22. PDF가 텍스트 PDF인지 판단
# ============================================================

def pdf_text_is_usable(
    items,
    headers,
    boundaries,
    footer_y
):

    if not all(

        key in headers

        for key in [
            "no",
            "part_no",
            "name",
            "quantity"
        ]
    ):

        return False


    header_y = max(

        item["y_center"]

        for item in headers.values()
    )


    # 실제 데이터 칸에 텍스트가 존재하는지 확인
    data_count = 0


    for item in items:

        if not (
            header_y
            <
            item["y_center"]
            <
            footer_y
        ):

            continue


        column = get_column(
            item["x_center"],
            boundaries
        )


        if column in [

            "part_no",
            "name",
            "spec",
            "quantity"

        ]:

            if item["text"].strip():

                data_count += 1


    # 한 행 정도의 실제 데이터가 존재하면
    # 디지털 텍스트 PDF로 판단

    return data_count >= 3


# ============================================================
# 23. 입력 파일 불러오기
# ============================================================

def load_document(input_path):

    extension = os.path.splitext(
        input_path
    )[1].lower()


    # =========================================
    # PDF
    # =========================================

    if extension == ".pdf":

        items, width, height = extract_pdf_text(
            input_path
        )

        meaningful_texts = [
            item["text"]
            for item in items
            if item["text"].strip()
        ]

        # PDF 안에 실제 문자 데이터가 충분히 있으면
        # EasyOCR 없이 PDF 문자 직접 사용
        if len(meaningful_texts) >= 5:

            print()
            print("입력 방식 : PDF 직접 텍스트 추출")
            print("→ EasyOCR를 사용하지 않습니다.")

            return {
                "items": items,
                "width": width,
                "height": height,
                "image_path": None,
                "mode": "PDF_TEXT"
            }


        # PDF가 이미지 형태인 경우만 OCR 사용
        print()
        print("PDF에 직접 추출 가능한 문자가 없습니다.")
        print("→ 이미지 OCR 방식으로 전환합니다.")

        image_path = render_pdf(
            input_path
        )

        items, width, height = run_easyocr(
            image_path
        )

        return {
            "items": items,
            "width": width,
            "height": height,
            "image_path": image_path,
            "mode": "OCR"
        }


    # =========================================
    # 이미지
    # =========================================

    elif extension in [
        ".jpg",
        ".jpeg",
        ".png"
    ]:

        items, width, height = run_easyocr(
            input_path
        )

        return {
            "items": items,
            "width": width,
            "height": height,
            "image_path": input_path,
            "mode": "OCR"
        }


    else:

        raise ValueError(
            "지원하지 않는 파일 형식입니다."
        )

# ============================================================
# 24. 메인 실행
# ============================================================

print()
print(
    "======================================"
)
print(
    "작업지시서 분석 시작"
)
print(
    "======================================"
)


document = load_document(
    INPUT_PATH
)

emit_progress(
    2,
    "문자 / 데이터 추출 완료"
)



items = document["items"]

width = document["width"]

height = document["height"]

image_path = document["image_path"]

mode = document["mode"]


# ============================================================
# 25. 원본 결과
# ============================================================

print()
print(
    "========== 입력 문자 결과 =========="
)


for item in items:

    print(

        f"{item['text']:<20}",

        "| X:",
        round(
            item["x_center"]
        ),

        "| Y:",
        round(
            item["y_center"]
        )
    )


# ============================================================
# 26. 헤더 찾기
# ============================================================

headers = find_headers(
    items
)


required_headers = [

    "no",
    "part_no",
    "name",
    "spec",
    "quantity"
]


missing = [

    key

    for key in required_headers

    if key not in headers
]


if missing:

    print()
    print(
        "필수 헤더 인식 실패 :",
        missing
    )

    raise SystemExit


print()
print(
    "========== 헤더 =========="
)


for key, item in headers.items():

    print(
        key,
        "→",
        item["text"]
    )


# ============================================================
# 27. 표 구조 확인
# ============================================================

boundaries = make_column_boundaries(

    headers,

    width
)


footer_y = find_footer_y(

    items,

    height
)


anchors = find_row_anchors(

    items,

    headers,

    boundaries,

    footer_y
)


if not anchors:

    print()
    print(
        "데이터 행을 찾지 못했습니다."
    )

    raise SystemExit


row_windows = make_row_windows(

    anchors,

    headers,

    footer_y
)

emit_progress(
    3,
    "품목 행 / 표 구조 분석 완료"
)



# ============================================================
# 28. 행 분석
# ============================================================

# ------------------------------------------------------------
# 28-1. 각 행의 텍스트 / 규격 / 수량 실제 분석
# ------------------------------------------------------------

parsed_rows = []


for row_window in row_windows:

    row_data = extract_row_data(

        items,

        row_window,

        boundaries
    )


    # 완전히 빈 행이면 무시
    if not any(

        row_data[key]

        for key in [

            "part_no",
            "name",
            "spec",
            "quantity"

        ]
    ):

        continue


    quantity = parse_quantity(

        row_data[
            "quantity"
        ]
    )


    quantity_source = "FIRST_PASS"


    # 전체 OCR에서 수량을 못 읽었으면
    # 수량 셀만 다시 인식
    if (
        quantity is None

        and

        mode == "OCR"
    ):

        print()
        print(
            f"{row_window['no']:02d}행 수량 미검출"
        )

        print(
            "→ 수량 칸 숫자 전용 재인식"
        )


        quantity = retry_quantity_cell(

            image_path,

            row_window,

            boundaries
        )


        if quantity is not None:

            quantity_source = (
                "QUANTITY_CELL_RETRY"
            )


    parsed_rows.append({

        "row_window": row_window,

        "row_data": row_data,

        "quantity": quantity,

        "quantity_source": quantity_source
    })


emit_progress(
    4,
    "규격 및 수량 분석 완료"
)


# ------------------------------------------------------------
# 28-2. 부품 DB 매칭 + Tray 결정
# ------------------------------------------------------------

final_results = []


for parsed in parsed_rows:

    row_window = parsed["row_window"]
    row_data = parsed["row_data"]
    quantity = parsed["quantity"]
    quantity_source = parsed[
        "quantity_source"
    ]


    best, margin, safe = (
        match_part(
            row_data
        )
    )


    # 수량이 없으면 무조건 작업 금지
    if quantity is None:

        safe = False


    final_results.append({

        "row": row_window["no"],

        "status": (
            "OK"
            if safe
            else "CHECK"
        ),

        "part_no": best["code"],

        "class_key": best[
            "part"
        ]["class_key"],

        "name": best[
            "part"
        ]["name"],

        "spec": best[
            "part"
        ]["spec"],

        "tray": best[
            "part"
        ]["tray"],

        "quantity": quantity,

        "quantity_source":
            quantity_source,

        "total_score":
            best["total"],

        "margin":
            margin,

        "scores":
            best["scores"],

        "ocr":
            row_data
    })


emit_progress(
    5,
    "Tray 매칭 완료"
)


# ============================================================
# 29. 최종 출력
# ============================================================

print()
print()
print(
    "======================================"
)
print(
    "========== 최종 결과 =========="
)
print(
    "======================================"
)


for item in final_results:

    print()
    print(
        "--------------------------------------"
    )

    print(
        "행       :",
        item["row"]
    )

    print(
        "상태     :",
        (
            "정상"
            if item["status"] == "OK"
            else "⚠ 확인 필요"
        )
    )

    print(
        "품번     :",
        item["part_no"]
    )

    print(
        "품명     :",
        item["name"]
    )

    print(
        "규격/사양:",
        item["spec"]
        if item["spec"]
        else "-"
    )

    print(
        "수량     :",
        item["quantity"]
    )

    print(
        "Tray     :",
        item["tray"]
    )

    print(
        "품번점수 :",
        round(
            item["scores"][
                "part_no"
            ]
            or 0,
            1
        )
    )

    print(
        "품명점수 :",
        round(
            item["scores"][
                "name"
            ]
            or 0,
            1
        )
    )

    print(
        "규격점수 :",
        round(
            item["scores"][
                "spec"
            ]
            or 0,
            1
        )
    )

    print(
        "종합점수 :",
        round(
            item[
                "total_score"
            ],
            1
        )
    )

    print(
        "2위 점수차:",
        round(
            item["margin"],
            1
        )
    )

    print(
        "수량 인식:",
        item[
            "quantity_source"
        ]
    )


# ============================================================
# 30. 전체 작업 가능 여부
# ============================================================

print()
print(
    "======================================"
)


if all(

    item["status"] == "OK"

    for item in final_results

):

    print(
        "모든 품목 확인 완료"
    )

    print(
        "→ 자동 Tray 작업 가능"
    )

else:

    print(
        "확인이 필요한 품목이 있습니다."
    )

    print(
        "→ 자동 Tray 이동 금지"
    )


print(
    "======================================"
)
# ============================================================
# UI 전달용 분석 결과 JSON 저장
# ============================================================

output_data = {
    "input_file": INPUT_PATH,
    "mode": mode,

    "all_ok": (
        len(final_results) > 0
        and
        all(
            item["status"] == "OK"
            for item in final_results
        )
    ),

    "items": final_results
}


with open(
    "analysis_result.json",
    "w",
    encoding="utf-8"
) as file:

    json.dump(
        output_data,
        file,
        ensure_ascii=False,
        indent=4
    )


# 기존 OCR 분석 코드
# ...
# ...
# ...

print(
    "======================================"
)


# ============================================================
# UI 전달용 결과 저장 완료
# ============================================================

print()
print("UI 전달용 결과 저장 완료")
print("→ analysis_result.json")
