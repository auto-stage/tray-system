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
