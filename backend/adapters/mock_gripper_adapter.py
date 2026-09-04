from __future__ import annotations

from typing import Any


class MockGripperAdapter:
    """
    MG996R Gripper Mock.

    CLOSE 시 Mock Load Cell에 Tray 하중을 만들고,
    OPEN 시 Tray 하중을 제거한다.
    """

    def __init__(
        self,
        loadcell=None,
        open_angle_deg: int = 30,
        close_angle_deg: int = 150,
    ) -> None:
        self.loadcell = loadcell
        self.open_angle_deg = self._validate_angle(open_angle_deg)
        self.close_angle_deg = self._validate_angle(close_angle_deg)
        self.is_open = True
        self.angle = self.open_angle_deg

    @staticmethod
    def _validate_angle(angle_deg: int) -> int:
        angle = int(angle_deg)

        if not 0 <= angle <= 180:
            raise ValueError(
                "Servo angle은 0~180 범위여야 합니다."
            )

        return angle

    def _set_load(
        self,
        present: bool,
    ) -> None:
        setter = getattr(
            self.loadcell,
            "set_mock_tray_present",
            None,
        )

        if callable(setter):
            setter(present)

    def open(self) -> dict[str, Any]:
        self.is_open = True
        result = self.set_angle(self.open_angle_deg)
        self._set_load(False)
        result.update({
            "state": "OPEN",
            "action": "OPEN",
            "message": "Mock Gripper OPEN",
        })
        return result

    def close(self) -> dict[str, Any]:
        self.is_open = False
        result = self.set_angle(self.close_angle_deg)
        self._set_load(True)
        result.update({
            "state": "CLOSED",
            "action": "CLOSE",
            "message": "Mock Gripper CLOSE",
        })
        return result

    def set_angle(
        self,
        angle: int,
    ) -> dict[str, Any]:
        try:
            self.angle = self._validate_angle(angle)
        except (TypeError, ValueError):
            return {
                "success": False,
                "mock": True,
                "message": "SERVO angle은 0~180 범위여야 합니다.",
            }

        return {
            "success": True,
            "mock": True,
            "angle": self.angle,
            "angle_deg": self.angle,
            "message": f"Mock Servo angle={self.angle}",
        }
