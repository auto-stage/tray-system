# Tray / Part Mapping

물리 Tray의 신원과 현재 적재 부품은 분리해서 관리한다.

## 물리 Tray / ArUco ID

`modules/aruco_tray_vision/config/trays.yaml`

- ArUco ID 1~6은 각각 Tray 1~6의 고정 물리 신원이다.
- Marker size, Marker-to-Grip offset, geometry calibration도 이 파일에 둔다.
- Tray 안의 부품을 바꿔도 ArUco ID는 바꾸지 않는다.

## 현재 적재 부품

`backend/config/parts.yaml`의 각 부품 `tray_id`가 단일 기준이다.

현재 기본 배치:

| Tray | Part |
|---:|---|
| 1 | `t_bolt` |
| 2 | `socket_head_bolt` |
| 3 | `corner_bracket` |
| 4 | `flange_nut` |
| 5 | `t_nut` |
| 6 | `l_bracket` |

실제 적재 위치를 바꾸려면 해당 부품의 `tray_id`만 변경한다.
`tray_id`는 1~6 범위이며 서로 중복될 수 없다.

예: T 볼트와 플랜지 너트의 Tray를 서로 바꾸는 경우

```yaml
t_bolt:
  tray_id: 4

flange_nut:
  tray_id: 1
```

작업지시 OCR -> `parts.yaml` -> Material Flow 공급 Queue ->
`stage.move_to_tray(tray_id)` -> ArUco expected Tray 확인 순으로 전달된다.

`inventory.json`은 재고 수량 상태를 보존하며,
UI에 표시되는 Tray/품명/규격은 Backend에서 `parts.yaml` 값을 우선 사용한다.
설정 변경 후에는 Backend 재시작 및 Frontend 새로고침을 권장한다.
