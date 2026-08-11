# AI Vision-Based 2-Axis Tray Supply System

OCR 기반 작업지시서 인식, ArUco 마커 기반 Tray 위치/자세 인식, STM32 기반 2축 스테이지 제어를 하나의 GitHub 저장소에서 관리하기 위한 통합 워크스페이스입니다.

> 현재 상태: 세 파트는 **아직 하나의 실행 시퀀스로 통합되지 않았습니다.** 이 저장소는 각 파트의 독립 실행 가능성을 유지하면서 추후 통합하기 쉽도록 디렉터리와 데이터 파일을 정리한 버전입니다.

## Repository Structure

```text
ai_vision_2axis_tray_supply_system/
├── README.md
├── requirements.txt
├── .gitignore
├── firmware/
│   ├── README.md
│   └── stm32_stage_controller/       # STM32CubeIDE / NUCLEO-F767ZI 2축 Stage
├── modules/
│   ├── work_order_ocr/               # OCR 작업지시서 인식
│   │   ├── ocr_test.py
│   │   ├── match_test.py
│   │   ├── requirements.txt
│   │   └── data/work_orders/         # 작업지시서 테스트 이미지
│   └── aruco_tray_vision/            # ArUco 6DoF Tray 검출/파지 목표 계산
│       ├── aruco_tray/
│       ├── config/
│       ├── patterns/
│       ├── tests/
│       ├── tools/
│       ├── main.py
│       └── gui_app.py
├── integration/
│   └── README.md                     # 추후 통합 계층
└── docs/
    └── INTEGRATION_PLAN.md
```

## Part 1 — STM32 2-Axis Stage Controller

경로:

```text
firmware/stm32_stage_controller/
```

STM32CubeIDE 프로젝트 전체를 유지했습니다. 원본에 포함되어 있던 `Debug/` 폴더는 컴파일 산출물이므로 GitHub 버전에서는 제외했습니다.

확인된 핵심 설정 (`Core/Src/main.c`):

| 기능 | 핀/주변장치 |
|---|---|
| X STEP/PUL | PA8 / TIM1_CH1 |
| Z STEP/PUL | PC6 / TIM8_CH1 |
| X DIR | PD4 |
| X ENA | PD5 |
| Z DIR | PD6 |
| Z ENA | PD7 |
| X MIN | PF12 |
| X MAX | PF13 |
| Z MIN | PF14 |
| Z MAX | PF15 |
| E-STOP | PG2 |
| PC 통신 | USART3 |

현재 코드에 들어 있는 대표적인 장비 의존 파라미터:

- X/Z `steps_per_mm`: 기본 320 step/mm
- X soft limit: 0–1000 mm
- Z soft limit: 0–700 mm
- Home fast/slow 속도와 가속도
- Home backoff 거리
- DIR 논리 레벨
- ENA 논리 레벨
- Limit/E-Stop active level
- Timer clock/prescaler

실제 기구, 드라이버 설정 및 센서 배선에 맞춰 최종 확정해야 합니다.

## Part 2 — Work Order OCR

경로:

```text
modules/work_order_ocr/
```

기존 Python 코드와 작업지시서 이미지를 분리했습니다.

```text
work_order_ocr/
├── ocr_test.py
├── match_test.py
└── data/work_orders/
    ├── work_order*.jpg
    ├── work_dark.jpg
    ├── work_mid.jpg
    ├── work_light.jpg
    └── ...
```

`ocr_test.py`의 기본 입력 이미지 경로도 새 데이터 폴더를 기준으로 동작하도록 변경했습니다. OCR/매칭 알고리즘 자체는 변경하지 않았습니다.

실행:

```bash
python modules/work_order_ocr/ocr_test.py
python modules/work_order_ocr/match_test.py
```

## Part 3 — ArUco Tray Vision

경로:

```text
modules/aruco_tray_vision/
```

기존 `aruco_tray_system_v3_6dof`의 Python 패키지, GUI, YAML 설정, 테스트, ArUco/Checkerboard 패턴 파일을 그대로 유지했습니다. `__pycache__`와 `.pyc`만 제거했습니다.

실행:

```bash
cd modules/aruco_tray_vision
python main.py
```

Self-test:

```bash
cd modules/aruco_tray_vision
python main.py --self-test
pytest -q
```

현재 `config/system.yaml`에서 Camera→Stage extrinsic은 의도적으로 미설정 상태이며, 실제 카메라와 스테이지 설치 후 캘리브레이션해야 합니다.

## Python Environment

Ubuntu 예시:

```bash
cd ai_vision_2axis_tray_supply_system
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

EasyOCR는 최초 실행 시 모델 파일 다운로드가 발생할 수 있습니다.

## Future Integration

통합 시 권장 데이터 흐름은 다음과 같습니다.

```text
작업지시서 이미지
 -> OCR
 -> 품목/규격/수량/Tray ID 결정
 -> 해당 Tray ArUco 검출
 -> 6DoF 및 3D 파지점 계산
 -> Camera 좌표계 -> Stage 좌표계 변환
 -> PC Stage 명령 생성
 -> UART/Serial
 -> STM32 2축 Stage 구동
```

구체적인 통합 계획은 `docs/INTEGRATION_PLAN.md`를 참고하십시오.

## GitHub Upload

GitHub에서 빈 저장소를 만든 뒤 이 폴더에서:

```bash
git init
git add .
git commit -m "Initial project workspace"
git branch -M main
git remote add origin <YOUR_GITHUB_REPOSITORY_URL>
git push -u origin main
```

STM32의 `Debug/`, Python의 `__pycache__/`, 가상환경 등은 `.gitignore`로 제외되어 있습니다.
