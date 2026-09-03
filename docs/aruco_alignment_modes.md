# ArUco Alignment Operation Modes

자동 Material Flow의 ArUco 정렬은 세 모드로 분리한다.

- `disabled`: ArUco callback을 연결하지 않고 기존 BYPASS 동작을 사용한다.
- `observe_only`: expected Tray의 ArUco ID를 확인하지만 Stage correction은 하지 않는다.
- `closed_loop`: ArUco 관측 -> X/Z correction -> 재관측을 수행한다.

설정 우선순위:

1. `ARUCO_ALIGNMENT_MODE` 환경변수
2. `system.yaml > integration > correction_loop > mode`
3. 기존 `correction_loop.enabled` fallback

기존 호환 규칙:

- `enabled=false` -> `disabled`
- `enabled=true` -> `closed_loop`

권장 개발 순서:

1. Stage 기본 실기 검증: `disabled`
2. ArUco ID / Tray ID / Pose / RPY 검증: `observe_only`
3. Camera-to-Carriage 및 X/Z 실측 보정 완료 후: `closed_loop`

## Marker / Tray identification policy

ArUco Marker/Tray 식별과 Stage correction calibration은 별개의 단계다.

```text
Marker ID 검출
-> Tray ID 매칭
-> Pose/RPY 관측
-> Stage correction readiness
-> 실제 X/Z correction
```

`observe_only`에서는 Camera-to-Carriage, Gripper reference,
X/Z tolerance 및 최대 correction 값이 준비되지 않았더라도
expected Tray의 ArUco ID가 정상 식별되면 성공한다.

단, 다른 Tray가 검출되면 `TRAY_ID_MISMATCH`로 실패한다.
Stage correction은 절대 실행하지 않으며,
`ready_for_stage_correction=false`는 관측 실패가 아니라
"자동 보정 미준비"를 의미한다.

`closed_loop`에서만 실제 Stage correction용 실측값과
`correction_loop.enabled=true`를 요구한다.
