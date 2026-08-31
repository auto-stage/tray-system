"""
Tray 실제 좌표 설정 파일.

중요:
- 지금은 실제 기구 좌표를 모르므로 None으로 둡니다.
- STM 담당자가 실제 레일에서 Tray 1~6 좌표를 측정한 뒤
  x / z 값을 mm 단위로 채우면 됩니다.
- speed / accel도 실제 기구에 맞춰 조정 가능합니다.
"""

TRAY_POSITIONS = {
    1: {
        "x": None,
        "z": None,
    },
    2: {
        "x": None,
        "z": None,
    },
    3: {
        "x": None,
        "z": None,
    },
    4: {
        "x": None,
        "z": None,
    },
    5: {
        "x": None,
        "z": None,
    },
    6: {
        "x": None,
        "z": None,
    },
}


MOVE_PROFILE = {
    "x": {
        "speed_mm_s": 50.0,
        "accel_mm_s2": 100.0,
    },
    "z": {
        "speed_mm_s": 30.0,
        "accel_mm_s2": 80.0,
    },
}


POSITION_TOLERANCE_MM = 0.5
STATUS_POLL_INTERVAL_SEC = 0.10
MOVE_TIMEOUT_SEC = 30.0
HOME_TIMEOUT_SEC = 45.0