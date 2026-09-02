from __future__ import annotations

from typing import Any


class MockGripperStepperAdapter:
    """Gripper 전후진 Stepper Mock."""

    def __init__(self) -> None:
        self.position = "RETRACTED"
        self.stopped = False

    def extend(self) -> dict[str, Any]:
        self.position = "EXTENDED"
        self.stopped = False

        return {
            "success": True,
            "mock": True,
            "position": self.position,
            "message": "Mock Gripper Stepper EXTEND",
        }

    def retract(self) -> dict[str, Any]:
        self.position = "RETRACTED"
        self.stopped = False

        return {
            "success": True,
            "mock": True,
            "position": self.position,
            "message": "Mock Gripper Stepper RETRACT",
        }

    def stop(self) -> dict[str, Any]:
        self.stopped = True

        return {
            "success": True,
            "mock": True,
            "position": self.position,
            "message": "Mock Gripper Stepper STOP",
        }
