from __future__ import annotations

import time
from typing import Any, Callable


class MaterialFlowExecutor:
    """
    Tray 공급 / 반납의 실제 장치 실행 순서를 담당한다.

    역할 분리:
    - MaterialFlowController : Queue / 상태 전이
    - MaterialFlowExecutor   : 실제 Adapter 호출 순서
    - Adapter                : 개별 장치 명령
    - STM32                  : 실제 하드웨어 구동

    현재 Gripper 전후진 Stepper는 팀원 구현 전이므로
    stepper=None이면 EXTEND / RETRACT를 BYPASS한다.
    """

    def __init__(
        self,
        *,
        material_flow,
        stage,
        gripper,
        loadcell,
        gripper_stepper=None,
        alignment_callback: Callable[[int], dict[str, Any]] | None = None,
        tray_present_threshold_g: float = 100.0,
        loadcell_samples: int = 5,
        grip_settle_sec: float = 0.5,
        stepper_settle_sec: float = 0.3,
    ) -> None:

        self.material_flow = material_flow
        self.stage = stage
        self.gripper = gripper
        self.loadcell = loadcell
        self.gripper_stepper = gripper_stepper

        self.alignment_callback = alignment_callback

        # TEMP:
        # 실제 Tray + 캐리지 조립 후 실측하여 변경한다.
        self.tray_present_threshold_g = float(
            tray_present_threshold_g
        )

        self.loadcell_samples = max(
            int(loadcell_samples),
            1,
        )

        self.grip_settle_sec = max(
            float(grip_settle_sec),
            0.0,
        )

        self.stepper_settle_sec = max(
            float(stepper_settle_sec),
            0.0,
        )

    # ========================================================
    # 공통 Helper
    # ========================================================

    @staticmethod
    def _failed(
        step: str,
        result: Any,
    ) -> dict[str, Any]:

        if isinstance(result, dict):
            message = result.get(
                "message",
                f"{step} 실패",
            )
        else:
            message = str(result)

        return {
            "success": False,
            "step": step,
            "message": message,
            "result": result,
        }

    def _extend(self) -> dict[str, Any]:

        if self.gripper_stepper is None:
            return {
                "success": True,
                "bypass": True,
                "message": "Gripper EXTEND BYPASS",
            }

        result = self.gripper_stepper.extend()

        if result.get("success"):
            time.sleep(
                self.stepper_settle_sec
            )

        return result

    def _retract(self) -> dict[str, Any]:

        if self.gripper_stepper is None:
            return {
                "success": True,
                "bypass": True,
                "message": "Gripper RETRACT BYPASS",
            }

        result = self.gripper_stepper.retract()

        if result.get("success"):
            time.sleep(
                self.stepper_settle_sec
            )

        return result

    def _align(
        self,
        tray_id: int,
    ) -> dict[str, Any]:

        if self.alignment_callback is None:
            return {
                "success": True,
                "bypass": True,
                "message": "ArUco alignment BYPASS",
            }

        return self.alignment_callback(
            tray_id
        )

    # ========================================================
    # 공급
    # ========================================================

    def supply_current_tray(
        self,
    ) -> dict[str, Any]:
        """
        현재 Supply Queue의 Tray 1개를
        선반 → Handoff까지 운반한다.
        """

        tray_id = (
            self.material_flow
            .get_current_supply_tray()
        )

        if tray_id is None:
            return {
                "success": False,
                "step": "START",
                "message": "공급할 Tray가 없습니다.",
            }

        history: list[dict[str, Any]] = []

        # ----------------------------------------------------
        # 1. Stage → Tray 위치
        # ----------------------------------------------------

        result = self.stage.move_to_tray(
            tray_id
        )

        history.append({
            "step": "MOVE_TO_TRAY",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "MOVE_TO_TRAY",
                result,
            )

        self.material_flow.supply_tray_arrived()

        # ----------------------------------------------------
        # 2. ArUco 정렬
        # ----------------------------------------------------

        result = self._align(
            tray_id
        )

        history.append({
            "step": "ARUCO_ALIGN",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "ARUCO_ALIGN",
                result,
            )

        self.material_flow.supply_alignment_complete()

        # ----------------------------------------------------
        # 3. Gripper 전진
        # ----------------------------------------------------

        result = self._extend()

        history.append({
            "step": "GRIPPER_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "GRIPPER_EXTEND",
                result,
            )

        # ----------------------------------------------------
        # 4. Tray 파지
        # ----------------------------------------------------

        result = self.gripper.close()

        history.append({
            "step": "GRIP_CLOSE",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "GRIP_CLOSE",
                result,
            )

        time.sleep(
            self.grip_settle_sec
        )

        # ----------------------------------------------------
        # 5. Load Cell → Tray 파지 확인
        # ----------------------------------------------------

        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "LOAD_VERIFY_PICK",
            "result": load_result,
        })

        if not load_result.get("success"):
            return self._failed(
                "LOAD_VERIFY_PICK",
                load_result,
            )

        if not load_result.get(
            "tray_present",
            False,
        ):
            return {
                "success": False,
                "step": "LOAD_VERIFY_PICK",
                "message": "Tray 파지 하중이 확인되지 않았습니다.",
                "loadcell": load_result,
                "history": history,
            }

        # ----------------------------------------------------
        # 6. Gripper 후진
        # ----------------------------------------------------

        result = self._retract()

        history.append({
            "step": "GRIPPER_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "GRIPPER_RETRACT",
                result,
            )

        self.material_flow.supply_extraction_complete()

        # ----------------------------------------------------
        # 7. Stage → Handoff
        # ----------------------------------------------------

        result = self.stage.move_to_handoff()

        history.append({
            "step": "MOVE_TO_HANDOFF",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "MOVE_TO_HANDOFF",
                result,
            )

        # ----------------------------------------------------
        # 8. Gripper 전진
        # ----------------------------------------------------

        result = self._extend()

        history.append({
            "step": "HANDOFF_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "HANDOFF_EXTEND",
                result,
            )

        # ----------------------------------------------------
        # 9. Tray 해제
        # ----------------------------------------------------

        result = self.gripper.open()

        history.append({
            "step": "GRIP_OPEN",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "GRIP_OPEN",
                result,
            )

        time.sleep(
            self.grip_settle_sec
        )

        # ----------------------------------------------------
        # 10. Load Cell → Tray 전달 확인
        # ----------------------------------------------------

        load_result = self.loadcell.tray_released(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "LOAD_VERIFY_RELEASE",
            "result": load_result,
        })

        if not load_result.get("success"):
            return self._failed(
                "LOAD_VERIFY_RELEASE",
                load_result,
            )

        if not load_result.get(
            "tray_released",
            False,
        ):
            return {
                "success": False,
                "step": "LOAD_VERIFY_RELEASE",
                "message": "Tray 전달 후 하중이 제거되지 않았습니다.",
                "loadcell": load_result,
                "history": history,
            }

        # ----------------------------------------------------
        # 11. Gripper 후진
        # ----------------------------------------------------

        result = self._retract()

        history.append({
            "step": "HANDOFF_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "HANDOFF_RETRACT",
                result,
            )

        status = (
            self.material_flow
            .supply_handoff_complete()
        )

        return {
            "success": True,
            "tray_id": tray_id,
            "message": f"TRAY {tray_id:02d} 공급 완료",
            "history": history,
            "material_flow": status,
        }

    # ========================================================
    # 반환
    # ========================================================

    def return_current_tray(
        self,
        tray_id: int,
    ) -> dict[str, Any]:
        """
        Handoff → 원래 Tray Slot 반납.

        현재 Stepper/ArUco가 미완성이어도
        BYPASS 구조로 전체 순서를 검증할 수 있다.
        """

        history: list[dict[str, Any]] = []

        # 1. 반환 Tray ID 등록
        try:
            self.material_flow.return_tray_identified(
                tray_id
            )
        except ValueError as exc:
            return {
                "success": False,
                "step": "RETURN_IDENTIFY",
                "message": str(exc),
            }

        # 2. Handoff에서 전진
        result = self._extend()

        history.append({
            "step": "RETURN_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_EXTEND",
                result,
            )

        # 3. 파지
        result = self.gripper.close()

        history.append({
            "step": "RETURN_GRIP_CLOSE",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_GRIP_CLOSE",
                result,
            )

        time.sleep(
            self.grip_settle_sec
        )

        # 4. Load Cell 파지 확인
        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "RETURN_LOAD_VERIFY_PICK",
            "result": load_result,
        })

        if (
            not load_result.get("success")
            or
            not load_result.get("tray_present")
        ):
            return {
                "success": False,
                "step": "RETURN_LOAD_VERIFY_PICK",
                "message": "반환 Tray 파지 확인 실패",
                "loadcell": load_result,
                "history": history,
            }

        # 5. 후진
        result = self._retract()

        if not result.get("success"):
            return self._failed(
                "RETURN_RETRACT",
                result,
            )

        self.material_flow.return_pick_complete()

        # 6. 해당 Slot으로 이동
        result = self.stage.move_to_tray(
            tray_id
        )

        history.append({
            "step": "RETURN_MOVE_TO_SLOT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_MOVE_TO_SLOT",
                result,
            )

        self.material_flow.return_slot_arrived()

        # 7. 삽입 방향으로 전진
        result = self._extend()

        if not result.get("success"):
            return self._failed(
                "RETURN_INSERT_EXTEND",
                result,
            )

        # 8. Tray 해제
        result = self.gripper.open()

        if not result.get("success"):
            return self._failed(
                "RETURN_GRIP_OPEN",
                result,
            )

        time.sleep(
            self.grip_settle_sec
        )

        # 9. Load Cell 해제 확인
        load_result = self.loadcell.tray_released(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "RETURN_LOAD_VERIFY_RELEASE",
            "result": load_result,
        })

        if (
            not load_result.get("success")
            or
            not load_result.get("tray_released")
        ):
            return {
                "success": False,
                "step": "RETURN_LOAD_VERIFY_RELEASE",
                "message": "반납 후 Tray 하중 제거 확인 실패",
                "loadcell": load_result,
                "history": history,
            }

        # 10. 후진
        result = self._retract()

        if not result.get("success"):
            return self._failed(
                "RETURN_INSERT_RETRACT",
                result,
            )

        self.material_flow.return_insert_complete()

        # 11. 다시 Handoff로 복귀
        result = self.stage.move_to_handoff()

        if not result.get("success"):
            return self._failed(
                "RETURN_TO_HANDOFF",
                result,
            )

        status = (
            self.material_flow
            .return_handoff_arrived()
        )

        return {
            "success": True,
            "tray_id": tray_id,
            "message": f"TRAY {tray_id:02d} 반납 완료",
            "history": history,
            "material_flow": status,
        }
