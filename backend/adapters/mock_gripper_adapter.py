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
    ) -> None:
        self.loadcell = loadcell
        self.is_open = True
        self.angle = 30

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
        self.angle = 30

        self._set_load(False)

        return {
            "success": True,
            "mock": True,
            "state": "OPEN",
            "angle": self.angle,
            "message": "Mock Gripper OPEN",
        }

    def close(self) -> dict[str, Any]:
        self.is_open = False
        self.angle = 150

        self._set_load(True)

        return {
            "success": True,
            "mock": True,
            "state": "CLOSED",
            "angle": self.angle,
            "message": "Mock Gripper CLOSE",
        }

    def set_angle(
        self,
        angle: int,
    ) -> dict[str, Any]:
        self.angle = int(angle)

        return {
            "success": True,
            "mock": True,
            "angle": self.angle,
            "message": f"Mock Servo angle={self.angle}",
        }
