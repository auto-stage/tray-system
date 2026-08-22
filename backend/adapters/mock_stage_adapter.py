from .stage_adapter import StageAdapter


class MockStageAdapter(StageAdapter):

    def __init__(self):
        self.current_tray = None
        self.homed = False
        self.moving = False
        self.estopped = False


    def home(self):

        self.homed = True
        self.current_tray = None

        print("[MOCK STAGE] HOME 완료")

        return {
            "success": True,
            "message": "HOME 완료"
        }


    def move_to_tray(self, tray_id: int):

        self.moving = True

        print(
            f"[MOCK STAGE] TRAY {tray_id} 이동 요청"
        )

        self.current_tray = tray_id
        self.moving = False

        return {
            "success": True,
            "tray_id": tray_id,
            "message": f"TRAY {tray_id} 이동 완료"
        }


    def pause(self):

        self.moving = False

        return {
            "success": True,
            "message": "일시정지"
        }


    def resume(self):

        return {
            "success": True,
            "message": "작업 재개"
        }


    def stop(self):

        self.moving = False

        return {
            "success": True,
            "message": "Stage 정지"
        }


    def emergency_stop(self):

        self.estopped = True
        self.moving = False

        return {
            "success": True,
            "message": "E-STOP"
        }


    def get_status(self):

        return {
            "connected": False,
            "mock": True,
            "homed": self.homed,
            "moving": self.moving,
            "estopped": self.estopped,
            "current_tray": self.current_tray
        }