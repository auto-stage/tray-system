# ArUco Alignment Operation Modes

자동 Material Flow의 ArUco 정렬 동작은 세 모드로 분리한다.

- `disabled`: ArUco alignment callback을 연결하지 않고 기존 BYPASS 동작을 사용한다.
- `observe_only`: expected Tray의 ArUco ID/Pose를 관측하지만 Stage correction은 하지 않는다.
- `closed_loop`: 기존 `/vision/align` 폐루프를 자동 Material Flow에서 사용한다.

설정 우선순위:

1. `ARUCO_ALIGNMENT_MODE` 환경변수
2. `system.yaml > integration > correction_loop > mode`
3. 기존 `correction_loop.enabled` fallback

기존 호환 규칙:

- `enabled=false` -> `disabled`
- `enabled=true` -> `closed_loop`

권장 개발 순서:

1. Stage 기본 실기 검증: `disabled`
2. ArUco ID/Pose/RPY 실기 검증: `observe_only`
3. Camera-to-Stage 실측 및 보정 완료 후: `closed_loop`

`/vision/aruco`는 관측용 진단 API, `/vision/align`은 수동 폐루프 진단 API로 유지한다.
