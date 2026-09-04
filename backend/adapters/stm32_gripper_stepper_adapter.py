from __future__ import annotations

import time
from typing import Any


class STM32GripperStepperAdapter:
    """
    Gripper 전후진 Stepper Adapter.

    별도 Serial 포트를 열지 않고 기존 STM32StageAdapter의
    Serial 연결, _io_lock, _send_expect()를 그대로 재사용한다.

    extend()/retract()는 STM32가 명령을 수락한 시점이 아니라
    실제 이동이 끝나 GRIPPER STATUS가 IDLE이 된 뒤 성공을 반환한다.
    """

    def __init__(self, stage) -> None:
        self.stage = stage

    def _ensure_connected(self) -> dict[str, Any] | None:
        if self.stage._serial and self.stage._serial.is_open:
            return None

        try:
            self.stage.connect()
        except Exception as exc:
            return {
                "success": False,
                "message": f"STM32 연결 실패: {exc}",
            }

        return None

    def stop(self) -> dict[str, Any]:
        """
        Gripper 전후진 Stepper만 HARD STOP한다.
        전체 X/Z/G 정지는 stage.stop() 또는 emergency_stop() 사용.
        """
        connection_error = self._ensure_connected()
        if connection_error:
            return connection_error

        result = self.stage._send_expect(
            "GRIPPER STOP",
            success="OK GRIPPER STOP",
            timeout=2.0,
        )

        if not result.get("success"):
            return {
                "success": False,
                "message": result.get(
                    "message",
                    "GRIPPER STOP 실패",
                ),
                "received": result.get(
                    "received",
                    [],
                ),
            }

        return {
            "success": True,
            "message": result.get(
                "message",
                "OK GRIPPER STOP",
            ),
        }

    def _wait_until_idle(self, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_message = ""

        while time.monotonic() < deadline:
            if self.stage._stop_event.is_set():
                return {
                    "success": False,
                    "cancelled": True,
                    "message": "GRIPPER 이동이 STOP으로 중단되었습니다.",
                }

            result = self.stage._send_expect(
                "GRIPPER STATUS",
                success="GRIPPER STATUS",
                timeout=1.0,
            )

            if not result.get("success"):
                message = str(
                    result.get(
                        "message",
                        "GRIPPER STATUS 확인 실패",
                    )
                )

                # 이동 중 일시적으로 STATUS 응답이 늦어질 수 있다.
                # 단일 1초 timeout으로 전체 이동을 실패시키지 않고,
                # _wait_until_idle()의 전체 timeout 동안 재시도한다.
                if message.startswith("TIMEOUT: GRIPPER STATUS"):
                    last_message = message
                    time.sleep(0.05)
                    continue

                # 명시적인 STM32 ERR 또는 Serial 오류는 즉시 실패 처리.
                return {
                    "success": False,
                    "message": message,
                    "received": result.get("received", []),
                }

            message = str(result.get("message", "")).strip()
            last_message = message

            if message.startswith("GRIPPER STATUS IDLE"):
                if self.stage._stop_event.is_set():
                    return {
                        "success": False,
                        "cancelled": True,
                        "message": "GRIPPER 이동이 STOP으로 중단되었습니다.",
                    }

                return {
                    "success": True,
                    "message": message,
                }

            if message.startswith("GRIPPER STATUS FAULT"):
                return {
                    "success": False,
                    "message": message,
                }

            time.sleep(0.05)

        stop_result = self.stop()

        return {
            "success": False,
            "timeout": True,
            "message": f"GRIPPER 이동 완료 대기 TIMEOUT: {last_message}",
            "stop_result": stop_result,
        }

    def home(self) -> dict[str, Any]:
        """
        Gripper rack 원점을 잡는다.

        STM32 HOME sequence:
        RETRACT limit -> release -> slow re-touch -> final 3 mm release -> 0 mm.
        """
        if self.stage._stop_event.is_set():
            return {
                "success": False,
                "cancelled": True,
                "action": "HOME",
                "message": "STOP 상태이므로 Gripper HOME을 시작하지 않습니다.",
            }

        connection_error = self._ensure_connected()
        if connection_error:
            return connection_error

        result = self.stage._send_expect(
            "GRIPPER HOME",
            success="OK GRIPPER HOME",
            timeout=2.0,
        )

        if not result.get("success"):
            return {
                "success": False,
                "action": "HOME",
                "message": result.get("message", "GRIPPER HOME 실패"),
                "received": result.get("received", []),
            }

        wait_result = self._wait_until_idle(timeout=30.0)
        if not wait_result.get("success"):
            return {
                "success": False,
                "action": "HOME",
                "message": wait_result.get("message", "GRIPPER HOME 완료 대기 실패"),
                "received": wait_result.get("received", []),
            }

        status_message = str(wait_result.get("message", ""))
        if " HOMED 1 " not in f" {status_message} ":
            return {
                "success": False,
                "action": "HOME",
                "message": f"GRIPPER HOME 후 HOMED 확인 실패: {status_message}",
            }

        return {
            "success": True,
            "action": "HOME",
            "message": status_message,
        }

    def _move(self, action: str) -> dict[str, Any]:
        action = action.strip().upper()

        if self.stage._stop_event.is_set():
            return {
                "success": False,
                "cancelled": True,
                "action": action,
                "message": "STOP 상태이므로 Gripper 이동을 시작하지 않습니다.",
            }

        connection_error = self._ensure_connected()
        if connection_error:
            return connection_error

        result = self.stage._send_expect(
            f"GRIPPER {action}",
            success=f"OK GRIPPER {action}",
            timeout=2.0,
        )

        if not result.get("success"):
            message = str(result.get("message", ""))

            # 전원 재인가/위치 유실 후 첫 동작은 자동 HOME 후 한 번만 재시도한다.
            if "ERR GRIPPER NOT_HOMED" in message:
                home_result = self.home()
                if not home_result.get("success"):
                    return {
                        "success": False,
                        "action": action,
                        "message": home_result.get("message", "Gripper 자동 HOME 실패"),
                        "received": home_result.get("received", []),
                    }

                result = self.stage._send_expect(
                    f"GRIPPER {action}",
                    success=f"OK GRIPPER {action}",
                    timeout=2.0,
                )

            if not result.get("success"):
                return {
                    "success": False,
                    "action": action,
                    "message": result.get("message", f"Gripper {action} 실패"),
                    "received": result.get("received", []),
                }

        wait_result = self._wait_until_idle(timeout=15.0)
        if not wait_result.get("success"):
            return {
                "success": False,
                "action": action,
                "message": wait_result.get("message", f"Gripper {action} 완료 대기 실패"),
                "received": wait_result.get("received", []),
            }

        return {
            "success": True,
            "action": action,
            "message": wait_result.get("message", f"GRIPPER {action} 완료"),
        }

    def move_to_mm(
        self,
        target_mm: float,
        *,
        speed_mm_s: float = 75.36,
        accel_mm_s2: float = 200.0,
    ) -> dict[str, Any]:
        """
        기존 STM32 generic MOVE G 프로토콜을 이용해 G축을 임의 절대 위치로 이동한다.

        STM firmware가 generic MOVE G를 허용해야 하며, 현재 G 위치는
        GRIPPER STATUS의 POS_MM에서 읽고 MOVE G에는 상대거리만 전달한다.
        """
        if self.stage._stop_event.is_set():
            return {
                "success": False,
                "cancelled": True,
                "action": "MOVE_TO_MM",
                "message": "STOP 상태이므로 Gripper 이동을 시작하지 않습니다.",
            }

        connection_error = self._ensure_connected()
        if connection_error:
            return connection_error

        status = self.stage._send_expect(
            "GRIPPER STATUS",
            success="GRIPPER STATUS",
            timeout=1.0,
        )
        if not status.get("success"):
            return status

        message = str(status.get("message", ""))
        import re
        match = re.search(r"\bPOS_MM\s+(-?\d+(?:\.\d+)?)", message)
        if match is None:
            return {
                "success": False,
                "action": "MOVE_TO_MM",
                "message": f"GRIPPER STATUS에서 POS_MM 파싱 실패: {message}",
            }

        if " HOMED 1 " not in f" {message} ":
            home_result = self.home()
            if not home_result.get("success"):
                return {
                    "success": False,
                    "action": "MOVE_TO_MM",
                    "message": home_result.get("message", "Gripper HOME 실패"),
                }
            status = self.stage._send_expect(
                "GRIPPER STATUS",
                success="GRIPPER STATUS",
                timeout=1.0,
            )
            message = str(status.get("message", ""))
            match = re.search(r"\bPOS_MM\s+(-?\d+(?:\.\d+)?)", message)
            if not status.get("success") or match is None:
                return {
                    "success": False,
                    "action": "MOVE_TO_MM",
                    "message": "HOME 후 GRIPPER STATUS 확인 실패",
                }

        current_mm = float(match.group(1))
        target_mm = float(target_mm)
        delta_mm = target_mm - current_mm

        if abs(delta_mm) < 0.05:
            return {
                "success": True,
                "action": "MOVE_TO_MM",
                "target_mm": target_mm,
                "message": f"GRIPPER 이미 {target_mm:.1f} mm 위치",
            }

        result = self.stage._send_expect(
            f"MOVE G {delta_mm:.6g} {float(speed_mm_s):.6g} {float(accel_mm_s2):.6g}",
            success="OK OK",
            timeout=2.0,
        )
        if not result.get("success"):
            return {
                "success": False,
                "action": "MOVE_TO_MM",
                "message": result.get("message", "GRIPPER MOVE G 실패"),
                "received": result.get("received", []),
            }

        wait_result = self._wait_until_idle(timeout=15.0)
        if not wait_result.get("success"):
            return {
                "success": False,
                "action": "MOVE_TO_MM",
                "message": wait_result.get("message", "GRIPPER MOVE G 완료 대기 실패"),
                "received": wait_result.get("received", []),
            }

        return {
            "success": True,
            "action": "MOVE_TO_MM",
            "target_mm": target_mm,
            "message": wait_result.get("message", f"GRIPPER {target_mm:.1f} mm 이동 완료"),
        }

    def extend(self) -> dict[str, Any]:
        return self._move("EXTEND")

    def retract(self) -> dict[str, Any]:
        return self._move("RETRACT")
