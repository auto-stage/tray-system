# tray-system

AI 비전 기반 2축 스테이션 연계 트레이 자동 조달 시스템

## 1. 프로젝트 개요

본 프로젝트는 작업지시서 인식, ArUco 마커 기반 트레이 위치·자세 추정, 2축 스테이지 제어를 연계하여 작업 대상 트레이를 자동으로 식별하고 조달하기 위한 시스템입니다.

현재 각 기능 모듈은 개별적으로 개발·검증 중이며, 최종 통합 단계에서는 다음 흐름으로 연결할 예정입니다.

```text
작업지시서 이미지
      ↓
OCR 작업지시서 인식
      ↓
작업 대상 / Tray ID 결정
      ↓
ArUco 마커 검출
      ↓
Tray 6DoF Pose 추정
      ↓
3D Grip Target 계산
      ↓
Moving Camera → Carriage 좌표 변환
      ↓
X/Z 상대 오차 보정
      ↓
PC Stage Control
      ↓ Serial/UART
STM32 Stage Controller
      ↓
X/Z 2축 스테이지 구동
```

## 2. 현재 개발 상태

현재 저장소는 다음 세 핵심 파트를 중심으로 구성되어 있습니다.

1. **STM32 2축 스테이지 펌웨어**
   - X/Z축 스텝모터 제어
   - PUL/DIR/ENA 제어
   - 리밋 입력
   - E-STOP
   - UART 명령 처리

2. **OCR 작업지시서 인식**
   - 작업지시서 이미지 입력
   - OCR 기반 문자열 인식
   - 작업 대상 매칭 시험
   - 다양한 촬영 조건의 테스트 이미지 관리

3. **ArUco 기반 트레이 비전**
   - 카메라 입력
   - ArUco ID 검출
   - Tray ID 매칭
   - 6DoF Pose 추정
   - 3D Grip Target 계산
   - Roll/Pitch/Yaw 유효성 검사
   - 이동부 Camera → Carriage 변환
   - X/Z 상대 보정량 계산

현재 세 파트는 완전 통합 전 단계이며, 각 모듈의 독립 동작을 우선 유지합니다.

## 3. Repository 구조

```text
tray-system/
│
├── README.md
├── requirements.txt
├── .gitignore
│
├── firmware/
│   ├── README.md
│   └── stm32_stage_controller/
│       ├── Core/
│       ├── Drivers/
│       ├── .settings/
│       ├── stage_move.ioc
│       └── ...
│
├── modules/
│   ├── stage_control/
│   │   └── README.md
│   ├── work_order_ocr/
│   │   ├── README.md
│   │   ├── requirements.txt
│   │   ├── ocr_test.py
│   │   ├── match_test.py
│   │   └── data/
│   │       └── work_orders/
│   └── aruco_tray_vision/
│       ├── aruco_tray/
│       ├── config/
│       ├── patterns/
│       ├── tests/
│       ├── tools/
│       ├── main.py
│       ├── gui_app.py
│       └── requirements.txt
│
├── integration/
│   └── README.md
│
└── docs/
    └── INTEGRATION_PLAN.md
```

## 4. 각 디렉터리 역할

### `firmware/stm32_stage_controller/`

STM32에서 실제로 실행되는 2축 스테이지 제어 펌웨어입니다.

주요 역할:
- X축 PUL/DIR/ENA 제어
- Z축 PUL/DIR/ENA 제어
- X/Z 리밋 입력
- E-STOP 입력
- UART 명령 수신
- 동작 상태 및 ACK 응답

STM32CubeIDE 프로젝트 형식을 유지합니다.

빌드 결과물인 `Debug/`, `Release/`, `*.o`, `*.elf`, `*.map` 등은 Git으로 관리하지 않습니다.

### `modules/stage_control/`

PC에서 STM32 스테이지를 직접 제어하기 위한 전용 모듈입니다.

ArUco 비전과 관계없이 액추에이터를 단독으로 수동 시험하거나 유지보수할 때 사용할 수 있도록 구성합니다.

예정 구조:

```text
modules/stage_control/
├── README.md
├── stage_serial.py
├── manual_control.py
└── manual_gui.py
```

예정 역할:
- STM32 Serial/UART 연결
- X축 수동 이동
- Z축 수동 이동
- Homing
- Stop
- 상태/ACK 확인
- 스테이지 단독 동작 시험
- GUI 기반 수동 제어

> 현재 ArUco 모듈 내부에도 `stage_serial.py`가 존재합니다.
> 기존 비전 코드의 정상 동작을 보존하기 위해 당장은 그대로 유지합니다.
> 최종 통합 단계에서 공통 Stage Serial 계층으로 통합하거나 import 구조로 변경할 예정입니다.

### `modules/work_order_ocr/`

OCR 기반 작업지시서 인식 모듈입니다.

```text
modules/work_order_ocr/
├── ocr_test.py
├── match_test.py
└── data/
    └── work_orders/
```

`data/work_orders/`에는 OCR 테스트에 사용하는 작업지시서 이미지들을 관리합니다.

### `modules/aruco_tray_vision/`

ArUco 마커 기반 트레이 인식 및 3D 위치·자세 추정 모듈입니다.

주요 기능:
- 카메라 프레임 획득
- ArUco Marker ID 검출
- Tray 정보 매칭
- 6DoF Pose 계산
- 3D Grip Target 계산
- Roll/Pitch/Yaw 허용범위 검사
- 카메라 캘리브레이션
- Stage Serial 연계 인터페이스

최종 ArUco 카메라는 X/Z 이동부에 고정되므로 Camera → Carriage 캘리브레이션과 현재 Stage 위치를 결합합니다. 실제 장착 전에는 자동 X/Z 보정이 안전 차단됩니다.

### `integration/`

최종 시스템 통합 계층입니다.

```text
OCR
 ↓
Tray 선택
 ↓
ArUco Vision
 ↓
3D Grip Target
 ↓
Camera → Moving Carriage Transform
 ↓
X/Z Relative Correction
 ↓
Stage Control
 ↓
STM32
```

## 5. STM32 주요 핀맵

| 기능 | STM32 핀 |
|---|---|
| X STEP/PUL | PA8 / TIM1_CH1 |
| X DIR | PD4 |
| X ENA | PD5 |
| Z STEP/PUL | PC6 / TIM8_CH1 |
| Z DIR | PD6 |
| Z ENA | PD7 |
| X MIN LIMIT | PF12 |
| X MAX LIMIT | PF13 |
| Z MIN LIMIT | PF14 |
| Z MAX LIMIT | PF15 |
| E-STOP | PG2 |
| PC 통신 | USART3 |

> 실제 하드웨어 변경 시 `main.c`, `.ioc` 및 본 문서를 함께 갱신해야 합니다.

## 6. 실측/확정이 필요한 주요 파라미터

- X축 이동 범위
- Z축 이동 범위
- X축 steps/mm
- Z축 steps/mm
- 마이크로스텝 설정
- 모터 1회전당 pulse 수
- 볼스크류/리드스크류 lead
- 모터 이동 방향
- 리밋 스위치 논리
- E-STOP 논리
- 최대 이동 속도
- 가속/감속 조건
- Camera → Stage extrinsic transform
- 실제 Grip Target offset

## 7. Python 환경 구성

```bash
cd ~/tray-system

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt
```

## 8. 개별 모듈 실행

### OCR

```bash
cd ~/tray-system
python modules/work_order_ocr/ocr_test.py
```

```bash
python modules/work_order_ocr/match_test.py
```

### ArUco

```bash
cd ~/tray-system/modules/aruco_tray_vision
python main.py
```

```bash
python main.py --self-test
pytest -q
```

### Stage Control

현재 PC 측 수동 제어 모듈을 별도로 구성하는 단계입니다.

향후:

```bash
python modules/stage_control/manual_control.py
```

또는:

```bash
python modules/stage_control/manual_gui.py
```

형태로 실행할 예정입니다.

## 9. Git 협업 규칙

본 프로젝트는 3인 협업을 기준으로 다음 GitHub Flow를 사용합니다.

```text
main
 ↓
feature branch 생성
 ↓
코드 수정
 ↓
commit
 ↓
push
 ↓
Pull Request
 ↓
다른 팀원 검토
 ↓
Merge
 ↓
main 최신화
```

### 작업 시작

```bash
cd ~/tray-system

git switch main
git pull

git switch -c feature/작업이름
```

### 코드 수정 후

```bash
git status
git diff
git add 수정한파일또는폴더
git commit -m "변경 내용"
git push -u origin feature/작업이름
```

GitHub에서 Pull Request를 생성하고 검토 후 `main`에 Merge합니다.

Merge 완료 후:

```bash
git switch main
git pull
```

작업이 끝난 로컬 브랜치는:

```bash
git branch -d feature/작업이름
```

으로 삭제할 수 있습니다.

## 10. 담당 파트 권장 구분

| 담당 | 주요 작업 경로 |
|---|---|
| STM32 / 액추에이터 | `firmware/stm32_stage_controller/`, `modules/stage_control/` |
| OCR | `modules/work_order_ocr/` |
| ArUco / Vision | `modules/aruco_tray_vision/` |
| 통합 | `integration/` |

공통 파일인 다음 항목은 수정 전에 팀원 간 공유를 권장합니다.

- `README.md`
- `requirements.txt`
- `integration/`
- 공통 config
- 공통 interface

## 11. Git 사용 시 주의사항

다음 명령은 원리를 충분히 이해하기 전까지 임의로 사용하지 않습니다.

```bash
git push --force
git reset --hard
```

또한 다음 정보는 절대 저장소에 commit하지 않습니다.

- GitHub Token
- API Key
- 비밀번호
- SSH Private Key
- 개인 인증 파일
- `.env` 내 실제 비밀정보

## 12. 개발 원칙

1. 현재 정상 동작하는 각 파트의 코드는 최대한 유지합니다.
2. 새 기능은 기존 코드를 비침습적으로 확장하는 방식으로 추가합니다.
3. 각 모듈은 최종 통합 전까지 독립 실행 및 시험이 가능해야 합니다.
4. 하드웨어 의존 파라미터는 실측 후 확정합니다.
5. 공통 통신 계층은 통합 시 중복 구현을 정리합니다.
6. `main`에는 검증된 코드만 Merge하는 것을 원칙으로 합니다.
7. 기능 추가·파라미터 변경·실행 방법 변경 시 본 README를 함께 갱신합니다.

## 13. 향후 통합 예정 사항

- PC용 Stage Manual Control 구현
- STM32 Serial Protocol 정리
- ArUco 내부 Stage Serial과 공통 Stage Control 계층 정리
- 이동부 ArUco Camera → Carriage calibration
- OCR 결과 → Tray ID 매핑
- Tray ID → ArUco Target 연계
- Vision Grip Target → Stage X/Z 폐루프 보정 실기 검증
- Load Cell 실제 Adapter 연동 및 단위중량/공차 캘리브레이션
- 고정 카메라 Tray ROI 및 부품 종류/형상/크기 검수 실기 검증
- Mock Part Vision → OpenCV/YOLO 기반 실제 검수 Adapter 교체
- 통합 Safety/Interlock
- 전체 자동 조달 Sequence 구현
- 통합 GUI 구성
- End-to-End 실제 장비 검증

---

## X-Z Stage Integration

X-Z Stage 좌표 매핑, STM32 연동, HOME 안전 로직 및
실제 장비 적용 절차는 아래 문서를 참고하세요.

- [Stage Mapping Integration Guide](docs/STAGE_MAPPING_INTEGRATION.md)

## Hardware-free Inspection Integration

카메라와 Load Cell 수령 전에는 다음 Mock 계층으로 통합 흐름을 검증합니다.

- `MockLoadCellAdapter`: 중량 기반 수량 판정 인터페이스 검증
- `MockPartInspectionAdapter`: 부품 존재/종류/외형 검수 인터페이스 검증
- `InspectionService`: Load Cell 수량 + Camera 검수 결과를 종합해 PASS/NG 판정
- `backend/config/parts.yaml`: 실제 하드웨어 실측값을 추후 입력하는 부품별 설정

기본 개발 모드는 다음과 같습니다.

```bash
LOADCELL_MODE=mock PART_INSPECTION_MODE=mock
```

주요 API:

- `GET /loadcell/status`
- `POST /loadcell/tare`
- `GET /inspection/status`
- `POST /inspection/run`
- `GET /parts/config`
- `POST /inspection/reload-config`

`parts.yaml`의 `unit_weight_g`, `empty_tray_weight_g`, `tolerance_g`, Vision ROI/크기 기준은
실제 센서와 카메라가 장착되기 전까지 `null` 및 `calibrated: false` 상태를 유지합니다.
