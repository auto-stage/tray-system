from __future__ import annotations

import glob
import json
import os
import threading
import time
from typing import Optional

import serial

from .stage_adapter import StageAdapter
from .slot_resolver import resolve_tray_target


# 기존 현장 매핑 프로그램에서 실제 사용한 값 기준
BAUDRATE = 115200
STATUS_POLL_SEC = 0.30

HOME_TIMEOUT_SEC = 60.0
MOVE_TIMEOUT_SEC = 120.0

POSITION_TOLERANCE_MM = 0.10

MOVE_PROFILE = {
    "X": {
        "speed": 20.0,
        "accel": 50.0,
    },
    "Z": {
        "speed": 10.0,
        "accel": 30.0,
    },
}


class STM32StageAdapter(StageAdapter):

    def __init__(
        self,
        port: Optional[str] = None,
        baudrate: int = BAUDRATE,
        read_timeout: float = 0.4,
        auto_connect: bool = True,
    ):
        self.port = port
        self.baudrate = baudrate
        self.read_timeout = read_timeout

        self._serial: Optional[
            serial.Serial
        ] = None

        self._io_lock = threading.Lock()
        self._motion_lock = threading.Lock()

        self.state = "DISCONNECTED"

        self.current_tray = None
        self.current_target = None

        self.last_error = None
        self.paused = False

        self._status = {
            "estop": False,
            "x": self._empty_axis(),
            "z": self._empty_axis(),
        }

        if auto_connect:
            self.connect()

    # ========================================================
    # 기본 상태
    # ========================================================

    @staticmethod
    def _empty_axis():
        return {
            "mode": "UNKNOWN",
            "pos_mm": 0.0,
            "steps": 0,
            "hz": 0,
            "enabled": False,
            "homed": False,
            "min": False,
            "max": False,
        }

    # ========================================================
    # Serial 연결
    # ========================================================

    def _resolve_port(self):

        if self.port:
            return self.port

        env_port = os.getenv(
            "STAGE_SERIAL_PORT"
        )

        if env_port:
            return env_port

        candidates = sorted(
            glob.glob("/dev/ttyACM*")
            +
            glob.glob("/dev/ttyUSB*")
        )

        if candidates:
            return candidates[0]

        raise RuntimeError(
            "STM32 Serial 포트를 찾지 못했습니다. "
            "STAGE_SERIAL_PORT=/dev/ttyACM0 "
            "형태로 지정하세요."
        )

    def connect(self):

        if (
            self._serial
            and
            self._serial.is_open
        ):
            return

        self.port = self._resolve_port()

        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.read_timeout,
            write_timeout=1.0,
        )

        time.sleep(0.3)

        self._serial.reset_input_buffer()

        result = self._send_expect(
            "PING",
            success="OK PONG",
            timeout=2.0,
        )

        if not result["success"]:

            self.close(
                safe=False
            )

            raise RuntimeError(
                "STM32 PING 실패: "
                + result["message"]
            )

        self.state = "CONNECTED"
        self.last_error = None

        self.refresh_status()

    def close(
        self,
        safe: bool = True,
    ):

        ser = self._serial

        if not ser:
            self.state = "DISCONNECTED"
            return

        if (
            safe
            and
            ser.is_open
        ):

            try:

                self._send_expect(
                    "STOP ALL HARD",
                    timeout=1.0,
                )

                self._send_expect(
                    "ENABLE X 0",
                    timeout=1.0,
                )

                self._send_expect(
                    "ENABLE Z 0",
                    timeout=1.0,
                )

            except Exception:
                pass

        try:

            ser.close()

        finally:

            self._serial = None
            self.state = "DISCONNECTED"

    # ========================================================
    # 저수준 Serial
    # ========================================================

    def _write_line(
        self,
        command: str,
    ):

        if (
            not self._serial
            or
            not self._serial.is_open
        ):

            raise RuntimeError(
                "STM32 Serial이 연결되어 있지 않습니다."
            )

        self._serial.write(
            (
                command.strip()
                + "\n"
            ).encode("ascii")
        )

        self._serial.flush()

    def _read_line(self):

        if (
            not self._serial
            or
            not self._serial.is_open
        ):
            return None

        raw = (
            self._serial.readline()
        )

        if not raw:
            return None

        return raw.decode(
            "ascii",
            errors="replace",
        ).strip()

    def _send_expect(
        self,
        command: str,
        success: str = "OK",
        timeout: float = 2.0,
    ):

        deadline = (
            time.monotonic()
            + timeout
        )

        with self._io_lock:

            try:

                self._write_line(
                    command
                )

                received = []

                while (
                    time.monotonic()
                    <
                    deadline
                ):

                    line = (
                        self._read_line()
                    )

                    if not line:
                        continue

                    received.append(
                        line
                    )

                    # 혹시 STATUS JSON이 섞여 들어온 경우
                    if line.startswith(
                        "{"
                    ):

                        self._consume_status_line(
                            line
                        )

                        continue

                    if line.startswith(
                        "ERR"
                    ):

                        self.last_error = line

                        return {
                            "success": False,
                            "message": line,
                            "received": received,
                        }

                    if line.startswith(
                        success
                    ):

                        self.last_error = None

                        return {
                            "success": True,
                            "message": line,
                            "received": received,
                        }

                self.last_error = (
                    f"TIMEOUT: {command}"
                )

                return {
                    "success": False,
                    "message":
                        self.last_error,
                    "received":
                        received,
                }

            except Exception as exc:

                self.last_error = str(
                    exc
                )

                return {
                    "success": False,
                    "message": str(exc),
                }

    # ========================================================
    # STATUS
    # ========================================================

    def _consume_status_line(
        self,
        line: str,
    ):

        payload = json.loads(
            line
        )

        if (
            payload.get("type")
            !=
            "status"
        ):

            raise ValueError(
                "알 수 없는 STATUS JSON"
            )

        self._status["estop"] = bool(
            payload.get(
                "estop",
                0,
            )
        )

        for axis in (
            "x",
            "z",
        ):

            raw = payload.get(
                axis,
                {},
            )

            self._status[axis] = {
                "mode":
                    str(
                        raw.get(
                            "mode",
                            "UNKNOWN",
                        )
                    ),

                "pos_mm":
                    float(
                        raw.get(
                            "pos_mm",
                            0.0,
                        )
                    ),

                "steps":
                    int(
                        raw.get(
                            "steps",
                            0,
                        )
                    ),

                "hz":
                    int(
                        raw.get(
                            "hz",
                            0,
                        )
                    ),

                "enabled":
                    bool(
                        raw.get(
                            "enabled",
                            0,
                        )
                    ),

                "homed":
                    bool(
                        raw.get(
                            "homed",
                            0,
                        )
                    ),

                "min":
                    bool(
                        raw.get(
                            "min",
                            0,
                        )
                    ),

                "max":
                    bool(
                        raw.get(
                            "max",
                            0,
                        )
                    ),
            }

        return payload

    def refresh_status(self):

        deadline = (
            time.monotonic()
            + 2.0
        )

        with self._io_lock:

            try:

                self._write_line(
                    "STATUS"
                )

                while (
                    time.monotonic()
                    <
                    deadline
                ):

                    line = (
                        self._read_line()
                    )

                    if not line:
                        continue

                    if line.startswith(
                        "ERR"
                    ):

                        raise RuntimeError(
                            line
                        )

                    if line.startswith(
                        "{"
                    ):

                        self._consume_status_line(
                            line
                        )

                        self._sync_state_name()

                        self.last_error = None

                        return (
                            self._snapshot()
                        )

                raise TimeoutError(
                    "STATUS 응답 시간 초과"
                )

            except Exception as exc:

                self.last_error = str(
                    exc
                )

                return (
                    self._snapshot()
                )

    def _sync_state_name(self):

        if self._status["estop"]:

            self.state = "ESTOPPED"

            return

        modes = {
            self._status["x"]["mode"],
            self._status["z"]["mode"],
        }

        if "FAULT" in modes:

            self.state = "ERROR"

        elif any(
            mode != "IDLE"
            for mode in modes
        ):

            self.state = "MOVING"

        elif self.paused:

            self.state = "PAUSED"

        else:

            self.state = "READY"

    def _snapshot(self):

        x = self._status["x"]
        z = self._status["z"]

        return {
            "connected":
                bool(
                    self._serial
                    and
                    self._serial.is_open
                ),

            "mock": False,

            "state":
                self.state,

            "estop":
                self._status["estop"],

            "homed": {
                "x": x["homed"],
                "z": z["homed"],
            },

            "enabled": {
                "x": x["enabled"],
                "z": z["enabled"],
            },

            "current_tray":
                self.current_tray,

            "position": {
                "x": x["pos_mm"],
                "z": z["pos_mm"],
            },

            "mode": {
                "x": x["mode"],
                "z": z["mode"],
            },

            "limits": {
                "X_MIN": x["min"],
                "X_MAX": x["max"],
                "Z_MIN": z["min"],
                "Z_MAX": z["max"],
            },

            "current_target":
                self.current_target,

            "last_error":
                self.last_error,
        }

    def get_status(self):

        if (
            self._serial
            and
            self._serial.is_open
        ):

            return (
                self.refresh_status()
            )

        return self._snapshot()

    # ========================================================
    # HOME
    # ========================================================

    def _home_axis(
        self,
        axis: str,
    ):

        result = (
            self._send_expect(
                f"ENABLE {axis} 1"
            )
        )

        if not result[
            "success"
        ]:
            return result

        result = (
            self._send_expect(
                f"HOME {axis}"
            )
        )

        if not result[
            "success"
        ]:
            return result

        deadline = (
            time.monotonic()
            +
            HOME_TIMEOUT_SEC
        )

        while (
            time.monotonic()
            <
            deadline
        ):

            status = (
                self.refresh_status()
            )

            axis_status = (
                self._status[
                    axis.lower()
                ]
            )

            if (
                status["estop"]
                or
                axis_status["mode"]
                ==
                "FAULT"
            ):

                return {
                    "success": False,
                    "message":
                        f"{axis} HOME 중 FAULT/ESTOP",
                    "status":
                        status,
                }

            if (
                axis_status[
                    "mode"
                ]
                ==
                "IDLE"
                and
                axis_status[
                    "homed"
                ]
            ):

                return {
                    "success": True,
                    "message":
                        f"{axis} HOME 완료",
                    "status":
                        status,
                }

            time.sleep(
                STATUS_POLL_SEC
            )

        # TIMEOUT이면 HOME을 계속 두지 않고 즉시 HARD STOP
        self._send_expect(
            f"STOP {axis} HARD",
            timeout=1.0,
        )

        self.last_error = (
            f"{axis} HOME TIMEOUT"
        )

        return {
            "success": False,
            "message":
                self.last_error,
            "status":
                self.refresh_status(),
        }

    def home(self):

        with self._motion_lock:

            self.paused = False
            self.state = "HOMING"

            # 기존 Mapping GUI와 동일하게 X -> Z 순차 HOME
            for axis in (
                "X",
                "Z",
            ):

                result = (
                    self._home_axis(
                        axis
                    )
                )

                if not result[
                    "success"
                ]:

                    self.state = "ERROR"

                    return result

            self.current_tray = None
            self.current_target = None

            self.state = "READY"

            return {
                "success": True,
                "message":
                    "X/Z HOME 완료",
                "status":
                    self.refresh_status(),
            }

    # ========================================================
    # 절대 목표좌표 -> STM 상대 MOVE
    # ========================================================

    def _move_axis(
        self,
        axis: str,
        target_mm: float,
    ):

        status = (
            self.refresh_status()
        )

        if not status[
            "connected"
        ]:

            return {
                "success": False,
                "message":
                    "STM32 Serial 연결이 없습니다.",
            }

        axis_status = (
            self._status[
                axis.lower()
            ]
        )

        if status["estop"]:

            return {
                "success": False,
                "message":
                    "ESTOP 상태입니다.",
            }

        if not axis_status[
            "homed"
        ]:

            return {
                "success": False,
                "message":
                    f"{axis}축 HOME이 필요합니다.",
            }

        if (
            axis_status["mode"]
            !=
            "IDLE"
        ):

            return {
                "success": False,
                "message":
                    f"{axis}축이 "
                    f"{axis_status['mode']} "
                    "상태입니다.",
            }

        # 핵심:
        # slot_map은 절대좌표,
        # STM MOVE는 상대거리이므로 차이를 계산
        delta = (
            float(target_mm)
            -
            axis_status["pos_mm"]
        )

        if (
            abs(delta)
            <=
            POSITION_TOLERANCE_MM
        ):

            return {
                "success": True,
                "message":
                    f"{axis}축 이미 목표 위치",
                "delta_mm": 0.0,
                "status": status,
            }

        result = (
            self._send_expect(
                f"ENABLE {axis} 1"
            )
        )

        if not result[
            "success"
        ]:
            return result

        profile = (
            MOVE_PROFILE[axis]
        )

        command = (
            f"MOVE {axis} "
            f"{delta:.6g} "
            f"{profile['speed']:.6g} "
            f"{profile['accel']:.6g}"
        )

        result = (
            self._send_expect(
                command
            )
        )

        if not result[
            "success"
        ]:
            return result

        deadline = (
            time.monotonic()
            +
            MOVE_TIMEOUT_SEC
        )

        seen_busy = False

        while (
            time.monotonic()
            <
            deadline
        ):

            status = (
                self.refresh_status()
            )

            axis_status = (
                self._status[
                    axis.lower()
                ]
            )

            mode = (
                axis_status["mode"]
            )

            reached = (
                abs(
                    axis_status[
                        "pos_mm"
                    ]
                    -
                    target_mm
                )
                <=
                POSITION_TOLERANCE_MM
            )

            if (
                status["estop"]
                or
                mode == "FAULT"
            ):

                return {
                    "success": False,
                    "message":
                        f"{axis} 이동 중 FAULT/ESTOP",
                    "status":
                        status,
                }

            if mode != "IDLE":
                seen_busy = True

            if (
                mode == "IDLE"
                and
                reached
            ):

                return {
                    "success": True,
                    "message":
                        f"{axis} 이동 완료",
                    "target_mm":
                        target_mm,
                    "delta_mm":
                        delta,
                    "status":
                        status,
                }

            # 움직였다가 IDLE이 됐는데 목표에 못 갔으면
            # 리밋/정지 등의 비정상 종료로 판단
            if (
                mode == "IDLE"
                and
                seen_busy
                and
                not reached
            ):

                self.last_error = (
                    f"{axis} 목표 미도달: "
                    f"target={target_mm:.4f}, "
                    f"reported="
                    f"{axis_status['pos_mm']:.4f}"
                )

                return {
                    "success": False,
                    "message":
                        self.last_error,
                    "status":
                        status,
                }

            time.sleep(
                STATUS_POLL_SEC
            )

        # 이동 timeout 시에도 HARD STOP
        self._send_expect(
            f"STOP {axis} HARD",
            timeout=1.0,
        )

        self.last_error = (
            f"{axis} MOVE TIMEOUT"
        )

        return {
            "success": False,
            "message":
                self.last_error,
            "status":
                self.refresh_status(),
        }

    def _move_to_position(
        self,
        x_mm: float,
        z_mm: float,
    ):

        # 기존 매핑 GUI와 동일하게 X -> Z
        for axis, target in (
            ("X", x_mm),
            ("Z", z_mm),
        ):

            result = (
                self._move_axis(
                    axis,
                    target,
                )
            )

            if not result[
                "success"
            ]:
                return result

        return {
            "success": True,
            "message":
                "X/Z 목표좌표 이동 완료",
            "status":
                self.refresh_status(),
        }

    def move_to_tray(
        self,
        tray_id: int,
    ):

        # 우리가 앞 단계에서 만든
        # Tray -> Slot -> X/Z 변환
        target = (
            resolve_tray_target(
                tray_id
            )
        )

        if not target.get(
            "success"
        ):

            self.last_error = (
                target.get(
                    "message"
                )
            )

            return {
                **target,
                "status":
                    self.get_status(),
            }

        with self._motion_lock:

            self.paused = False

            self.current_target = (
                target
            )

            self.state = "MOVING"

            result = (
                self._move_to_position(
                    target["x_mm"],
                    target["z_mm"],
                )
            )

            if not result[
                "success"
            ]:

                self.state = "ERROR"

                return {
                    **result,
                    "tray_id":
                        tray_id,
                    "target":
                        target,
                }

            self.current_tray = (
                tray_id
            )

            self.state = "READY"
            self.last_error = None

            return {
                "success": True,

                "message":
                    (
                        f"TRAY "
                        f"{tray_id:02d} "
                        f"-> Slot "
                        f"{target['slot_number']} "
                        "이동 완료"
                    ),

                "tray_id":
                    tray_id,

                "target":
                    target,

                "status":
                    result["status"],
            }

    # ========================================================
    # STOP / PAUSE / ESTOP
    # ========================================================

    def pause(self):

        result = (
            self._send_expect(
                "STOP ALL SOFT",
                timeout=2.0,
            )
        )

        if result[
            "success"
        ]:

            self.paused = True
            self.state = "PAUSED"

        return {
            **result,
            "status":
                self.refresh_status(),
        }

    def resume(self):

        if (
            not self.paused
            or
            not self.current_target
        ):

            return {
                "success": False,
                "message":
                    "재개할 목표가 없습니다.",
                "status":
                    self.get_status(),
            }

        with self._motion_lock:

            self.paused = False
            self.state = "MOVING"

            result = (
                self._move_to_position(
                    self.current_target[
                        "x_mm"
                    ],
                    self.current_target[
                        "z_mm"
                    ],
                )
            )

            if result[
                "success"
            ]:

                self.state = "READY"

            else:

                self.state = "ERROR"

            return result

    def stop(self):

        # UI의 일반 STOP도 현재 프로젝트에서는
        # 안전하게 HARD STOP 사용
        result = (
            self._send_expect(
                "STOP ALL HARD",
                timeout=2.0,
            )
        )

        self.paused = False

        if result[
            "success"
        ]:

            self.state = "STOPPED"

        return {
            **result,
            "status":
                self.refresh_status(),
        }

    def emergency_stop(self):

        result = (
            self._send_expect(
                "ESTOP",
                success="OK ESTOP",
                timeout=2.0,
            )
        )

        self.paused = False

        if result[
            "success"
        ]:

            self.state = "ESTOPPED"

        return {
            **result,
            "status":
                self.refresh_status(),
        }

    def reset_error(self):

        result = (
            self._send_expect(
                "RESET",
                timeout=2.0,
            )
        )

        if result[
            "success"
        ]:

            self.current_tray = None
            self.current_target = None
            self.paused = False

            self.state = "CONNECTED"

        return {
            **result,
            "status":
                self.refresh_status(),
        }

    def reset_for_retry(
        self,
        use_home: bool = True,
    ):

        result = self.stop()

        if not result[
            "success"
        ]:
            return result

        if use_home:
            return self.home()

        return {
            "success": True,
            "message":
                "RETRY_READY",
            "status":
                self.refresh_status(),
        }
