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
        tray_present_threshold_g: float = 10.0,
        loadcell_samples: int = 5,
        grip_settle_sec: float = 0.5,
        stepper_settle_sec: float = 0.3,
        gripper_servo_bypass: bool = False,
        tray_weight_change_threshold_g: float = 20.0,
        release_weight_change_threshold_g: float = 15.0,
        release_monitor_timeout_sec: float = 5.0,
        release_monitor_poll_sec: float = 0.2,
        release_confirm_count: int = 3,
        return_slot_z_offset_mm: float = 0.0,
        handoff_pick_z_offset_mm: float = 0.0,
        handoff_drop_extend_reduction_mm: float = 0.0,
        gripper_full_extend_mm: float = 250.0,
    ) -> None:

        self.material_flow = material_flow
        self.stage = stage
        self.gripper = gripper
        self.loadcell = loadcell
        self.gripper_stepper = gripper_stepper
        self.gripper_servo_bypass = bool(gripper_servo_bypass)

        # HX711 #1은 절대 무게가 아니라 EXTEND 직전과 RETRACT 완료 후의
        # 5회 평균 하중 변화량으로 Tray 적재/하역 여부만 판정한다.
        self.tray_weight_change_threshold_g = max(
            float(tray_weight_change_threshold_g),
            0.0,
        )
        self._pre_extend_load_g: float | None = None

        self.release_weight_change_threshold_g = max(
            float(release_weight_change_threshold_g),
            0.0,
        )

        self.release_monitor_timeout_sec = max(
            float(release_monitor_timeout_sec),
            0.1,
        )

        self.release_monitor_poll_sec = max(
            float(release_monitor_poll_sec),
            0.05,
        )

        self.release_confirm_count = max(
            int(release_confirm_count),
            1,
        )

        # 공급용 Mapping 좌표는 그대로 사용하고,
        # 반납 시에만 Z축 삽입 높이를 추가 보정한다.
        self.return_slot_z_offset_mm = float(
            return_slot_z_offset_mm
        )

        # Handoff/최종 Load Cell 위치에서 Tray를 다시 집을 때만
        # 적용하는 Z 미세 보정값.
        # Z+는 위쪽이므로 음수 값은 Stage 하강을 의미한다.
        self.handoff_pick_z_offset_mm = float(
            handoff_pick_z_offset_mm
        )

        # Handoff에 Tray를 내려놓을 때만 G축 전진거리를 줄인다.
        # 기존 STM generic MOVE G 프로토콜을 사용하므로 STM 수정은 없다.
        self.handoff_drop_extend_reduction_mm = max(
            float(handoff_drop_extend_reduction_mm),
            0.0,
        )
        self.gripper_full_extend_mm = float(gripper_full_extend_mm)

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

        baseline_result = self.loadcell.read_average(
            samples=self.loadcell_samples,
        )

        if not baseline_result.get("success"):
            return {
                "success": False,
                "message": baseline_result.get(
                    "message",
                    "EXTEND 직전 Load Cell baseline 측정 실패",
                ),
                "loadcell": baseline_result,
            }

        self._pre_extend_load_g = float(
            baseline_result["average_weight_g"]
        )

        if self.gripper_stepper is None:
            return {
                "success": True,
                "bypass": True,
                "message": "Gripper EXTEND BYPASS",
                "load_baseline_g": self._pre_extend_load_g,
            }

        result = self.gripper_stepper.extend()

        if result.get("success"):
            time.sleep(
                self.stepper_settle_sec
            )

        return result

    def _extend_handoff_drop(self) -> dict[str, Any]:
        """Handoff 하역 시에만 정상 250 mm보다 짧게 전진한다."""
        reduction = self.handoff_drop_extend_reduction_mm
        if reduction <= 1e-6:
            return self._extend()

        target_mm = self.gripper_full_extend_mm - reduction
        if target_mm <= 0.0:
            return {
                "success": False,
                "message": "Handoff 하역 G축 목표거리가 0 mm 이하입니다.",
            }

        if self._stop_requested():
            return {
                "success": False,
                "cancelled": True,
                "message": "STOP 상태이므로 Handoff EXTEND를 실행하지 않습니다.",
            }

        # RELEASE 변화량 판정을 위해 EXTEND 직전 5회 평균 baseline 유지.
        baseline_result = self.loadcell.read_average(
            samples=self.loadcell_samples,
        )
        if not baseline_result.get("success"):
            return baseline_result
        self._pre_extend_load_g = float(baseline_result["average_weight_g"])

        if self.gripper_stepper is None:
            return {
                "success": True,
                "bypass": True,
                "message": f"Gripper Handoff EXTEND {target_mm:.1f} mm BYPASS",
                "load_baseline_g": self._pre_extend_load_g,
            }

        move_to_mm = getattr(self.gripper_stepper, "move_to_mm", None)
        if not callable(move_to_mm):
            return {
                "success": False,
                "message": "Gripper Adapter가 임의 G축 위치 이동을 지원하지 않습니다.",
            }

        result = move_to_mm(target_mm)
        if result.get("success"):
            time.sleep(self.stepper_settle_sec)
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

    def _retract_empty_with_limit_recovery(self) -> dict[str, Any]:
        """
        Tray를 내려놓은 뒤 빈 Gripper를 수납한다.

        정상적으로는 일반 RETRACT(논리 0 mm)를 사용한다.
        다만 위치 오차 때문에 RETRACT 도중 물리 후진 리밋이 먼저
        감지되어 정확히 FAULT / HOMED 0 / RETRACT_LIMIT 1 상태가 된
        경우에만 HOME을 1회 실행하여 G축 기준점을 복구한다.

        Tray를 잡고 있는 PICK RETRACT에는 이 복구를 사용하지 않는다.
        """

        retract_result = self._retract()

        if retract_result.get("success"):
            return retract_result

        if (
            retract_result.get("cancelled")
            or retract_result.get("timeout")
            or self._stop_requested()
        ):
            return retract_result

        message = str(
            retract_result.get("message", "")
        ).upper()

        recoverable_limit_fault = all((
            "GRIPPER STATUS FAULT" in message,
            "HOMED 0" in message,
            "RETRACT_LIMIT 1" in message,
            "EXTEND_LIMIT 0" in message,
        ))

        if not recoverable_limit_fault:
            return retract_result

        home = getattr(
            self.gripper_stepper,
            "home",
            None,
        )

        if not callable(home):
            return retract_result

        home_result = home()

        if not home_result.get("success"):
            return {
                "success": False,
                "recovery_attempted": True,
                "message": (
                    "빈 Gripper RETRACT 중 후진 리밋 FAULT 발생 후 "
                    "HOME 복구에도 실패했습니다: "
                    f"{home_result.get('message', 'HOME 실패')}"
                ),
                "retract_result": retract_result,
                "home_result": home_result,
            }

        if self.stepper_settle_sec > 0.0:
            time.sleep(self.stepper_settle_sec)

        return {
            "success": True,
            "recovered": True,
            "recovery": "HOME_AFTER_RETRACT_LIMIT_FAULT",
            "message": (
                "빈 Gripper RETRACT 중 후진 리밋 FAULT를 "
                "HOME으로 복구했습니다."
            ),
            "retract_result": retract_result,
            "home_result": home_result,
        }

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

    def _wait_after_release_open(self) -> None:
        """
        실제 Servo에서는 OPEN 후 기계적 안정화를 기다린다.

        Servo BYPASS에서는 별도 고정 대기를 하지 않는다.
        작업자가 Tray를 제거하는 시간은
        _verify_release_by_change()의 실시간 감시 구간에서 제공한다.
        """

        if (
            not self.gripper_servo_bypass
            and self.grip_settle_sec > 0.0
        ):
            time.sleep(self.grip_settle_sec)

    def _measure_release_baseline(self) -> dict[str, Any]:
        """
        OPEN 직전 Load Cell 평균 하중을 기준값으로 저장한다.
        """

        return self.loadcell.read_average(
            samples=self.loadcell_samples,
        )

    def _verify_release_by_change(
        self,
        *,
        baseline_weight_g: float,
    ) -> dict[str, Any]:
        """
        OPEN 이후 Load Cell을 일정 시간 계속 감시한다.

        현재 평균값과 OPEN 직전 baseline의 절댓값 차이가
        threshold 이상인 상태가 연속 confirm_count회 관측되면
        Tray가 해제된 것으로 판정한다.
        """

        deadline = (
            time.monotonic()
            + self.release_monitor_timeout_sec
        )

        consecutive = 0
        max_change_g = 0.0
        last_result: dict[str, Any] | None = None
        last_weight_g = float(baseline_weight_g)
        last_change_g = 0.0

        while time.monotonic() < deadline:

            if self._stop_requested():
                return {
                    "success": False,
                    "cancelled": True,
                    "message":
                        "STOP 상태이므로 RELEASE 감시를 중단합니다.",
                }

            result = self.loadcell.read_average(
                samples=self.loadcell_samples,
            )

            if not result.get("success"):
                return result

            last_result = result

            current_weight_g = float(
                result["average_weight_g"]
            )

            weight_change_g = abs(
                current_weight_g
                - float(baseline_weight_g)
            )

            last_weight_g = current_weight_g
            last_change_g = weight_change_g
            max_change_g = max(
                max_change_g,
                weight_change_g,
            )

            print(
                "[RELEASE MONITOR] "
                f"baseline={float(baseline_weight_g):.1f}g "
                f"current={current_weight_g:.1f}g "
                f"change={weight_change_g:.1f}g "
                f"threshold={self.release_weight_change_threshold_g:.1f}g "
                f"confirm={consecutive}/{self.release_confirm_count}"
            )

            if (
                weight_change_g
                >= self.release_weight_change_threshold_g
            ):
                consecutive += 1
            else:
                consecutive = 0

            if consecutive >= self.release_confirm_count:
                return {
                    **result,
                    "success": True,
                    "tray_released": True,
                    "baseline_weight_g":
                        float(baseline_weight_g),
                    "release_weight_g":
                        current_weight_g,
                    "weight_change_g":
                        weight_change_g,
                    "max_weight_change_g":
                        max_change_g,
                    "threshold_g":
                        self.release_weight_change_threshold_g,
                    "confirm_count":
                        consecutive,
                }

            time.sleep(
                self.release_monitor_poll_sec
            )

        if last_result is None:
            return {
                "success": False,
                "message":
                    "RELEASE Load Cell 측정값을 얻지 못했습니다.",
            }

        return {
            **last_result,
            "success": True,
            "tray_released": False,
            "baseline_weight_g":
                float(baseline_weight_g),
            "release_weight_g":
                last_weight_g,
            "weight_change_g":
                last_change_g,
            "max_weight_change_g":
                max_change_g,
            "threshold_g":
                self.release_weight_change_threshold_g,
            "confirm_count":
                consecutive,
        }

    def _verify_pick_by_change(self) -> dict[str, Any]:
        """EXTEND 직전보다 RETRACT 후 하중이 20g 이상 증가했는지 확인한다."""

        if self._pre_extend_load_g is None:
            return {
                "success": False,
                "message": "PICK Load Cell baseline이 없습니다.",
            }

        result = self.loadcell.read_average(
            samples=self.loadcell_samples,
        )

        if not result.get("success"):
            return result

        before_g = float(self._pre_extend_load_g)
        after_g = float(result["average_weight_g"])
        change_g = after_g - before_g

        passed = (
            change_g
            >= self.tray_weight_change_threshold_g
        )

        print(
            "[PICK LOAD CHANGE] "
            f"before={before_g:.1f}g "
            f"after={after_g:.1f}g "
            f"change={change_g:+.1f}g "
            f"threshold=+{self.tray_weight_change_threshold_g:.1f}g "
            f"passed={passed}"
        )

        return {
            **result,
            "measured_average_weight_g": after_g,

            # 기존 UI가 average_weight_g를 표시하므로
            # 절대값 대신 실제 판정 변화량을 표시한다.
            "average_weight_g": change_g,

            "tray_present": passed,
            "baseline_weight_g": before_g,
            "after_weight_g": after_g,
            "weight_change_g": change_g,
            "threshold_g":
                self.tray_weight_change_threshold_g,
            "comparison": "increase",
        }

    def _verify_release_by_change_once(
        self,
    ) -> dict[str, Any]:
        """EXTEND 직전보다 RETRACT 후 하중이 20g 이상 감소했는지 확인한다."""

        if self._pre_extend_load_g is None:
            return {
                "success": False,
                "message": "RELEASE Load Cell baseline이 없습니다.",
            }

        result = self.loadcell.read_average(
            samples=self.loadcell_samples,
        )

        if not result.get("success"):
            return result

        before_g = float(self._pre_extend_load_g)
        after_g = float(result["average_weight_g"])
        change_g = before_g - after_g

        passed = (
            change_g
            >= self.tray_weight_change_threshold_g
        )

        print(
            "[RELEASE LOAD CHANGE] "
            f"before={before_g:.1f}g "
            f"after={after_g:.1f}g "
            f"drop={change_g:+.1f}g "
            f"threshold=+{self.tray_weight_change_threshold_g:.1f}g "
            f"passed={passed}"
        )

        return {
            **result,
            "measured_average_weight_g": after_g,
            "average_weight_g": change_g,
            "tray_released": passed,
            "baseline_weight_g": before_g,
            "after_weight_g": after_g,
            "weight_change_g": change_g,
            "threshold_g":
                self.tray_weight_change_threshold_g,
            "comparison": "decrease",
        }

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
        -> RETRACT
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

        # Tray를 선반에서 캐리지 위로 완전히 인출한 뒤
        # 캐리지 Load Cell로 존재 여부를 판정한다.
        result = self._retract()
        history.append({
            "step": "PICK_RETRY_EXTRACT_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return {
                "success": False,
                "step": "PICK_RETRY_EXTRACT_RETRACT",
                "message": result.get(
                    "message",
                    "PICK 재시도 인출 RETRACT 실패",
                ),
                "history": history,
            }

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self._verify_pick_by_change()

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
        -> RETRACT
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

        # Handoff Tray를 캐리지 위로 완전히 인출한 뒤
        # 캐리지 Load Cell로 존재 여부를 판정한다.
        result = self._retract()
        history.append({
            "step": "RETURN_PICK_RETRY_EXTRACT_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            failure = self._failed(
                "RETURN_PICK_RETRY_EXTRACT_RETRACT",
                result,
            )
            failure["history"] = history
            return failure

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_RETURN_RETRY_LOAD_VERIFY",
                history,
            )

        load_result = self._verify_pick_by_change()

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
        """동일 EXTEND 직전 baseline 대비 하중 감소량을 1회 재확인한다."""

        time.sleep(self.grip_settle_sec)

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
            )

        load_result = self._verify_release_by_change_once()

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
                    f"하중 감소량이 "
                    f"{self.tray_weight_change_threshold_g:.1f}g "
                    "미만입니다."
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
        # 5. Gripper 후진 → Tray를 캐리지 위로 완전히 인출
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

        # ----------------------------------------------------
        # 6. Load Cell → 캐리지 위 Tray 존재 확인
        # ----------------------------------------------------

        self._set_progress(
            phase="SUPPLY",
            step="LOAD_VERIFY_PICK",
            message="Load Cell로 Tray 인출 상태 확인 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self._verify_pick_by_change()

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

        # 정상 경로와 PICK 재시도 경로 모두 이 시점에는
        # RETRACT 및 Load Cell 확인이 완료된 상태다.
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
            message=(
                "Tray 전달을 위해 Gripper 전진 중 "
                f"({self.gripper_full_extend_mm - self.handoff_drop_extend_reduction_mm:.1f} mm)"
            ),
        )

        result = self._extend_handoff_drop()

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

        self._wait_after_release_open()

        # ----------------------------------------------------
        # 10. Gripper 후진 → Tray를 캐리지에서 완전히 분리
        # ----------------------------------------------------

        self._set_progress(
            phase="SUPPLY",
            step="HANDOFF_RETRACT",
            message="Tray 전달 후 Gripper 후진 중",
        )

        result = self._retract_empty_with_limit_recovery()

        history.append({
            "step": "HANDOFF_RETRACT",
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "HANDOFF_RETRACT",
                result,
            )

        # ----------------------------------------------------
        # 11. Load Cell → 캐리지에서 Tray가 사라졌는지 확인
        # ----------------------------------------------------

        self._set_progress(
            phase="SUPPLY",
            step="LOAD_VERIFY_RELEASE",
            message="Load Cell로 Tray 전달 완료 상태 확인 중",
        )

        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
                history,
            )

        load_result = self._verify_release_by_change_once()

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

        # 2. Handoff/최종 Load Cell에서 Tray를 다시 집기 위한 Z 보정
        # Handoff에 내려놓는 높이는 변경하지 않고 PICK 직전에만 적용한다.
        if abs(self.handoff_pick_z_offset_mm) > 1e-6:
            self._set_progress(
                phase="RETURN",
                step="HANDOFF_PICK_Z_OFFSET",
                message=(
                    "Handoff Tray PICK 높이 "
                    f"Z {self.handoff_pick_z_offset_mm:+.1f} mm 보정 중"
                ),
                detail={
                    "tray_id": tray_id,
                    "z_offset_mm": self.handoff_pick_z_offset_mm,
                },
            )

            result = self.stage.move_relative(
                0.0,
                self.handoff_pick_z_offset_mm,
            )

            history.append({
                "step": "HANDOFF_PICK_Z_OFFSET",
                "z_offset_mm": self.handoff_pick_z_offset_mm,
                "result": result,
            })

            if not result.get("success"):
                return self._failed(
                    "HANDOFF_PICK_Z_OFFSET",
                    result,
                )

        # 3. Handoff에서 전진
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

        # 4. 후진 → Handoff Tray를 캐리지 위로 완전히 인출
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

        # 5. Load Cell → 캐리지 위 Tray 존재 확인
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_PICK",
                history,
            )

        load_result = self._verify_pick_by_change()

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

        # 정상 경로와 PICK 재시도 경로 모두 이 시점에는
        # RETRACT 및 Load Cell 확인이 완료된 상태다.
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

        # ----------------------------------------------------
        # 반납 전용 Z 높이 보정
        #
        # 공급/인출 시에는 기존 Mapping 좌표를 그대로 사용하고,
        # 반납 시에만 Slot 도착 후 Z축을 추가 상승시킨다.
        # ----------------------------------------------------
        if abs(self.return_slot_z_offset_mm) > 1e-6:
            self._set_progress(
                phase="RETURN",
                step="RETURN_SLOT_Z_OFFSET",
                message=(
                    f"반납 삽입 높이 Z "
                    f"{self.return_slot_z_offset_mm:+.1f} mm 보정 중"
                ),
                detail={
                    "tray_id": tray_id,
                    "z_offset_mm": self.return_slot_z_offset_mm,
                },
            )

            result = self.stage.move_relative(
                0.0,
                self.return_slot_z_offset_mm,
            )

            history.append({
                "step": "RETURN_SLOT_Z_OFFSET",
                "z_offset_mm": self.return_slot_z_offset_mm,
                "result": result,
            })

            print(
                "[RETURN Z OFFSET] "
                f"TRAY {tray_id:02d} "
                f"Z {self.return_slot_z_offset_mm:+.1f} mm "
                f"target={result.get('target_mm')}"
            )

            if not result.get("success"):
                return self._failed(
                    "RETURN_SLOT_Z_OFFSET",
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

        self._wait_after_release_open()

        # 9. 반납 후 빈 Gripper 수납
        # 정상 RETRACT를 우선 사용하고, 물리 후진 리밋이 먼저
        # 들어온 특정 위치 오차 FAULT에서만 HOME으로 복구한다.
        self._set_progress(
            phase="RETURN",
            step="RETURN_INSERT_RETRACT",
            message="Tray 반납 후 Gripper 후진 중",
        )

        result = self._retract_empty_with_limit_recovery()

        history.append({
            "step": "RETURN_INSERT_RETRACT",
            "action": result.get(
                "recovery",
                "GRIPPER_RETRACT",
            ),
            "result": result,
        })

        if not result.get("success"):
            return self._failed(
                "RETURN_INSERT_RETRACT",
                result,
            )

        # 10. Load Cell → 캐리지에서 Tray가 사라졌는지 확인
        if self._stop_requested():
            return self._cancelled(
                "BEFORE_LOAD_VERIFY_RELEASE",
                history,
            )

        load_result = self._verify_release_by_change_once()

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
