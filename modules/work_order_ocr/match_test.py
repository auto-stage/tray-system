from rapidfuzz import fuzz
import re


part_db = [
    {"name": "육각볼트",       "spec": "M6X20", "tray": 1},
    {"name": "육각렌치볼트",   "spec": "M5X15", "tray": 2},
    {"name": "십자머리나사",   "spec": "M4X12", "tray": 3},
    {"name": "육각너트",       "spec": "M6",    "tray": 4},
    {"name": "평와셔",         "spec": "M6",    "tray": 5},
    {"name": "스프링와셔",     "spec": "M6",    "tray": 6}
]


ocr_lines = [
    "육각분트 H6 X20 10 En",
    "육각렌치 볼트 H5 X15 8 EH",
    "섬자 이리 나사 시4 X12 20E4",
    "육각너트 1L| 6 1oEA",
    "평와서 H6 10 EA",
    "스프랑와서 H 6 10E]}"
]


def get_korean(text):
    return ''.join(re.findall(r'[가-힣]+', text))


def get_db_spec(spec):
    # M6X20 -> 6X20
    # M6 -> 6
    return spec.replace("M", "")


def find_ocr_spec(text):

    text = text.upper()
    text = text.replace("×", "X")

    # 6 X 20 같은 규격 우선 검색
    match = re.search(r'(\d+)\s*X\s*(\d+)', text)

    if match:
        return match.group(1) + "X" + match.group(2)

    # X가 없는 경우
    return re.findall(r'\d+', text)


def extract_quantity(text, spec):

    text = text.upper()
    text = text.replace("×", "X")

    # 예: M6X20
    if "X" in spec:

        spec_number = spec.replace("M", "")
        first, second = spec_number.split("X")

        # DB의 정확한 규격 숫자를 이용해서 위치 찾기
        pattern = rf'{first}\s*X\s*{second}'
        match = re.search(pattern, text)

        if match:
            # 규격 뒤쪽만 가져오기
            remain = text[match.end():]

            # 수량 영역에서 흔한 OCR 오류 보정
            remain = remain.replace("O", "0")

            quantity_match = re.search(r'\d+', remain)

            if quantity_match:
                return int(quantity_match.group())

    # 예: M6
    else:

        spec_number = spec.replace("M", "")

        # 규격 숫자가 나타나는 위치 찾기
        match = re.search(re.escape(spec_number), text)

        if match:
            # M6의 6 뒤쪽부터 확인
            remain = text[match.end():]

            # 10을 1o / 1O로 읽는 경우 보정
            remain = remain.replace("O", "0")

            quantity_match = re.search(r'\d+', remain)

            if quantity_match:
                return int(quantity_match.group())

    return None


for line in ocr_lines:

    korean = get_korean(line)
    ocr_spec = find_ocr_spec(line)

    best_part = None
    best_score = -1

    for part in part_db:

        # 품명 점수
        name_score = fuzz.ratio(
            korean,
            part["name"]
        )

        db_spec = get_db_spec(part["spec"])

        # 규격 점수
        if isinstance(ocr_spec, str):

            if db_spec == ocr_spec:
                spec_score = 100
            else:
                spec_score = fuzz.ratio(
                    ocr_spec,
                    db_spec
                )

        else:

            if "X" not in db_spec and db_spec in ocr_spec:
                spec_score = 100
            else:
                spec_score = 0

        # 종합 점수
        total_score = (
            name_score * 0.7
            + spec_score * 0.3
        )

        if total_score > best_score:
            best_score = total_score
            best_part = part


    # ----------------------------
    # 품목이 결정된 뒤 수량 추출
    # ----------------------------

    quantity = extract_quantity(
        line,
        best_part["spec"]
    )


    print("\n================================")
    print("OCR 원문 :", line)
    print("최종 품목 :", best_part["name"])
    print("최종 규격 :", best_part["spec"])
    print("수량      :", quantity)
    print("Tray      :", best_part["tray"])
    print("종합 점수 :", round(best_score, 1))