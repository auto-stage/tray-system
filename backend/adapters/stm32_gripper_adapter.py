from __future__ import annotations

from typing import Any


class STM32GripperAdapter:
    """
    MG996R gripper servo adapter.

    별도 Serial 포트를 열지 않고 STM32StageAdapter의
    기존 Serial 연결과 I/O lock을 그대로 재사용한다.
    """

    def __init__(self, stage) -> None:
        self.stage = stage

    def _send_grip_command(
        self,
        action: str,
    ) -> dict[str, Any]:

        action = action.strip().upper()

        if action not in {
            "OPEN",
            "CLOSE",
        }:
            return {
                "success": False,
                "message": f"지원하지 않는 GRIP action: {action}",
            }

        # STM32StageAdapter가 아직 연결되지 않았다면 연결
        if (
            not self.stage._serial
            or
            not self.stage._serial.is_open
        ):
            try:
                self.stage.connect()
            except Exception as exc:
                return {
                    "success": False,
                    "message": f"STM32 연결 실패: {exc}",
                }

        result = self.stage._send_expect(
            f"GRIP {action}",
            success=f"OK GRIP {action}",
            timeout=2.0,
        )

        if not result.get("success"):
            return {
                "success": False,
                "action": action,
                "message": result.get(
                    "message",
                    f"GRIP {action} 실패",
                ),
                "received": result.get(
                    "received",
                    [],
                ),
            }

        # STM32 응답 예:
        # OK GRIP OPEN 30
        # OK GRIP CLOSE 150
        tokens = (
            result.get(
                "message",
                "",
            ).split()
        )

        angle_deg = None

        if len(tokens) >= 4:
            try:
                angle_deg = int(
                    tokens[3]
                )
            except ValueError:
                pass

        return {
            "success": True,
            "action": action,
            "angle_deg": angle_deg,
            "message": result.get(
                "message",
                "",
            ),
        }

    def open(self) -> dict[str, Any]:
        return self._send_grip_command(
            "OPEN"
        )

    def close(self) -> dict[str, Any]:
        return self._send_grip_command(
            "CLOSE"
        )

    def set_angle(
        self,
        angle_deg: int,
    ) -> dict[str, Any]:
        """
        실제 그리퍼 장착 후 OPEN/CLOSE 각도 튜닝용.
        정상 Material Flow에서는 open()/close() 사용을 권장한다.
        """

        angle = int(angle_deg)

        if not 0 <= angle <= 180:
            return {
                "success": False,
                "message": "SERVO angle은 0~180 범위여야 합니다.",
            }

        if (
            not self.stage._serial
            or
            not self.stage._serial.is_open
        ):
            try:
                self.stage.connect()
            except Exception as exc:
                return {
                    "success": False,
                    "message": f"STM32 연결 실패: {exc}",
                }

        result = self.stage._send_expect(
            f"SERVO {angle}",
            success=f"OK SERVO {angle}",
            timeout=2.0,
        )

        if not result.get("success"):
            return {
                "success": False,
                "angle_deg": angle,
                "message": result.get(
                    "message",
                    "SERVO 명령 실패",
                ),
            }

        return {
            "success": True,
            "angle_deg": angle,
            "message": result.get(
                "message",
                "",
            ),
        }
