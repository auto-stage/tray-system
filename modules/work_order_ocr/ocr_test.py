# Git collaboration test
import easyocr
import warnings
import re
from pathlib import Path
from rapidfuzz import fuzz


# ==========================================
# 1. 기본 설정
# ==========================================

warnings.filterwarnings("ignore")

# 한국어 + 영어 OCR
reader = easyocr.Reader(['ko', 'en'], gpu=False)


# ==========================================
# 2. 부품 데이터베이스
# ==========================================

part_db = [
    {"name": "T볼트",       "spec": "M6X20", "tray": 1},
    {"name": "육각렌치볼트",   "spec": "M5X15", "tray": 2},
    {"name": "십자머리나사",   "spec": "M4X12", "tray": 3},
    {"name": "육각너트",       "spec": "M6",    "tray": 4},
    {"name": "평와셔",         "spec": "M6",    "tray": 5},
    {"name": "스프링와셔",     "spec": "M6",    "tray": 6}
]


# ==========================================
# 3. EasyOCR 실행
# ==========================================

# 테스트 이미지 경로 (현재 파일 위치 기준)
IMAGE_PATH = Path(__file__).resolve().parent / 'data' / 'work_orders' / 'work_light.jpg'
result = reader.readtext(str(IMAGE_PATH))

items = []


# EasyOCR 결과에서 글자와 위치 정보 저장
for box, text, confidence in result:

    x_values = [point[0] for point in box]
    y_values = [point[1] for point in box]

    x_left = min(x_values)
    y_top = min(y_values)
    y_bottom = max(y_values)

    items.append({
        'text': text,
        'x': x_left,
        'top': y_top,
        'bottom': y_bottom,
        'confidence': confidence
    })


# ==========================================
# 4. 같은 행끼리 묶기
# ==========================================

# 위쪽 글자부터 정렬
items.sort(key=lambda item: item['top'])

lines = []


for item in items:

    best_line = None
    best_overlap = 0

    # 기존 줄들과 세로 영역 비교
    for line in lines:

        overlap = (
            min(line['bottom'], item['bottom'])
            - max(line['top'], item['top'])
        )

        if overlap > 0:

            item_height = item['bottom'] - item['top']
            line_height = line['bottom'] - line['top']

            overlap_ratio = overlap / min(
                item_height,
                line_height
            )

            if overlap_ratio > best_overlap:
                best_overlap = overlap_ratio
                best_line = line


    # 40% 이상 겹치면 같은 줄
    if best_line is not None and best_overlap >= 0.4:

        best_line['words'].append(item)

        best_line['top'] = min(
            best_line['top'],
            item['top']
        )

        best_line['bottom'] = max(
            best_line['bottom'],
            item['bottom']
        )

    # 아니면 새로운 줄
    else:

        lines.append({
            'top': item['top'],
            'bottom': item['bottom'],
            'words': [item]
        })


# 위 → 아래 순서
lines.sort(key=lambda line: line['top'])


# ==========================================
# 5. 한 줄짜리 문자열로 만들기
# ==========================================

ocr_lines = []


for line in lines:

    # 같은 줄 안에서는 왼쪽 → 오른쪽
    line['words'].sort(
        key=lambda word: word['x']
    )

    texts = [
        word['text']
        for word in line['words']
    ]

    line_text = ' '.join(texts)

    ocr_lines.append(line_text)


# ==========================================
# 6. 필요한 함수들
# ==========================================


def get_korean(text):
    """
    OCR 결과에서 한글만 추출

    예:
    육각물트 M6X2O 10 EA
    ↓
    육각물트
    """

    return ''.join(
        re.findall(r'[가-힣]+', text)
    )


def get_db_spec(spec):
    """
    DB 규격에서 M 제거

    M6X20 → 6X20
    M6    → 6
    """

    return spec.replace("M", "")


def normalize_number_text(text):
    """
    숫자/규격 주변에서 자주 발생하는
    OCR 오류를 일부 보정
    """

    text = text.upper()

    # × 기호를 X로 통일
    text = text.replace("×", "X")


    # 숫자 옆 O는 0으로 보정
    # 예: 2O → 20
    #     1O → 10
    text = re.sub(
        r'(?<=\d)O|O(?=\d)',
        '0',
        text
    )


    # X 바로 뒤의 T / I / L을 1로 오인식한 경우
    #
    # 예:
    # M4Xt2
    # ↓ 대문자화
    # M4XT2
    # ↓
    # M4X12

    text = re.sub(
        r'(?<=X)\s*[TIL]\s*(?=\d)',
        '1',
        text
    )

    return text


def find_ocr_spec(text):
    """
    OCR 결과에서 규격 형태를 찾아냄

    예:
    H6 X20 → 6X20
    M6X2O  → 6X20
    M4Xt2  → 4X12
    """

    text = normalize_number_text(text)


    # 먼저 6X20, 5X15 같은 규격 검색
    match = re.search(
        r'(\d+)\s*X\s*(\d+)',
        text
    )

    if match:

        return (
            match.group(1)
            + "X"
            + match.group(2)
        )


    # X가 없는 경우에는 숫자들을 따로 반환
    #
    # 예:
    # M6 10EA
    # ↓
    # ['6', '10']

    return re.findall(r'\d+', text)


# ==========================================
# 7. 수량 추출
# ==========================================

def extract_quantity(text, spec):

    # OCR 오인식 먼저 보정
    text = normalize_number_text(text)


    # --------------------------------------
    # M6X20처럼 X가 있는 규격
    # --------------------------------------

    if "X" in spec:

        spec_number = spec.replace("M", "")

        first, second = spec_number.split("X")


        # 예:
        # M6X20이면
        # 6 X 20 위치를 찾음

        pattern = rf'{first}\s*X\s*{second}'

        match = re.search(
            pattern,
            text
        )


        if match:

            # 규격 뒤의 문자열만 가져옴
            #
            # M6X20 10EA
            #       ^^^^^
            #       이 부분

            remain = text[match.end():]


            # 수량에서 O → 0 보정
            #
            # 1OEA → 10EA

            remain = re.sub(
                r'(?<=\d)O|O(?=\d)',
                '0',
                remain
            )


            # 규격 뒤에서 처음 나오는 숫자를 수량으로 사용
            quantity_match = re.search(
                r'\d+',
                remain
            )

            if quantity_match:

                return int(
                    quantity_match.group()
                )


    # --------------------------------------
    # M6처럼 X가 없는 규격
    # --------------------------------------

    else:

        spec_number = spec.replace("M", "")


        # 규격 숫자 찾기
        #
        # M6 → 6

        match = re.search(
            re.escape(spec_number),
            text
        )


        if match:

            # 규격 뒤의 부분만 가져옴
            remain = text[match.end():]


            # 1O → 10 보정
            remain = re.sub(
                r'(?<=\d)O|O(?=\d)',
                '0',
                remain
            )


            # 규격 뒤 처음 나오는 숫자
            quantity_match = re.search(
                r'\d+',
                remain
            )

            if quantity_match:

                return int(
                    quantity_match.group()
                )


    # 수량을 못 찾았을 경우
    return None


# ==========================================
# 8. EasyOCR 원본 결과 출력
# ==========================================

print()
print("========== OCR 원본 ==========")

for line in ocr_lines:
    print(line)


# ==========================================
# 9. DB와 비교해서 품목 찾기
# ==========================================

print()
print("========== 최종 결과 ==========")


for line in ocr_lines:

    # OCR 결과에서 한글 품명 추출
    korean = get_korean(line)

    # OCR 결과에서 규격 추출
    ocr_spec = find_ocr_spec(line)


    best_part = None
    best_score = -1


    # --------------------------------------
    # DB 6개와 하나씩 비교
    # --------------------------------------

    for part in part_db:

        # 품명 유사도
        name_score = fuzz.ratio(
            korean,
            part["name"]
        )


        # DB 규격
        db_spec = get_db_spec(
            part["spec"]
        )


        # ----------------------------------
        # 규격 점수
        # ----------------------------------

        # 6X20 같은 규격을 찾은 경우
        if isinstance(ocr_spec, str):

            if db_spec == ocr_spec:

                spec_score = 100

            else:

                spec_score = fuzz.ratio(
                    ocr_spec,
                    db_spec
                )


        # M6 같은 단순 규격
        else:

            if (
                "X" not in db_spec
                and db_spec in ocr_spec
            ):

                spec_score = 100

            else:

                spec_score = 0


        # ----------------------------------
        # 품명 70% + 규격 30%
        # ----------------------------------

        total_score = (
            name_score * 0.7
            + spec_score * 0.3
        )


        # 가장 점수가 높은 부품 저장
        if total_score > best_score:

            best_score = total_score
            best_part = part


    # ======================================
    # 10. 최종 품목이 결정된 뒤 수량 추출
    # ======================================

    quantity = extract_quantity(
        line,
        best_part["spec"]
    )


    # ======================================
    # 11. 결과 출력
    # ======================================

    print()

    print("OCR 원문 :", line)

    print(
        "품목     :",
        best_part["name"]
    )

    print(
        "규격     :",
        best_part["spec"]
    )

    print(
        "수량     :",
        quantity
    )

    print(
        "Tray     :",
        best_part["tray"]
    )

    print(
        "점수     :",
        round(best_score, 1)
    )