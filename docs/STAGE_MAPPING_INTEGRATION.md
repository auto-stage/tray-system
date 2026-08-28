# X-Z Stage Mapping & STM32 Integration Guide

## 1. 현재 Stage 제어 구조

현재 Stage 제어 흐름은 다음과 같다.

```text
작업지시서 OCR
→ Tray ID 결정
→ rack_layout.json에서 Tray의 현재 Slot 확인
→ Slot 번호를 XC/ZR Anchor로 변환
→ slot_map.json에서 실제 X/Z 좌표 조회
→ STM32 Stage 이동
→ /stage/status로 실제 상태 확인
→ 목표 도착 확인
→ Picking 단계 진행
```

중요한 점은 Tray 자체에 좌표를 저장하는 것이 아니라,
물리적인 Rack Slot에 좌표를 저장한다는 것이다.

---

## 2. Rack 구조

현재 Rack은 3행 × 2열 구조이다.

```text
           XC1          XC2

ZR1      Slot 1       Slot 2

ZR2      Slot 3       Slot 4

ZR3      Slot 5       Slot 6
```

각 Slot의 좌표 조합은 다음과 같다.

| Slot | 위치 | X Anchor | Z Anchor |
|---|---|---|---|
| 1 | 좌측 상단 | XC1 | ZR1 |
| 2 | 우측 상단 | XC2 | ZR1 |
| 3 | 좌측 중단 | XC1 | ZR2 |
| 4 | 우측 중단 | XC2 | ZR2 |
| 5 | 좌측 하단 | XC1 | ZR3 |
| 6 | 우측 하단 | XC2 | ZR3 |

---

## 3. 현재 Mapping 상태

현재 측정된 X 좌표:

```text
XC1 = 2.7094 mm
XC2 = 339.0406 mm
```

현재 Z 좌표:

```text
ZR1 = 미매핑
ZR2 = 미매핑
ZR3 = 미매핑
```

기구를 재조립한 이후에는 기존 XC1/XC2 좌표를 그대로 사용하지 않고
실제 열 중심과 다시 일치하는지 확인한다.

---

## 4. 주요 파일

```text
backend/
├── server.py
├── adapters/
│   ├── mock_stage_adapter.py
│   ├── stm32_stage_adapter.py
│   └── slot_resolver.py
└── data/
    ├── rack_layout.json
    └── slot_map.json

frontend/
└── src/
    └── App.tsx

firmware/
└── stm32_stage_controller/
```

### slot_resolver.py

다음 변환을 담당한다.

```text
Tray ID
→ 현재 Slot
→ XC/ZR Anchor
→ X/Z 실제 목표좌표
```

### rack_layout.json

현재 어떤 Tray가 어떤 Slot에 있는지 저장한다.

Tray가 재배치되면 이 데이터가 변경될 수 있다.

### slot_map.json

각 물리 Slot을 구성하는 XC/ZR Anchor의 실제 Stage 좌표를 저장한다.

Rack의 Tray 배치가 바뀌더라도 Slot 자체의 물리 좌표는 유지된다.

### stm32_stage_adapter.py

Backend와 STM32 사이의 실제 Serial 통신을 담당한다.

---

## 5. STM32 명령 구조

주요 명령:

```text
PING
STATUS

ENABLE X 1
ENABLE Z 1

HOME X
HOME Z

MOVE X <delta_mm> <speed> <accel>
MOVE Z <delta_mm> <speed> <accel>

STOP ALL HARD
STOP ALL SOFT

ESTOP
RESET
```

MOVE 명령은 상대 이동이므로 Backend에서 다음 계산을 수행한다.

```text
이동거리 = 목표 위치 - 현재 위치
```

예:

```text
현재 X = 100 mm
목표 X = 339.0406 mm

delta = 239.0406 mm
```

따라서 STM32에는 다음과 같이 전달된다.

```text
MOVE X 239.041 20 50
```

---

## 6. 현재 이동 Profile

현재 Backend 설정:

```text
X축
Speed = 20 mm/s
Accel = 50 mm/s²

Z축
Speed = 10 mm/s
Accel = 30 mm/s²
```

이 값은 최종 운전값이 아니다.

실제 Stage 시험에서는 안전한 속도로 위치 정확도와 탈조 여부를 먼저 확인한 뒤
속도와 가속도를 최종 조정한다.

---

## 7. HOME 운용 정책

HOME은 작업지시서 하나가 끝날 때마다 수행하지 않는다.

기본 운용:

```text
장비 시작
→ HOME
→ 작업지시서 A
→ 작업지시서 B
→ 작업지시서 C
...
```

다음과 같은 경우 다시 HOME을 수행한다.

- 전원 재인가
- RESET 수행
- Stage 기준 위치 상실
- 탈조 의심
- 오류 복구
- 기구부를 수동으로 이동한 경우

작업 시작 시 X 또는 Z가 NOT HOMED이면
UI가 자동 작업을 차단하고 HOME 실행 여부를 확인한다.

---

## 8. Stage Control UI

UI 상단:

```text
설정
→ STAGE CONTROL
```

확인 가능한 정보:

- Stage Mode
- STM32 연결 상태
- X 현재 위치
- Z 현재 위치
- X HOME 상태
- Z HOME 상태
- X MIN / MAX Limit
- Z MIN / MAX Limit

제어 기능:

- X/Z HOME
- HARD STOP
- RESET
- STATUS 갱신

RESET을 수행하면 HOME 기준이 무효화되므로
다시 HOME을 수행해야 한다.

---

# 9. 현재까지 검증 완료

## HOME 미완료 차단

```text
작업 시작
→ X/Z NOT HOMED 확인
→ 작업 차단
→ HOME 실행 요청
```

검증 완료.

## Mapping 미완료 차단

TRAY 01 기준:

```text
TRAY 01
→ Slot 5
→ XC1 + ZR3
→ ZR3 미매핑
→ MAPPING_INCOMPLETE
→ 이동 차단
```

검증 완료.

## 정상 Mapping Mock Test

테스트용 Z 좌표:

```text
ZR1 = 100 mm
ZR2 = 350 mm
ZR3 = 600 mm
```

TRAY 01:

```text
TRAY 01
→ Slot 5
→ XC1 + ZR3
→ X = 2.7094 mm
→ Z = 600 mm
→ Stage 이동 성공
→ Stage 도착 확인
→ Picking 화면 진입
```

검증 완료.

테스트 완료 후 ZR1/ZR2/ZR3 테스트 값은 제거하고
기존 slot_map.json으로 복원하였다.

---

# 10. 실제 STM32 모드 실행

Serial Port 확인:

```bash
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

예:

```text
/dev/ttyACM0
```

Backend 실행:

```bash
cd ~/Downloads/tray-system

source .venv/bin/activate

export STAGE_MODE=stm32
export STAGE_SERIAL_PORT=/dev/ttyACM0

cd backend

uvicorn server:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd ~/Downloads/tray-system/frontend
npm run dev
```

정상 연결 시 UI에서 다음 상태를 확인한다.

```text
Stage : NORMAL
STM32 : CONNECTED
```

---

# 11. 실제 장비 작업 체크리스트

## STEP 1. 전원 인가 전

- [ ] X축 기구 체결 상태 확인
- [ ] Z축 기구 체결 상태 확인
- [ ] 커플링 / 벨트 / 리드스크류 풀림 확인
- [ ] Stage 전체 이동영역에 장애물 없는지 확인
- [ ] 케이블이 Stage 이동 중 걸리지 않는지 확인
- [ ] X MIN Limit Switch 확인
- [ ] X MAX Limit Switch 확인
- [ ] Z MIN Limit Switch 확인
- [ ] Z MAX Limit Switch 확인
- [ ] Limit Switch NC / COM 배선 확인
- [ ] Driver ENA / DIR / PUL 연결 확인
- [ ] STM32와 Driver GND 공통 확인
- [ ] 24V SMPS 연결 확인
- [ ] 분배단자 연결 확인
- [ ] 5A Fuse 확인
- [ ] HARD STOP 또는 전원 차단 즉시 가능한 상태 확보

처음부터 전체 자동 Workflow를 실행하지 않는다.

---

## STEP 2. STM32 연결 확인

- [ ] STM32 USB 연결
- [ ] `/dev/ttyACM*` 또는 `/dev/ttyUSB*` 확인
- [ ] PING 응답 확인
- [ ] STATUS 응답 확인
- [ ] X축 상태 확인
- [ ] Z축 상태 확인
- [ ] E-STOP 상태 확인
- [ ] Limit 입력 상태 확인

정상 PING:

```text
OK PONG
```

---

## STEP 3. Limit Switch 입력 확인

모터 HOME보다 먼저 Limit 입력 자체를 확인한다.

- [ ] X MIN Switch 수동 작동 → STATUS 변화
- [ ] X MAX Switch 수동 작동 → STATUS 변화
- [ ] Z MIN Switch 수동 작동 → STATUS 변화
- [ ] Z MAX Switch 수동 작동 → STATUS 변화

스위치 표시 방향 또는 상태가 이상하면 HOME을 진행하지 않는다.

---

## STEP 4. X축 HOME

- [ ] X축 HOME 방향 확인
- [ ] HOME이 MIN 방향으로 진행되는지 확인
- [ ] 저속으로 HOME 실행
- [ ] MIN Switch 접근 확인
- [ ] Limit 감지 후 정지 확인
- [ ] Back-off 확인
- [ ] 재접근 확인
- [ ] 최종 위치 약 0 mm 확인
- [ ] X homed = true 확인

방향이 잘못되었거나 예상과 다르게 움직이면 즉시 HARD STOP.

---

## STEP 5. Z축 HOME

- [ ] Z축 HOME 방향 확인
- [ ] 중력 방향과 하중 상태 확인
- [ ] 저속 HOME 실행
- [ ] MIN Switch 접근 확인
- [ ] Back-off 확인
- [ ] 재접근 확인
- [ ] 최종 위치 약 0 mm 확인
- [ ] Z homed = true 확인

Z축은 하중이 있으므로 X축보다 보수적으로 시험한다.

---

## STEP 6. 기존 X 좌표 재검증

현재 기록:

```text
XC1 = 2.7094 mm
XC2 = 339.0406 mm
```

### XC1

- [ ] HOME
- [ ] XC1 근처로 이동
- [ ] 실제 좌측 열 중심 확인
- [ ] 필요하면 새 좌표 Teaching

### XC2

- [ ] XC2 근처로 이동
- [ ] 실제 우측 열 중심 확인
- [ ] 필요하면 새 좌표 Teaching

---

## STEP 7. ZR1 / ZR2 / ZR3 Mapping

최종 속도 튜닝이 목적이 아니라
정확한 중심 좌표 확보가 목적이다.

안전한 저속으로 수행한다.

### ZR1

- [ ] 상단 행 중심 이동
- [ ] 기구 중심 확인
- [ ] Z 좌표 저장

### ZR2

- [ ] 중단 행 중심 이동
- [ ] 기구 중심 확인
- [ ] Z 좌표 저장

### ZR3

- [ ] 하단 행 중심 이동
- [ ] 기구 중심 확인
- [ ] Z 좌표 저장

최종적으로 다음 Anchor 5개가 모두 있어야 한다.

```text
XC1
XC2
ZR1
ZR2
ZR3
```

---

## STEP 8. 최종 slot_map.json 반영

실측 좌표를 다음 파일에 반영한다.

```text
backend/data/slot_map.json
```

6개 Slot 모두 다음 변환이 성공해야 한다.

```text
Tray
→ 현재 Slot
→ XC / ZR
→ X / Z 목표좌표
```

`MAPPING_INCOMPLETE`가 발생하면 자동운전을 진행하지 않는다.

---

## STEP 9. 실제 Backend 연결

```bash
export STAGE_MODE=stm32
export STAGE_SERIAL_PORT=/dev/ttyACM0
```

Stage Control에서 확인:

- [ ] Mode = STM32
- [ ] STM32 = CONNECTED
- [ ] X 위치 표시 정상
- [ ] Z 위치 표시 정상
- [ ] X HOME 상태 정상
- [ ] Z HOME 상태 정상
- [ ] Limit 표시 정상

---

## STEP 10. 첫 실제 이동

처음부터 6개 Slot을 전부 움직이지 않는다.

- [ ] HOME 완료
- [ ] HARD STOP 준비
- [ ] 충돌 위험이 낮은 Slot 하나 선정
- [ ] 저속 이동
- [ ] X 방향 정상
- [ ] Z 방향 정상
- [ ] UI 현재좌표 정상
- [ ] 목표 위치 정지 정상
- [ ] 실제 Rack 중심 일치
- [ ] 이상 진동 없음
- [ ] 탈조 없음

---

## STEP 11. 6개 Slot 검증

- [ ] Slot 1 = XC1 + ZR1
- [ ] Slot 2 = XC2 + ZR1
- [ ] Slot 3 = XC1 + ZR2
- [ ] Slot 4 = XC2 + ZR2
- [ ] Slot 5 = XC1 + ZR3
- [ ] Slot 6 = XC2 + ZR3

각 Slot의 실제 중심과 좌표를 확인한다.

---

## STEP 12. 반복 위치 검증

단순히 한 번 도착하는 것만 확인하지 않는다.

예:

```text
Slot 1
→ Slot 6
→ Slot 1
→ Slot 6
```

2~3회 반복하면서 다음을 확인한다.

- [ ] 반복 위치 오차
- [ ] 탈조
- [ ] 좌표 누적 오차
- [ ] 비정상 진동
- [ ] Driver 이상
- [ ] Limit 오작동

---

## STEP 13. 속도 / 가속도 튜닝

Mapping과 반복 위치 검증이 끝난 후 진행한다.

```text
안전한 저속
→ 반복 이동
→ 정상 확인
→ 속도 조금 증가
→ 반복 이동
→ 가속도 조정
→ 최종 운전값 선정
```

목표는 최대 속도가 아니라
탈조 없이 안정적으로 반복 가능한 운전 속도를 선정하는 것이다.

특히 Z축은 하중 때문에 보수적으로 설정한다.

---

## STEP 14. 전체 Workflow 실기 검증

마지막 단계에서 전체 자동 Workflow를 실행한다.

```text
작업지시서 OCR
→ Tray 결정
→ Slot 조회
→ X/Z 좌표 결정
→ 실제 Stage 이동
→ 도착 판정
→ Picking
```

확인:

- [ ] HOME 미완료 시 차단
- [ ] Mapping 미완료 시 차단
- [ ] 실제 Tray 위치로 이동
- [ ] UI X/Z 위치 실시간 갱신
- [ ] 도착 후 Picking 전환
- [ ] HARD STOP 정상
- [ ] E-STOP 정상

---

# 12. 실제 장비 시험 주의사항

1. HOME 없이 자동 좌표 이동하지 않는다.
2. 처음부터 높은 속도로 시험하지 않는다.
3. 기구 재조립 후 기존 XC1/XC2 좌표를 무조건 신뢰하지 않는다.
4. 사람의 손이나 물체가 이동영역에 있을 때 자동운전하지 않는다.
5. 장거리 이동 전 Limit Switch 상태부터 확인한다.
6. Z축은 하중에 따른 탈조 가능성을 특히 확인한다.
7. 현재 좌표 기준은 CARRIAGE_CENTER이므로 Gripper 장착 후 TCP Offset이 필요할 수 있다.
8. 실제 피킹 테스트 전 Gripper와 Rack 간 간섭 여부를 별도로 확인한다.

---

# 13. 현재 개발 브랜치

```text
feature/stage-mapping-integration
```

주요 Stage Integration 커밋:

```text
d7568fd  connect tray slots to taught stage coordinates
28efffd  integrate real STM32 stage protocol
e1c40f3  integrate STM32 stage adapter and mode switch
9a7115e  validate stage movement and poll real stage status
bd6a56c  add stage control and homing safety flow
93e44b7  add missing backend dependencies
```

---

# 14. 다음 실제 작업 요약

```text
1. 기구 / 배선 점검
2. STM32 연결
3. Limit 입력 확인
4. X HOME
5. Z HOME
6. XC1 / XC2 재검증
7. ZR1 / ZR2 / ZR3 Teaching
8. slot_map.json 반영
9. 실제 STM32 Mode 실행
10. Slot 1개 저속 이동
11. 6개 Slot 검증
12. 반복 위치 검증
13. 속도 / 가속도 튜닝
14. 전체 Workflow 실기 검증
```

---

## 2026-08-28 실제 Stage 매핑 및 Material Flow 통합

### 1. X-Z Stage 최종 실측 매핑

최종 선반 설치 위치를 기준으로 6개 물리 Slot의 X/Z 좌표를 직접 Teaching하였다.

| Tray | Slot | Mapping Key | X (mm) | Z (mm) |
|---|---:|---|---:|---:|
| TRAY 01 | 5 | XC1ZR3 | 322.7781 | 0.7906 |
| TRAY 02 | 6 | XC2ZR3 | 657.0219 | 0.7906 |
| TRAY 03 | 3 | XC1ZR2 | 322.7563 | 210.1719 |
| TRAY 04 | 4 | XC2ZR2 | 657.0219 | 206.2625 |
| TRAY 05 | 1 | XC1ZR1 | 322.7563 | 416.8813 |
| TRAY 06 | 2 | XC2ZR1 | 664.7531 | 416.2781 |

기존의 X Column / Z Row 독립 조합 방식 대신,
각 Slot의 실제 X/Z 좌표를 직접 사용하는 6점 매핑 방식으로 변경하였다.

실제 STM32 Stage에서 6개 Tray 위치로 이동 시험을 수행하였으며,
모든 위치에서 정상 이동을 확인하였다.

실제 Teaching 시 사용한 물리 기준은 다음과 같다.

- X축 기준: Z축 모터 박스의 오른쪽 측면
- Z축 기준: Z축 모터 박스의 아래쪽 면

향후 ArUco 보정 및 Gripper TCP Offset 적용 시 위 물리 기준을 고려해야 한다.


### 2. Stage 실제 하드웨어 설정 및 검증

STM32 Stage 통신 및 이동 설정은 다음과 같다.

- USART: 115200 bps
- X 이동 속도: 15 mm/s
- Z 이동 속도: 15 mm/s
- X/Z 가속도: 20 mm/s²
- HOME Timeout: 60 s → 300 s
- Z축 설치 방향에 맞게 DIR polarity 수정

장거리 HOME 수행 시 기존 60초 제한으로 중간 정지하는 문제를 방지하기 위해
Backend의 HOME Timeout을 300초로 변경하였다.

실제 장비에서 다음 항목을 확인하였다.

- X/Z HOME
- X/Z Limit
- HARD STOP
- 6개 Tray Slot 실제 이동


### 3. Conveyor Handoff 위치 추가

Tray를 Conveyor에 전달하거나 반환 Tray를 받기 위한 시스템 위치를
Tray Slot 좌표와 분리하여 관리하도록 구성하였다.

추가 파일:

`backend/data/system_positions.json`

현재 임시 Conveyor Handoff 좌표:

- X = 0.0000 mm
- Z = 210.1719 mm

Z 좌표는 현재 좌측 중단 Slot(XC1ZR2)의 높이를 기준으로 설정하였다.

현재 값은 실제 Conveyor 설치 전 임시 위치이며,
향후 Conveyor 설치 후 실제 전달 위치를 다시 Teaching하여 변경한다.


### 4. Stage Handoff 이동 기능 추가

다음 기능을 추가하였다.

- `backend/adapters/system_position_resolver.py`
- `StageAdapter.move_to_handoff()`
- `MockStageAdapter.move_to_handoff()`
- `STM32StageAdapter.move_to_handoff()`
- `POST /stage/move-to-handoff`

따라서 Stage는 Tray Slot뿐 아니라
Conveyor Handoff 위치로도 절대좌표 이동할 수 있다.


### 5. Stage 공급과 작업자 작업 병렬화

기존 Workflow는 아래와 같은 직렬 구조였다.

`TRAY 이동 → 작업자 Picking → Vision 검수 → Item Complete → 다음 TRAY 이동`

이 구조에서는 작업자가 현재 Tray를 처리하는 동안
Stage가 다음 Tray를 공급하지 못하고 대기하게 된다.

신속한 Tray 공급을 위해 Stage Material Flow와
작업자 Picking/검수 Workflow를 분리하였다.

최종 공급 개념은 다음과 같다.

`TRAY 이동 → ArUco 정렬 → Gripper 파지/인출 → Carriage Load Cell 확인 → Conveyor Handoff → 즉시 다음 Tray 공급`

Conveyor는 여러 Tray를 순차적으로 수용할 수 있다고 가정하며,
작업자는 이미 전달된 Tray를 Stage의 다음 공급 동작과 독립적으로 처리한다.


### 6. MaterialFlowController 추가

Stage의 Tray 공급 및 회수 흐름을 별도로 관리하기 위해
다음 파일을 추가하였다.

`backend/workflow/material_flow_controller.py`

Material Flow는 기존 작업자 WorkflowController와 독립적으로 동작한다.

Supply 주요 상태:

- `TRAY_MOVING`
- `ARUCO_ALIGN`
- `EXTRACTING`
- `HANDOFF_MOVING`
- `SUPPLY_COMPLETE`

Return 주요 상태:

- `RETURN_WAIT`
- `RETURN_PICKING`
- `RETURNING_TO_SLOT`
- `RETURN_INSERTING`
- `RETURN_TO_HANDOFF`
- `ALL_RETURNED`

Supply Queue와 Return Queue를 별도로 관리하여
Stage 공급/회수와 작업자 Picking/검수가 병렬적으로 진행될 수 있도록 구성하였다.


### 7. 최종 Tray 공급 흐름

예를 들어 작업지시서에서 TRAY 01과 TRAY 02가 필요한 경우:

`TRAY 01 → Handoff → TRAY 02 → Handoff → SUPPLY_COMPLETE → RETURN_WAIT`

각 Tray의 실제 공급 단계는 다음과 같다.

`Tray Slot 이동 → ArUco 미세 정렬 → Gripper 파지/인출 → Carriage Load Cell 확인 → Handoff 이동 → Conveyor 전달`

현재 ArUco, Gripper, Carriage Load Cell, Conveyor가 준비되지 않은 단계는
소프트웨어 검증을 위해 성공한 것으로 처리하고 있으며,
향후 실제 장비의 완료 신호로 교체한다.


### 8. Tray 자동 회수 흐름

모든 공급이 완료되면 Stage는 Conveyor Handoff 위치에서
반환 Tray를 기다리는 `RETURN_WAIT` 상태로 진입한다.

작업자가 Tray 사용을 완료하면 해당 Tray가 Return Queue에 등록된다.

반환 Tray는 다음 순서로 처리한다.

`Conveyor 역방향 이동 → Handoff 도착 → ArUco 자동 인식 → Gripper 파지 → Carriage Load Cell 확인 → 해당 Tray Slot 이동 → Tray 삽입/해제 → Handoff 복귀`

반환 순서는 고정하지 않는다.

각 Tray에 부착된 ArUco Marker를 통해 실제 Handoff에 도착한 Tray ID를 인식하고,
해당 Tray의 복귀 목표 Slot을 결정하도록 설계하였다.

따라서 TRAY 02가 TRAY 01보다 먼저 반환되어도
ArUco 인식 결과에 따라 TRAY 02부터 정상 복귀시킬 수 있다.


### 9. Load Cell 역할

Load Cell은 총 2개를 사용하는 구조를 기준으로 한다.

캐리지 측 Load Cell:

`Gripper 인출 동작 완료 + Load Cell 하중 증가 → Tray 적재/인출 성공 보조 검증`

Conveyor 끝단 작업자 측 Load Cell:

`작업자 Picking 이후 Tray 중량 기반 최종 검수`

캐리지 Load Cell만으로 완전 인출 여부를 단독 판정하지 않고,
Gripper의 인출 완료 조건과 함께 사용하는 것을 기준으로 한다.


### 10. 현재 Mock/BYPASS 처리 항목

현재 실제 장비가 준비되지 않은 다음 기능은
Material Flow 내부에서 성공한 것으로 처리하여 전체 흐름을 검증하였다.

- ArUco 기반 위치 미세 보정
- Gripper 파지 및 인출
- Carriage Load Cell 기반 인출 검증
- Conveyor 전달 및 반환 신호
- 반환 Tray 실제 ArUco 인식
- Tray 삽입 및 Gripper 해제

향후 장비가 준비되면 현재 Material Flow 구조는 유지하고,
각 BYPASS 구간을 실제 센서 및 액추에이터의 완료 신호로 교체한다.


### 11. 검증 결과

Backend 검증:

- `python3 -m py_compile` 성공
- `git diff --check` 이상 없음

Frontend 검증:

- `npm run build` 성공
- 전체 Mock Workflow 실행 완료

Mock Material Flow 최종 상태:

- Supply Queue: `[1, 2]`
- Supplied Trays: `[1, 2]`
- `supply_state = SUPPLY_COMPLETE`
- `supply_complete = true`
- Returned Trays: `[1, 2]`
- `return_state = ALL_RETURNED`
- `all_returned = true`
- 최종 `WORK COMPLETE` 화면 진입 확인

실제 Stage 검증:

- 6개 Slot 실물 이동 정상 확인


### 12. 관련 주요 커밋

- `ba7e7d3` — 실제 6개 Slot Stage 매핑 및 이동 검증
- `54808d5` — 병렬 Tray 공급 및 자동 회수 Material Flow 추가


### 13. 향후 작업

1. 배선 정리 후 실제 Stage 연속 왕복 동선 검증
2. `TRAY → HANDOFF → 다음 TRAY` 실제 연속 이동 시험
3. `HANDOFF → TRAY Slot → HANDOFF` 실제 회수 이동 시험
4. ArUco 기반 미세 위치 보정 실제 연동
5. Gripper 파지 / 인출 / 삽입 실제 연동
6. Carriage Load Cell 기반 인출 성공 검증
7. 실제 Conveyor 전달 / 반환 신호 연동
8. UI에 Stage Supply / Return 상태 시각화 보강
9. 전체 시스템 통합 시험

