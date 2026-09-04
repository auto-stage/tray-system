from __future__ import annotations

from typing import Any


class STM32GripperAdapter:
    """
    MG996R gripper servo adapter.

    별도 Serial 포트를 열지 않고 STM32StageAdapter의
    기존 Serial 연결과 I/O lock을 그대로 재사용한다.

    OPEN/CLOSE 의미와 각도값은 상위 Backend가 관리하고,
    STM32에는 항상 ``SERVO <angle_deg>`` 명령만 전달한다.
    """

    def __init__(
        self,
        stage,
        open_angle_deg: int = 30,
        close_angle_deg: int = 150,
    ) -> None:
        self.stage = stage
        self.open_angle_deg = self._validate_angle(open_angle_deg)
        self.close_angle_deg = self._validate_angle(close_angle_deg)

    @staticmethod
    def _validate_angle(angle_deg: int) -> int:
        angle = int(angle_deg)

        if not 0 <= angle <= 180:
            raise ValueError(
                "Servo angle은 0~180 범위여야 합니다."
            )

        return angle

    def _ensure_connected(self) -> dict[str, Any] | None:
        if (
            self.stage._serial
            and self.stage._serial.is_open
        ):
            return None

        try:
            self.stage.connect()
        except Exception as exc:
            return {
                "success": False,
                "message": f"STM32 연결 실패: {exc}",
            }

        return None

    def set_angle(
        self,
        angle_deg: int,
    ) -> dict[str, Any]:
        """상위에서 지정한 절대 각도를 STM32에 전달한다."""

        try:
            angle = self._validate_angle(angle_deg)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "SERVO angle은 0~180 범위여야 합니다.",
            }

        connection_error = self._ensure_connected()
        if connection_error is not None:
            return connection_error

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
                "received": result.get(
                    "received",
                    [],
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

    def _set_named_angle(
        self,
        action: str,
        angle_deg: int,
    ) -> dict[str, Any]:
        result = self.set_angle(angle_deg)
        result["action"] = action
        return result

    def open(self) -> dict[str, Any]:
        return self._set_named_angle(
            "OPEN",
            self.open_angle_deg,
        )

    def close(self) -> dict[str, Any]:
        return self._set_named_angle(
            "CLOSE",
            self.close_angle_deg,
        )
