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
        gripper_servo_bypass: bool = False,
    ) -> None:

        self.material_flow = material_flow
        self.stage = stage
        self.gripper = gripper
        self.loadcell = loadcell
        self.gripper_stepper = gripper_stepper
        self.gripper_servo_bypass = bool(gripper_servo_bypass)

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

        response = {
            "success": False,
            "step": step,
            "message": message,
            "result": result,
        }

        if isinstance(result, dict):
            if result.get("cancelled"):
                response["cancelled"] = True

            if result.get("timeout"):
                response["timeout"] = True

        return response

    def _stop_requested(self) -> bool:
        """
        전체 Stage HARD STOP / ESTOP 요청 여부를 확인한다.

        Mock 등 _stop_event가 없는 Stage도 고려한다.
        """
        event = getattr(
            self.stage,
            "_stop_event",
            None,
        )

        return bool(
            event is not None
            and event.is_set()
        )

    def _cancelled(
        self,
        step: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "cancelled": True,
            "step": step,
            "message":
                "HARD STOP/ESTOP으로 Material Flow가 중단되었습니다.",
            "history":
                history if history is not None else [],
        }

    def _set_progress(
        self,
        *,
        phase: str,
        step: str,
        message: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        setter = getattr(
            self.material_flow,
            "set_progress",
            None,
        )

        if callable(setter):
            setter(
                phase=phase,
                step=step,
                message=message,
                detail=detail,
            )

    def _set_validation(
        self,
        *,
        validation_type: str,
        passed: bool,
        detail: dict[str, Any] | None = None,
    ) -> None:
        setter = getattr(
            self.material_flow,
            "set_validation",
            None,
        )

        if callable(setter):
            setter(
                validation_type=validation_type,
                passed=passed,
                detail=detail,
            )

    def _extend(self) -> dict[str, Any]:

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message":
                    "STOP 상태이므로 EXTEND를 실행하지 않습니다.",
            }

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

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message":
                    "STOP 상태이므로 RETRACT를 실행하지 않습니다.",
            }

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

    def _grip_close(self) -> dict[str, Any]:
        """
        Servo CLOSE.

        GRIPPER_SERVO_BYPASS=1 시험에서는
        G축은 그대로 동작시키고 Servo 명령만 생략한다.
        """

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message": "STOP 상태이므로 GRIP CLOSE를 실행하지 않습니다.",
            }

        if self.gripper_servo_bypass:
            return {
                "success": True,
                "bypass": True,
                "message": "Gripper Servo CLOSE BYPASS",
            }

        return self.gripper.close()

    def _grip_open(self) -> dict[str, Any]:
        """
        Servo OPEN.

        GRIPPER_SERVO_BYPASS=1 시험에서는
        G축은 그대로 동작시키고 Servo 명령만 생략한다.
        """

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message": "STOP 상태이므로 GRIP OPEN을 실행하지 않습니다.",
            }

        if self.gripper_servo_bypass:
            return {
                "success": True,
                "bypass": True,
                "message": "Gripper Servo OPEN BYPASS",
            }

        return self.gripper.open()

    def _align(
        self,
        tray_id: int,
    ) -> dict[str, Any]:

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message":
                    "STOP 상태이므로 ArUco 정렬을 실행하지 않습니다.",
            }

        if self.alignment_callback is None:
            return {
                "success": True,
                "bypass": True,
                "message": "ArUco alignment BYPASS",
            }

        return self.alignment_callback(
            tray_id
        )

    def _retry_supply_pick_once(
        self,
        tray_id: int,
    ) -> dict[str, Any]:
        """
        Tray 파지 실패 시 1회만 재시도한다.

        OPEN
        -> RETRACT
        -> ArUco 재보정 callback
        -> EXTEND
        -> CLOSE
        -> Load Cell 재확인

        alignment_callback이 없으면 ArUco 단계는 BYPASS된다.
        """

        history: list[dict[str, Any]] = []

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_OPEN",
                history,
            )

        result = self._grip_open()
        history.append({
            "step": "PICK_RETRY_OPEN",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_OPEN",
                "message": result.get(
                    "message",
                    "PICK 재시도 OPEN 실패",
                ),
                "history": history,
            }

        time.sleep(self.grip_settle_sec)

        result = self._retract()
        history.append({
            "step": "PICK_RETRY_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_RETRACT",
                "message": result.get(
                    "message",
                    "PICK 재시도 RETRACT 실패",
                ),
                "history": history,
            }

        result = self._align(tray_id)
        history.append({
            "step": "PICK_RETRY_ARUCO_ALIGN",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_ARUCO_ALIGN",
                "message": result.get(
                    "message",
                    "PICK 재시도 ArUco 보정 실패",
                ),
                "history": history,
            }

        result = self._extend()
        history.append({
            "step": "PICK_RETRY_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_EXTEND",
                "message": result.get(
                    "message",
                    "PICK 재시도 EXTEND 실패",
                ),
                "history": history,
            }

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_CLOSE",
                history,
            )

        result = self._grip_close()
        history.append({
            "step": "PICK_RETRY_CLOSE",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_CLOSE",
                "message": result.get(
                    "message",
                    "PICK 재시도 CLOSE 실패",
                ),
                "history": history,
            }

        time.sleep(self.grip_settle_sec)

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "PICK_RETRY_LOAD_VERIFY",
            "result": load_result,
        })

        if not load_result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_LOAD_VERIFY",
                "message": load_result.get(
                    "message",
                    "PICK 재시도 Load Cell 측정 실패",
                ),
                "loadcell": load_result,
                "history": history,
            }

        if not load_result.get(
            "tray_present",
            False,
        ):
            return {
                "success": False,
                "step": "PICK_FAILED",
                "message": (
                    "Tray 파지 재시도 후에도 "
                    "하중이 확인되지 않았습니다."
                ),
                "loadcell": load_result,
                "history": history,
            }

        return {
            "success": True,
            "loadcell": load_result,
            "history": history,
        }

    def _retry_return_pick_once(
        self,
    ) -> dict[str, Any]:
        """
        Handoff에서 반환 Tray 파지 실패 시 1회 재시도한다.

        OPEN
        -> RETRACT
        -> EXTEND
        -> CLOSE
        -> Load Cell 재확인

        Handoff 파지이므로 ArUco 재정렬은 수행하지 않는다.
        """

        history: list[dict[str, Any]] = []

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_RETURN_RETRY_OPEN",
                history,
            )

        result = self._grip_open()

        history.append({
            "step": "RETURN_PICK_RETRY_OPEN",
            "result": result,
        })

        if not result.get("success"):
            failure = self._failed(
                "RETURN_PICK_RETRY_OPEN",
                result,
            )
            failure["history"] = history
            return failure

        time.sleep(self.grip_settle_sec)

        result = self._retract()

        history.append({
            "step": "RETURN_PICK_RETRY_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            failure = self._failed(
                "RETURN_PICK_RETRY_RETRACT",
                result,
            )
            failure["history"] = history
            return failure

        result = self._extend()

        history.append({
            "step": "RETURN_PICK_RETRY_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            failure = self._failed(
                "RETURN_PICK_RETRY_EXTEND",
                result,
            )
            failure["history"] = history
            return failure

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_RETURN_RETRY_CLOSE",
                history,
            )

        result = self._grip_close()

        history.append({
            "step": "RETURN_PICK_RETRY_CLOSE",
            "result": result,
        })

        if not result.get("success"):
            failure = self._failed(
                "RETURN_PICK_RETRY_CLOSE",
                result,
            )
            failure["history"] = history
            return failure

        time.sleep(self.grip_settle_sec)

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_RETURN_RETRY_LOAD_VERIFY",
                history,
            )

        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "RETURN_PICK_RETRY_LOAD_VERIFY",
            "result": load_result,
        })

        if not load_result.get("success"):
            return {
                "success": False,
                "step": "RETURN_PICK_RETRY_LOAD_VERIFY",
                "message": load_result.get(
                    "message",
                    "반환 Tray PICK 재시도 Load Cell 측정 실패",
                ),
                "loadcell": load_result,
                "history": history,
            }

        if not load_result.get(
            "tray_present",
            False,
        ):
            return {
                "success": False,
                "step": "RETURN_PICK_FAILED",
                "message": (
                    "반환 Tray 파지 재시도 후에도 "
                    "하중이 확인되지 않았습니다."
                ),
                "loadcell": load_result,
                "history": history,
            }

        return {
            "success": True,
            "loadcell": load_result,
            "history": history,
        }

    def _recheck_release_once(
        self,
    ) -> dict[str, Any]:
        """
        OPEN 후 Tray 해제가 확인되지 않을 경우
        추가 대기 후 Load Cell을 딱 1회 재확인한다.

        재확인에도 실패하면 RETRACT / Stage 이동을 진행하지 않는다.
        """

        time.sleep(self.grip_settle_sec)

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
            )

        load_result = self.loadcell.tray_released(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        if not load_result.get("success"):
            return {
                "success": False,
                "step": "RELEASE_RECHECK",
                "message": load_result.get(
                    "message",
                    "Tray 해제 재확인 측정 실패",
                ),
                "loadcell": load_result,
            }

        if not load_result.get(
            "tray_released",
            False,
        ):
            return {
                "success": False,
                "step": "RELEASE_FAILED",
                "message": (
                    "Tray 해제 재확인 후에도 "
                    "하중이 남아 있습니다."
                ),
                "loadcell": load_result,
            }

        return {
            "success": True,
            "loadcell": load_result,
        }

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

        if getattr(
            self.material_flow,
            "supply_state",
            None,
        ) == "ABORTED":
            return {
                "success": False,
                "cancelled": True,
                "step": "START",
                "message": (
                    "Material Flow가 중단 상태입니다. "
                    "기구 상태 확인 후 작업을 다시 시작하세요."
                ),
            }

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

        self._set_progress(
            phase="SUPPLY",
            step="MOVE_TO_TRAY",
            message=f"TRAY {tray_id:02d} 위치로 이동 중",
            detail={"tray_id": tray_id},
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_MOVE_TO_TRAY",
                history,
            )

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

        self._set_progress(
            phase="SUPPLY",
            step="ALIGN",
            message="Tray 위치 보정 중",
            detail={"tray_id": tray_id},
        )

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

        self._set_progress(
            phase="SUPPLY",
            step="GRIPPER_EXTEND",
            message="Gripper가 Tray 방향으로 전진 중",
        )

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

        self._set_progress(
            phase="SUPPLY",
            step="GRIP_CLOSE",
            message="Tray 파지 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_CLOSE",
                history,
            )

        result = self._grip_close()

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

        self._set_progress(
            phase="SUPPLY",
            step="LOAD_VERIFY_PICK",
            message="Load Cell로 Tray 파지 상태 확인 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        self._set_validation(
            validation_type="PICK",
            passed=bool(
                load_result.get("success")
                and load_result.get("tray_present")
            ),
            detail={
                "average_weight_g":
                    load_result.get("average_weight_g"),
                "threshold_g":
                    load_result.get("threshold_g"),
            },
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
            self._set_progress(
                phase="SUPPLY",
                step="PICK_RETRY",
                message="Tray 파지 재시도 중",
            )

            retry_result = self._retry_supply_pick_once(
                tray_id
            )

            history.extend(
                retry_result.get(
                    "history",
                    [],
                )
            )

            if not retry_result.get("success"):
                return {
                    "success": False,
                    "step": retry_result.get(
                        "step",
                        "PICK_FAILED",
                    ),
                    "message": retry_result.get(
                        "message",
                        "Tray 파지 재시도 실패",
                    ),
                    "loadcell": retry_result.get(
                        "loadcell",
                    ),
                    "history": history,
                }

            retry_loadcell = (
                retry_result.get("loadcell")
                if isinstance(
                    retry_result.get("loadcell"),
                    dict,
                )
                else {}
            )

            self._set_validation(
                validation_type="PICK",
                passed=True,
                detail={
                    "average_weight_g":
                        retry_loadcell.get(
                            "average_weight_g"
                        ),
                    "threshold_g":
                        retry_loadcell.get(
                            "threshold_g"
                        ),
                    "retry": True,
                },
            )

        # ----------------------------------------------------
        # 6. Gripper 후진
        # ----------------------------------------------------

        self._set_progress(
            phase="SUPPLY",
            step="PICK_RETRACT",
            message="Tray 인출 후 Gripper 후진 중",
        )

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

        self._set_progress(
            phase="SUPPLY",
            step="MOVE_TO_HANDOFF",
            message="Conveyor Handoff 위치로 이동 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_MOVE_TO_HANDOFF",
                history,
            )

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

        self._set_progress(
            phase="SUPPLY",
            step="HANDOFF_EXTEND",
            message="Tray 전달을 위해 Gripper 전진 중",
        )

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

        self._set_progress(
            phase="SUPPLY",
            step="GRIP_OPEN",
            message="Handoff 위치에서 Tray 해제 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_OPEN",
                history,
            )

        result = self._grip_open()

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

        self._set_progress(
            phase="SUPPLY",
            step="LOAD_VERIFY_RELEASE",
            message="Load Cell로 Tray 전달 상태 확인 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
                history,
            )

        load_result = self.loadcell.tray_released(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        self._set_validation(
            validation_type="RELEASE",
            passed=bool(
                load_result.get("success")
                and load_result.get("tray_released")
            ),
            detail={
                "average_weight_g":
                    load_result.get("average_weight_g"),
                "threshold_g":
                    load_result.get("threshold_g"),
            },
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
            recheck_result = self._recheck_release_once()

            history.append({
                "step": "LOAD_RELEASE_RECHECK",
                "result": recheck_result,
            })

            if not recheck_result.get("success"):
                return {
                    "success": False,
                    "step": recheck_result.get(
                        "step",
                        "RELEASE_FAILED",
                    ),
                    "message": recheck_result.get(
                        "message",
                        "Tray 해제 재확인 실패",
                    ),
                    "loadcell": recheck_result.get(
                        "loadcell",
                    ),
                    "history": history,
                }

            recheck_loadcell = (
                recheck_result.get("loadcell")
                if isinstance(
                    recheck_result.get("loadcell"),
                    dict,
                )
                else {}
            )

            self._set_validation(
                validation_type="RELEASE",
                passed=True,
                detail={
                    "average_weight_g":
                        recheck_loadcell.get(
                            "average_weight_g"
                        ),
                    "threshold_g":
                        recheck_loadcell.get(
                            "threshold_g"
                        ),
                    "recheck": True,
                },
            )

        # ----------------------------------------------------
        # 11. Gripper 후진
        # ----------------------------------------------------

        self._set_progress(
            phase="SUPPLY",
            step="HANDOFF_RETRACT",
            message="Tray 전달 완료 후 Gripper 후진 중",
        )

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

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_SUPPLY_COMPLETE",
                history,
            )

        self._set_progress(
            phase="SUPPLY",
            step="COMPLETE",
            message=f"TRAY {tray_id:02d} 자동 조달 완료",
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

        if getattr(
            self.material_flow,
            "return_state",
            None,
        ) == "ABORTED":
            return {
                "success": False,
                "cancelled": True,
                "step": "RETURN_START",
                "message": (
                    "Material Flow가 중단 상태입니다. "
                    "기구 상태 확인 후 작업을 다시 시작하세요."
                ),
                "history": history,
            }

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
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_CLOSE",
                history,
            )

        result = self._grip_close()

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
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self.loadcell.tray_present(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "RETURN_LOAD_VERIFY_PICK",
            "result": load_result,
        })

        if not load_result.get("success"):
            return {
                "success": False,
                "step": "RETURN_LOAD_VERIFY_PICK",
                "message": load_result.get(
                    "message",
                    "반환 Tray Load Cell 측정 실패",
                ),
                "loadcell": load_result,
                "history": history,
            }

        if not load_result.get(
            "tray_present",
            False,
        ):
            retry_result = self._retry_return_pick_once()

            history.extend(
                retry_result.get(
                    "history",
                    [],
                )
            )

            if not retry_result.get("success"):
                return {
                    "success": False,
                    "step": retry_result.get(
                        "step",
                        "RETURN_PICK_FAILED",
                    ),
                    "message": retry_result.get(
                        "message",
                        "반환 Tray 파지 재시도 실패",
                    ),
                    "loadcell": retry_result.get(
                        "loadcell",
                    ),
                    "history": history,
                }

        # 5. 후진
        result = self._retract()

        history.append({
            "step": "RETURN_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_RETRACT",
                result,
            )

        self.material_flow.return_pick_complete()

        # 6. 해당 Slot으로 이동
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_MOVE_TO_TRAY",
                history,
            )

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

        history.append({
            "step": "RETURN_INSERT_EXTEND",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_INSERT_EXTEND",
                result,
            )

        # 8. Tray 해제
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_GRIP_OPEN",
                history,
            )

        result = self._grip_open()

        history.append({
            "step": "RETURN_GRIP_OPEN",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_GRIP_OPEN",
                result,
            )

        time.sleep(
            self.grip_settle_sec
        )

        # 9. Load Cell 해제 확인
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
                history,
            )

        load_result = self.loadcell.tray_released(
            threshold_g=self.tray_present_threshold_g,
            samples=self.loadcell_samples,
        )

        history.append({
            "step": "RETURN_LOAD_VERIFY_RELEASE",
            "result": load_result,
        })

        if not load_result.get("success"):
            return {
                "success": False,
                "step": "RETURN_LOAD_VERIFY_RELEASE",
                "message": load_result.get(
                    "message",
                    "반납 Load Cell 측정 실패",
                ),
                "loadcell": load_result,
                "history": history,
            }

        if not load_result.get(
            "tray_released",
            False,
        ):
            recheck_result = self._recheck_release_once()

            history.append({
                "step": "RETURN_RELEASE_RECHECK",
                "result": recheck_result,
            })

            if not recheck_result.get("success"):
                return {
                    "success": False,
                    "step": recheck_result.get(
                        "step",
                        "RELEASE_FAILED",
                    ),
                    "message": recheck_result.get(
                        "message",
                        "반납 Tray 해제 재확인 실패",
                    ),
                    "loadcell": recheck_result.get(
                        "loadcell",
                    ),
                    "history": history,
                }

        # 10. 후진
        result = self._retract()

        history.append({
            "step": "RETURN_INSERT_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_INSERT_RETRACT",
                result,
            )

        self.material_flow.return_insert_complete()

        # 11. 다시 Handoff로 복귀
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_MOVE_TO_HANDOFF",
                history,
            )

        result = self.stage.move_to_handoff()

        history.append({
            "step": "RETURN_TO_HANDOFF",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_TO_HANDOFF",
                result,
            )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_RETURN_COMPLETE",
                history,
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
