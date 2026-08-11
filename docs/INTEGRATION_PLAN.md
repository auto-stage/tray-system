# Future Integration Plan

현재 세 파트는 독립 실행 상태입니다. 통합 시에는 기존 모듈 내부 로직을 직접 섞기보다 상위 orchestration 계층에서 연결하는 방식을 권장합니다.

```text
Work Order Image
      |
      v
Work Order OCR
(part / spec / quantity / tray_id)
      |
      v
ArUco Tray Vision
(tray pose / 3D grip target)
      |
      v
Camera -> Stage Transform
      |
      v
Stage Command Interface
      |
      v
STM32 2-axis Stage Controller
```

## 통합 시 우선 확정할 인터페이스

- OCR 출력 형식: `part_name`, `spec`, `quantity`, `tray_id`
- ArUco 입력: OCR에서 선택된 `tray_id`
- Vision 출력: Stage 좌표계의 목표 위치
- PC↔STM32 직렬 프로토콜: 이동, 정지, 원점복귀, 상태 조회, 에러 응답
- 실제 Stage 축 매핑: X/Y 또는 X/Z
- Camera→Stage extrinsic calibration

현재 저장소에서는 위 인터페이스를 구현하지 않았으며 각 기존 파트를 그대로 보존합니다.
