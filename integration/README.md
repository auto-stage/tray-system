# Integration

이 디렉터리는 현재 독립적으로 동작하는 세 파트를 추후 하나의 운용 시퀀스로 연결하기 위한 공간입니다.

예정 데이터 흐름:

1. `modules/work_order_ocr` — 작업지시서 OCR 및 품목/Tray 번호 결정
2. `modules/aruco_tray_vision` — 해당 Tray의 ArUco 검출, 6DoF 자세 및 파지 목표 계산
3. `firmware/stm32_stage_controller` — PC 측 직렬 명령을 받아 2축 스테이지 구동

현재는 기능 통합 코드를 추가하지 않았습니다. 각 파트의 기존 독립 실행 구조를 유지합니다.
