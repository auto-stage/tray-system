# Work Order OCR

작업지시서 이미지를 EasyOCR로 읽고, 품명/규격을 부품 DB와 fuzzy matching하여 Tray 번호와 수량을 판별하는 독립 모듈입니다.

## 구조

- `ocr_test.py`: 실제 이미지 OCR + 행 정렬 + 품목/규격/수량 매칭
- `match_test.py`: OCR 결과 문자열을 가정한 매칭 로직 단독 시험
- `data/work_orders/`: 작업지시서 및 조명 조건별 테스트 이미지

## 실행

저장소 루트에서:

```bash
python modules/work_order_ocr/ocr_test.py
python modules/work_order_ocr/match_test.py
```

또는 이 폴더에서 직접 실행해도 됩니다. `ocr_test.py`의 기본 이미지는 `data/work_orders/work_light.jpg`입니다.
