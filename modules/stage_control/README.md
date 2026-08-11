# Stage Control

PC에서 STM32 기반 2축 스테이지를 제어하기 위한 모듈입니다.

## 역할

- STM32 시리얼 통신
- X축 수동 이동 명령
- Z축 수동 이동 명령
- Homing 명령
- 정지 명령
- 상태 및 ACK 응답 확인
- 액추에이터 단독 동작 시험

## Firmware

STM32 펌웨어는 다음 디렉터리에 있습니다.

`firmware/stm32_stage_controller/`

## Planned Files

추후 다음과 같은 파일을 추가할 예정입니다.

- `stage_serial.py` : STM32 통신 계층
- `manual_control.py` : 터미널 기반 수동 제어
- `manual_gui.py` : GUI 기반 수동 제어