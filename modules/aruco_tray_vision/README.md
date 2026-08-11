# ArUco Tray 6DoF / 3D Grip System

## 현재 구현

- 실시간 노트북/USB 카메라 영상
- ArUco DICT_4X4_50 ID 및 코너 검출
- 캘리브레이션 전: ID / 픽셀 위치 / 영상 평면 Yaw
- 캘리브레이션 후: solvePnP 기반 X/Y/Z + Roll/Pitch/Yaw
- Marker→Grip 3D rigid offset 변환
- Roll/Pitch/Yaw 허용범위 검사
- Camera→Stage 4×4 extrinsic transform 연결 구조
- PyQt6 GUI / 체커보드 카메라 캘리브레이션
- STM SerialStage / Gripper 통합 골격

## 설치 및 실행

```bash
pip install -r requirements.txt
python main.py
```

GUI 없이 핵심 3D 계산 확인:

```bash
python main.py --self-test
pytest -q
```

## 지금 테스트

카메라 프로파일이 미캘리브레이션이어도 ArUco ID, 픽셀 중심, 영상 Yaw는 확인할 수 있습니다.
6DoF와 mm 단위 XYZ/3D 파지점은 실제 카메라 캘리브레이션 후 활성화됩니다.

## 카메라 캘리브레이션

GUI의 `카메라 캘리브레이션` 탭에서 체커보드를 다양한 위치/거리/기울기로 최소 10장(권장 15장 이상) 추가하고 계산/저장합니다.
노트북 카메라와 최종 외장 웹캠은 각각 별도 YAML로 캘리브레이션합니다.

## 실제 장비 도착 후 교체할 값

1. `config/trays.yaml`
   - `marker_size_mm`: 최종 실제 마커 검은 외곽 한 변
   - `marker_to_grip_mm`: 마커 좌표계 기준 실제 파지점 X/Y/Z 치수
2. `config/system.yaml`
   - Roll/Pitch/Yaw 실제 파지 허용범위
   - Camera→Stage 4×4 extrinsic matrix
   - 실제 2축이 X/Y인지 X/Z인지 축 매핑
   - Camera→Gripper TCP offset
3. `aruco_tray/stage_serial.py`
   - 실제 STM 명령/응답 프로토콜
4. `aruco_tray/gripper_serial.py`
   - 실제 그리퍼 명령/완료 응답

## 중요한 통합 조건

- EYE_IN_HAND: 카메라가 그리퍼/스테이지와 같이 움직이면 트레이 ArUco 재관측으로 실제 상대 잔여오차 폐루프 보정이 가능.
- FIXED_OVERHEAD: 카메라가 고정되고 트레이 마커만 보면 트레이 자세는 알 수 있지만 스테이지가 명령보다 덜 움직인 오차는 직접 관측할 수 없음. 이 경우 이동부 마커 또는 엔코더/리니어스케일 피드백이 필요.

현재 Camera→Stage extrinsic은 의도적으로 미설정 상태이므로 GUI 시퀀스는 Stage 변환 단계에서 정상적으로 중단됩니다. 실제 카메라와 스테이지를 고정 설치한 뒤 해당 캘리브레이션을 수행해야 합니다.
