"""
STM32 Stage Adapter
===================

역할
----
React UI / FastAPI / WorkflowController 쪽 코드는 그대로 두고,
이 파일만 실제 STM32의 시리얼 프로토콜에 맞추면 됩니다.

권장 흐름:
React UI
  -> FastAPI server.py
  -> WorkflowController
  -> STM32StageAdapter
  -> USB Serial
  -> STM32

중요:
- 리밋 스위치 감시, 모터 정지, 원점 복귀의 실제 저수준 안전 동작은 STM32가 담당합니다.
- Python은 HOME / MOVE / STOP / ESTOP 등의 "고수준 명령"을 보냅니다.
- STM32는 DONE / ERROR / POSITION / LIMIT 등의 상태를 반환합니다.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import serial


class STM32StageAdapter:
    """
    PC <-> STM32 시리얼 연결 어댑터.

    기본 프로토콜 예:
        PC -> STM32
            HOME
            MOVE:5
            PAUSE
            RESUME
            STOP
            ESTOP
            STATUS
            RESET_ERROR

        STM32 -> PC
            READY
            ACK
            MOVING:5
            DONE:5
            HOME_DONE
            PAUSED
            RESUMED
            STOPPED
            ESTOPPED
            POSITION:X=358,Z=126
            LIMIT:X_MIN=0,X_MAX=0,Z_MIN=1,Z_MAX=0
            ERROR:LIMIT
            ERROR:MOTOR
            ERROR:POSITION
    """

    def __init__(
        self,
        port: str = "COM3",
        baudrate: int = 115200,
        timeout: float = 0.2,
        command_timeout: float = 15.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.command_timeout = command_timeout

        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()

        self.state = "DISCONNECTED"
        self.current_tray: Optional[int] = None
        self.x: Optional[float] = None
        self.z: Optional[float] = None
        self.last_error: Optional[str] = None

        self.limits = {
            "X_MIN": False,
            "X_MAX": False,
            "Z_MIN": False,
            "Z_MAX": False,
        }

        self.connect()

    # ========================================================
    # 연결
    # ========================================================

    def connect(self):
        if self._serial and self._serial.is_open:
            return

        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
            write_timeout=1.0,
        )

        # 보드 리셋/USB 연결 안정화 여유
        time.sleep(1.5)

        self.state = "CONNECTED"

    def close(self):
        if self._serial and self._serial.is_open:
            self._serial.close()

        self.state = "DISCONNECTED"

    # ========================================================
    # 내부 통신 함수
    # ========================================================

    def _send_line(self, command: str):
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("STM32 Serial이 연결되어 있지 않습니다.")

        payload = (command.strip() + "\n").encode("utf-8")
        self._serial.write(payload)
        self._serial.flush()

    def _read_line(self) -> Optional[str]:
        if not self._serial or not self._serial.is_open:
            return None

        raw = self._serial.readline()

        if not raw:
            return None

        return raw.decode(
            "utf-8",
            errors="replace",
        ).strip()

    def _update_status_from_line(self, line: str):
        """
        STM32가 보내는 상태 문자열을 Python 내부 상태로 반영.
        """
        if not line:
            return

        if line == "READY":
            self.state = "READY"
            self.last_error = None
            return

        if line.startswith("MOVING:"):
            self.state = "MOVING"

            try:
                self.current_tray = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

            return

        if line.startswith("DONE:"):
            self.state = "READY"

            try:
                self.current_tray = int(
                    line.split(":", 1)[1]
                )
            except ValueError:
                pass

            return

        if line == "HOME_DONE":
            self.state = "READY"
            self.current_tray = None
            return

        if line == "PAUSED":
            self.state = "PAUSED"
            return

        if line == "RESUMED":
            self.state = "MOVING"
            return

        if line == "STOPPED":
            self.state = "STOPPED"
            return

        if line == "ESTOPPED":
            self.state = "ESTOPPED"
            return

        if line.startswith("ERROR:"):
            self.state = "ERROR"
            self.last_error = line.split(
                ":",
                1,
            )[1]
            return

        if line.startswith("POSITION:"):
            # 예: POSITION:X=358,Z=126
            try:
                payload = line.split(":", 1)[1]

                values = {}

                for token in payload.split(","):
                    key, value = token.split(
                        "=",
                        1,
                    )
                    values[key.strip()] = float(
                        value.strip()
                    )

                if "X" in values:
                    self.x = values["X"]

                if "Z" in values:
                    self.z = values["Z"]

            except (
                ValueError,
                IndexError,
            ):
                pass

            return

        if line.startswith("LIMIT:"):
            # 예:
            # LIMIT:X_MIN=0,X_MAX=0,Z_MIN=1,Z_MAX=0
            try:
                payload = line.split(
                    ":",
                    1,
                )[1]

                for token in payload.split(","):
                    key, value = token.split(
                        "=",
                        1,
                    )

                    key = key.strip()

                    if key in self.limits:
                        self.limits[key] = (
                            value.strip() == "1"
                        )

            except (
                ValueError,
                IndexError,
            ):
                pass

    def _wait_for(
        self,
        success_prefixes: tuple[str, ...],
        timeout: Optional[float] = None,
    ):
        """
        명령 전송 후 STM32의 완료/오류 응답을 기다린다.

        ERROR:* 가 오면 즉시 실패.
        success_prefixes 중 하나가 오면 성공.
        """
        deadline = time.monotonic() + (
            timeout
            if timeout is not None
            else self.command_timeout
        )

        received = []

        while time.monotonic() < deadline:
            line = self._read_line()

            if not line:
                continue

            received.append(line)
            self._update_status_from_line(line)

            if line.startswith("ERROR:"):
                return {
                    "success": False,
                    "message": line,
                    "received": received,
                    "status": self.get_status(),
                }

            if any(
                line.startswith(prefix)
                for prefix
                in success_prefixes
            ):
                return {
                    "success": True,
                    "message": line,
                    "received": received,
                    "status": self.get_status(),
                }

        self.state = "ERROR"
        self.last_error = "TIMEOUT"

        return {
            "success": False,
            "message": "ERROR:TIMEOUT",
            "received": received,
            "status": self.get_status(),
        }

    def _command(
        self,
        command: str,
        success_prefixes: tuple[str, ...],
        timeout: Optional[float] = None,
    ):
        with self._lock:
            try:
                self._send_line(command)

                return self._wait_for(
                    success_prefixes,
                    timeout=timeout,
                )

            except Exception as error:
                self.state = "ERROR"
                self.last_error = str(error)

                return {
                    "success": False,
                    "message": str(error),
                    "status": self.get_status(),
                }

    # ========================================================
    # StageAdapter 역할
    # ========================================================

    def home(self):
        """
        Python은 HOME만 요청.
        실제 원점 탐색 / 리밋 스위치 처리 / 모터 정지는 STM32가 담당.
        """
        self.state = "HOMING"

        return self._command(
            "HOME",
            ("HOME_DONE",),
            timeout=30.0,
        )

    def move_to_tray(self, tray_id: int):
        """
        특정 Tray로 이동.

        Python은 Tray ID만 전달하고,
        Tray 좌표 / 가감속 / 리밋 감시는 STM32에서 처리하는 구조를 권장.
        """
        if tray_id not in range(1, 7):
            return {
                "success": False,
                "message": (
                    f"잘못된 Tray ID: {tray_id}"
                ),
                "status": self.get_status(),
            }

        self.state = "MOVING"

        return self._command(
            f"MOVE:{tray_id}",
            (f"DONE:{tray_id}",),
        )

    def pause(self):
        return self._command(
            "PAUSE",
            ("PAUSED",),
            timeout=3.0,
        )

    def resume(self):
        return self._command(
            "RESUME",
            ("RESUMED", "MOVING:"),
            timeout=3.0,
        )

    def stop(self):
        """
        정상 정지.
        현재 위치를 유지할지, 감속 정지할지는 STM32 구현에서 결정.
        """
        return self._command(
            "STOP",
            ("STOPPED",),
            timeout=3.0,
        )

    def emergency_stop(self):
        """
        비상 정지.
        실제 비상정지는 하드웨어 차원의 안전회로/STM32 처리가 우선.
        Python 명령은 보조 제어 채널로 봐야 한다.
        """
        return self._command(
            "ESTOP",
            ("ESTOPPED",),
            timeout=2.0,
        )

    def reset_error(self):
        """
        ERROR 상태 해제 요청.
        STM32가 안전 조건을 확인한 뒤 READY를 반환해야 한다.
        """
        return self._command(
            "RESET_ERROR",
            ("READY",),
            timeout=5.0,
        )

    def reset_for_retry(
        self,
        use_home: bool = True,
    ):
        """
        '현재 단계 다시 시작'용 복구 함수.

        기본 권장:
            STOP
            -> HOME
            -> READY

        실제 장비에서 HOME이 위험하거나 불필요한 상황이 있다면
        STM 담당자와 협의해 use_home=False 또는 별도 복구 명령으로 변경.

        중요:
        오류 상태에서 자동 HOME을 무조건 실행하면 안 되는 장비라면,
        이 함수 내부 정책을 반드시 실제 기구 안전 요구사항에 맞게 변경.
        """
        stop_result = self.stop()

        if not stop_result.get("success"):
            return {
                "success": False,
                "step": "STOP",
                "result": stop_result,
            }

        if use_home:
            home_result = self.home()

            if not home_result.get("success"):
                return {
                    "success": False,
                    "step": "HOME",
                    "result": home_result,
                }

        return {
            "success": True,
            "message": "RETRY_READY",
            "status": self.get_status(),
        }

    def get_status(self):
        """
        현재 Python이 알고 있는 Stage 상태.

        필요하면 STATUS 명령을 보내 최신 정보를 받아오도록
        별도 refresh_status()를 호출할 수 있다.
        """
        return {
            "connected": bool(
                self._serial
                and self._serial.is_open
            ),
            "state": self.state,
            "current_tray": self.current_tray,
            "position": {
                "x": self.x,
                "z": self.z,
            },
            "limits": dict(self.limits),
            "last_error": self.last_error,
        }

    def refresh_status(self):
        """
        STM32에 현재 상태를 요청.

        STM32는 STATUS 요청 뒤 최소한 아래 중 필요한 값을 보내는 것을 권장:
            READY / MOVING:n / PAUSED / STOPPED / ERROR:...
            POSITION:X=...,Z=...
            LIMIT:X_MIN=...,X_MAX=...,Z_MIN=...,Z_MAX=...
            STATUS_DONE
        """
        with self._lock:
            try:
                self._send_line("STATUS")

                return self._wait_for(
                    ("STATUS_DONE",),
                    timeout=2.0,
                )

            except Exception as error:
                self.state = "ERROR"
                self.last_error = str(error)

                return {
                    "success": False,
                    "message": str(error),
                    "status": self.get_status(),
                }