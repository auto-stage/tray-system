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

    def _wait_until_idle(self, timeout: float = 15.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        last_message = ""

        while time.monotonic() < deadline:
            result = self.stage._send_expect(
                "GRIPPER STATUS",
                success="GRIPPER STATUS",
                timeout=1.0,
            )

            if not result.get("success"):
                return {
                    "success": False,
                    "message": result.get("message", "GRIPPER STATUS 확인 실패"),
                    "received": result.get("received", []),
                }

            message = str(result.get("message", "")).strip()
            last_message = message

            if message.startswith("GRIPPER STATUS IDLE"):
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

        return {
            "success": False,
            "message": f"GRIPPER 이동 완료 대기 TIMEOUT: {last_message}",
        }

    def _move(self, action: str) -> dict[str, Any]:
        action = action.strip().upper()

        connection_error = self._ensure_connected()
        if connection_error:
            return connection_error

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

    def extend(self) -> dict[str, Any]:
        return self._move("EXTEND")

    def retract(self) -> dict[str, Any]:
        return self._move("RETRACT")
