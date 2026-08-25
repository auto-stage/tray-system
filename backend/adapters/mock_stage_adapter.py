from .stage_adapter import StageAdapter
from .slot_resolver import resolve_tray_target


class MockStageAdapter(StageAdapter):

    def __init__(self):
        self.current_tray = None
        self.homed = False
        self.moving = False
        self.estopped = False

        self.x = 0.0
        self.z = 0.0

        self.current_target = None
        self.last_error = None

    def home(self):
        """
        실제 장비에서는 X/Z HOME을 수행하지만,
        Mock에서는 두 축 모두 HOME 완료 상태로 처리한다.
        """

        if self.estopped:
            return {
                "success": False,
                "error": "ESTOP_ACTIVE",
                "message": "E-STOP 상태에서는 HOME할 수 없습니다.",
                "status": self.get_status(),
            }

        self.homed = True
        self.current_tray = None

        self.x = 0.0
        self.z = 0.0

        self.current_target = None
        self.last_error = None

        print("[MOCK STAGE] X/Z HOME 완료")

        return {
            "success": True,
            "message": "X/Z HOME 완료",
            "status": self.get_status(),
        }

    def move_to_tray(self, tray_id: int):
        """
        Tray ID
        -> rack_layout
        -> 물리 Slot
        -> slot_map
        -> X/Z 목표좌표

        까지 실제 Backend 로직을 사용한다.

        모터 구동만 Mock으로 처리한다.
        """

        if self.estopped:
            return {
                "success": False,
                "error": "ESTOP_ACTIVE",
                "message": "E-STOP 상태에서는 이동할 수 없습니다.",
                "status": self.get_status(),
            }

        if not self.homed:
            return {
                "success": False,
                "error": "NOT_HOMED",
                "message": "Stage HOME을 먼저 수행해야 합니다.",
                "status": self.get_status(),
            }

        target = resolve_tray_target(
            tray_id
        )

        if not target.get("success"):
            self.last_error = target

            print(
                "[MOCK STAGE] 이동 차단:",
                target.get("message")
            )

            return {
                **target,
                "status": self.get_status(),
            }

        self.moving = True
        self.current_target = target

        print(
            "[MOCK STAGE]",
            f"TRAY {tray_id:02d}",
            f"-> Slot {target['slot_number']}",
            f"({target['slot_name']})",
        )

        print(
            "[MOCK STAGE]",
            f"목표 X={target['x_mm']:.4f} mm",
            f"Z={target['z_mm']:.4f} mm",
        )

        # 실제 모터 대신 즉시 목표 위치에 도착한 것으로 처리
        self.x = target["x_mm"]
        self.z = target["z_mm"]

        self.current_tray = tray_id
        self.moving = False
        self.last_error = None

        return {
            "success": True,
            "tray_id": tray_id,
            "target": target,
            "message": (
                f"TRAY {tray_id:02d} "
                f"-> Slot {target['slot_number']} "
                "이동 완료 (MOCK)"
            ),
            "status": self.get_status(),
        }

    def pause(self):
        self.moving = False

        return {
            "success": True,
            "message": "일시정지 (MOCK)",
            "status": self.get_status(),
        }

    def resume(self):
        return {
            "success": True,
            "message": "작업 재개 (MOCK)",
            "status": self.get_status(),
        }

    def stop(self):
        self.moving = False

        return {
            "success": True,
            "message": "Stage 정지 (MOCK)",
            "status": self.get_status(),
        }

    def emergency_stop(self):
        self.estopped = True
        self.moving = False

        return {
            "success": True,
            "message": "E-STOP (MOCK)",
            "status": self.get_status(),
        }

    def reset_error(self):
        """
        실제 STM32 RESET과 동일한 의미의 Mock 복구.
        E-STOP / 오류는 해제하지만 HOME 기준은 무효화한다.
        """
        self.estopped = False
        self.moving = False
        self.homed = False

        self.current_tray = None
        self.current_target = None
        self.last_error = None

        return {
            "success": True,
            "message": "Stage RESET 완료 (MOCK) - HOME 필요",
            "status": self.get_status(),
        }

    def get_status(self):
        return {
            "connected": False,
            "mock": True,

            "homed": self.homed,
            "moving": self.moving,
            "estopped": self.estopped,

            "current_tray":
                self.current_tray,

            "position": {
                "x": self.x,
                "z": self.z,
            },

            "current_target":
                self.current_target,

            "last_error":
                self.last_error,
        }
