class StageAdapter:
    """
    React UI / server.py와 실제 STM32 Stage 코드 사이의 연결 규격.

    현재는 실제 STM32 통신을 구현하지 않는다.
    나중에 레일 파트 코드가 확정되면 이 인터페이스에 맞춰 연결한다.
    """

    def home(self):
        raise NotImplementedError

    def move_to_tray(self, tray_id: int):
        raise NotImplementedError

    def move_relative(self, x_delta_mm: float, z_delta_mm: float):
        """Vision correction: move X/Z by relative millimeter deltas."""
        raise NotImplementedError

    def pause(self):
        raise NotImplementedError

    def resume(self):
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError

    def emergency_stop(self):
        raise NotImplementedError

    def get_status(self):
        raise NotImplementedError