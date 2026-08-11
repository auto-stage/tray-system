from __future__ import annotations

from .interfaces import GripperInterface


class SerialGripper(GripperInterface):
    """Future real gripper adapter. Replace commands after STM/gripper protocol is fixed."""
    def __init__(self, command_sender):
        self.command_sender = command_sender

    def open(self) -> None:
        self.command_sender("GRIP_OPEN")

    def close(self) -> None:
        self.command_sender("GRIP_CLOSE")
