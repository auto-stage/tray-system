from __future__ import annotations

from .interfaces import StageInterface


class SerialStage(StageInterface):
    """Actual stage adapter skeleton.

    IMPORTANT: Command strings below are placeholders until STM firmware protocol is finalized.
    Keep the upper-level interface and modify only this adapter when integrating hardware.
    """
    def __init__(self, port: str, baudrate: int = 115200, timeout_s: float = 1.0):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial 설치 필요: pip install pyserial") from exc
        self.ser = serial.Serial(port, baudrate, timeout=timeout_s, write_timeout=timeout_s)

    def _request(self, command: str, expected_prefix: str = "OK") -> str:
        self.ser.write((command.strip() + "\n").encode("ascii"))
        self.ser.flush()
        line = self.ser.readline().decode("ascii", errors="replace").strip()
        if not line:
            raise TimeoutError(f"응답 없음: {command}")
        if expected_prefix and not line.startswith(expected_prefix):
            raise RuntimeError(f"예상하지 못한 응답: {line}")
        return line

    def home(self) -> None:
        self._request("HOME")

    def move_absolute(self, axis1_mm: float, axis2_mm: float) -> None:
        self._request(f"MOVE_ABS {axis1_mm:.3f} {axis2_mm:.3f}")

    def move_relative(self, d_axis1_mm: float, d_axis2_mm: float) -> None:
        self._request(f"MOVE_REL {d_axis1_mm:.3f} {d_axis2_mm:.3f}")

    def get_position(self) -> tuple[float, float]:
        parts = self._request("POS?", "POS").split()
        if len(parts) != 3:
            raise RuntimeError("POS 응답 형식 오류")
        return float(parts[1]), float(parts[2])

    def stop(self) -> None:
        self._request("STOP")

    def close(self) -> None:
        self.ser.close()
