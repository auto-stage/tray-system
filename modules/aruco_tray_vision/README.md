# ArUco Tray 6DoF / Moving X/Z Carriage Vision

## 현재 구현

- 실시간 노트북/USB 카메라 영상
- ArUco DICT_4X4_50 ID 및 코너 검출
- 캘리브레이션 전: ID / 픽셀 위치 / 영상 평면 Yaw
- Intrinsic 캘리브레이션 후: solvePnP 기반 X/Y/Z + Roll/Pitch/Yaw
- Marker→Grip 3D rigid offset 변환
- Roll/Pitch/Yaw 허용범위 검사
- ArUco ID 1~6 고정 식별
- 이동부 고정 Camera→Carriage 변환 구조
- X/Z 상대 보정량 산출 구조
- 깊이 방향 오차 출력(그리퍼 전후진 액추에이터 선정 전 모니터링 전용)
- PyQt6 GUI / 체커보드 카메라 캘리브레이션

## 설치 및 실행

```bash
pip install -r requirements.txt
python main.py
```

GUI 없이 핵심 계산 확인:

```bash
python main.py --self-test
pytest -q
```

## 카메라 구조

최종 ArUco 카메라는 X/Z 최종 이동부에 고정됩니다.
따라서 고정 Camera→Stage 절대 변환 하나를 사용하는 것이 아니라,
고정 Camera→Carriage 변환과 현재 Stage X/Z 위치를 결합합니다.

```text
ArUco 6DoF / Grip Target
        ↓
Camera → Carriage
        ↓
X 오차 → Stage X 보정
Z 오차 → Stage Z 보정
Y 오차 → Gripper 전후진 깊이 정보
```

그리퍼 전후진 액추에이터는 아직 선정되지 않았으므로 `UNDECIDED` 상태이며 실제 깊이축 명령은 생성하지 않습니다.

## 실제 장비 도착 후 확정할 값

1. `config/trays.yaml`
   - `marker_size_mm`: 실제 인쇄된 ArUco 검은 외곽 한 변
   - `marker_to_grip_mm`: 마커 좌표계 기준 실제 파지점 X/Y/Z
   - 실측 완료 후 `geometry_calibrated: true`
2. `config/system.yaml`
   - Roll/Pitch/Yaw 실제 파지 허용범위
   - Camera→Carriage 4×4 변환
   - 후퇴 상태의 Gripper 기준점
   - X/Z 정렬 허용오차
   - 1회 최대 Vision 보정량
   - 실측 완료 후 `moving_camera_alignment.calibrated: true`
3. 그리퍼 전후진 액추에이터 선정 후
   - `gripper_depth_axis.mode`
   - 이동 범위 / 기준점 / 구동 Adapter

## 폐루프 보정

예정 시퀀스:

```text
Slot Teaching 좌표로 1차 이동
→ ArUco 재관측
→ X/Z 오차 계산
→ Stage 상대 보정
→ ArUco 재관측
→ 허용오차 이내 확인
```

`correction_loop.enabled`는 실제 장착/캘리브레이션 전까지 `false`로 유지합니다.
