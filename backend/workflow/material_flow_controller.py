import re


class MaterialFlowController:
    """
    X-Z Stage의 Tray 공급 / 회수 흐름을 관리한다.

    작업자의 Picking / Vision Workflow와 독립적으로 동작한다.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.supply_queue = []
        self.supply_index = 0
        self.supplied_trays = []

        self.return_queue = []
        self.returned_trays = []

        self.current_return_tray = None

        self.supply_state = "IDLE"
        self.return_state = "IDLE"

        # UI 실시간 자동 조달/반납 진행 표시용
        self.current_phase = "IDLE"
        self.current_step = "IDLE"
        self.current_message = "대기"
        self.current_detail = {}
        self.last_validation = None

    def set_progress(
        self,
        *,
        phase,
        step,
        message,
        detail=None,
    ):
        """
        MaterialFlowExecutor의 현재 실제 동작을 UI에 노출한다.
        """
        self.current_phase = str(phase)
        self.current_step = str(step)
        self.current_message = str(message)
        self.current_detail = (
            dict(detail)
            if isinstance(detail, dict)
            else {}
        )

        return self.get_status()

    def set_validation(
        self,
        *,
        validation_type,
        passed,
        detail=None,
    ):
        """
        Load Cell 등 최근 검증 결과를 보존한다.
        """
        self.last_validation = {
            "type": str(validation_type),
            "passed": bool(passed),
            "detail": (
                dict(detail)
                if isinstance(detail, dict)
                else {}
            ),
        }

        return self.get_status()

    def abort(self):
        """
        현재 자동 Material Flow를 중단 상태로 고정한다.

        Queue / 진행 이력은 보존하지만 자동 재개는 허용하지 않는다.
        새 작업은 start()를 통해 명시적으로 다시 시작해야 한다.
        """
        self.supply_state = "ABORTED"
        self.return_state = "ABORTED"

        self.current_phase = "ABORTED"
        self.current_step = "ABORTED"
        self.current_message = (
            "자동 운전이 중단되었습니다."
        )

        return self.get_status()

    @staticmethod
    def _normalize_tray_id(value):
        """
        1, "1", "TRAY01", "TRAY 01" 등을
        정수 Tray ID로 정규화한다.
        """

        if isinstance(value, int):
            tray_id = value

        elif isinstance(value, str):
            match = re.search(r"(\d+)", value)

            if not match:
                raise ValueError(
                    f"Tray ID를 해석할 수 없습니다: {value}"
                )

            tray_id = int(match.group(1))

        else:
            raise ValueError(
                f"지원하지 않는 Tray ID 형식: {value}"
            )

        if tray_id not in range(1, 7):
            raise ValueError(
                f"잘못된 Tray ID: {tray_id}"
            )

        return tray_id

    def start(self, items):
        """
        작업지시서 품목에서 공급할 Tray Queue를 생성한다.

        동일 Tray가 여러 품목에서 사용될 경우
        한 번만 공급한다.
        """

        self.reset()

        seen = set()

        for item in items:
            tray_value = item.get("tray")

            tray_id = self._normalize_tray_id(
                tray_value
            )

            if tray_id in seen:
                continue

            seen.add(tray_id)
            self.supply_queue.append(tray_id)

        if self.supply_queue:
            self.supply_state = "TRAY_MOVING"
            self.return_state = "WAIT_SUPPLY_COMPLETE"

            self.current_phase = "SUPPLY"
            self.current_step = "MOVE_TO_TRAY"
            self.current_message = (
                "첫 번째 Tray 위치 이동 대기"
            )

        return self.get_status()

    def get_current_supply_tray(self):
        if (
            self.supply_index
            >= len(self.supply_queue)
        ):
            return None

        return self.supply_queue[
            self.supply_index
        ]

    # ========================================================
    # SUPPLY
    # ========================================================

    def supply_tray_arrived(self):
        """
        Stage가 선반의 현재 공급 Tray 위치에 도착.
        """

        self.supply_state = "ARUCO_ALIGN"

        return self.get_status()

    def supply_alignment_complete(self):
        """
        ArUco 위치 보정 완료.

        현재 카메라가 없을 때는 Mock/BYPASS 가능.
        """

        self.supply_state = "EXTRACTING"

        return self.get_status()

    def supply_extraction_complete(self):
        """
        Gripper 인출 + 캐리지 Load Cell 확인 완료.

        현재 장비가 없을 때는 Mock/BYPASS 가능.
        """

        self.supply_state = "HANDOFF_MOVING"

        return self.get_status()

    def supply_handoff_complete(self):
        """
        현재 Tray를 Conveyor Handoff에 전달 완료.
        이후 작업자 Workflow와 독립적으로
        바로 다음 Tray 공급을 시작한다.
        """

        tray_id = (
            self.get_current_supply_tray()
        )

        if tray_id is None:
            raise ValueError(
                "현재 공급 중인 Tray가 없습니다."
            )

        if tray_id not in self.supplied_trays:
            self.supplied_trays.append(
                tray_id
            )

        self.supply_index += 1

        if (
            self.supply_index
            < len(self.supply_queue)
        ):
            self.supply_state = "TRAY_MOVING"

        else:
            self.supply_state = "SUPPLY_COMPLETE"

            # 모든 공급이 끝났으므로
            # Stage는 Handoff에서 회수를 기다린다.
            self.return_state = "RETURN_WAIT"

        return self.get_status()

    # ========================================================
    # RETURN
    # ========================================================

    def enqueue_return(self, tray_id):
        """
        작업자가 사용을 완료한 Tray를
        반환 대기 Queue에 등록한다.
        """

        tray_id = self._normalize_tray_id(
            tray_id
        )

        if tray_id not in self.supplied_trays:
            raise ValueError(
                f"아직 공급되지 않은 Tray입니다: TRAY {tray_id:02d}"
            )

        if tray_id in self.returned_trays:
            return self.get_status()

        if tray_id not in self.return_queue:
            self.return_queue.append(
                tray_id
            )

        return self.get_status()

    def return_tray_identified(
        self,
        tray_id,
    ):
        """
        Handoff로 돌아온 Tray의 ArUco ID를 확인.

        회수 순서는 고정하지 않는다.
        실제 검출된 Tray ID를 기준으로 복귀시킨다.
        """

        tray_id = self._normalize_tray_id(
            tray_id
        )

        if (
            self.supply_state
            != "SUPPLY_COMPLETE"
        ):
            raise ValueError(
                "모든 Tray 공급이 끝나기 전에는 "
                "회수를 시작할 수 없습니다."
            )

        if tray_id not in self.return_queue:
            raise ValueError(
                f"회수 대기 Tray가 아닙니다: TRAY {tray_id:02d}"
            )

        self.current_return_tray = (
            tray_id
        )

        self.return_state = (
            "RETURN_PICKING"
        )

        return self.get_status()

    def return_pick_complete(self):
        """
        Handoff에서 반환 Tray 파지 완료.
        """

        if self.current_return_tray is None:
            raise ValueError(
                "현재 회수 중인 Tray가 없습니다."
            )

        self.return_state = (
            "RETURNING_TO_SLOT"
        )

        return self.get_status()

    def return_slot_arrived(self):
        """
        반환 Tray의 목표 Slot에 도착.
        """

        if self.current_return_tray is None:
            raise ValueError(
                "현재 회수 중인 Tray가 없습니다."
            )

        self.return_state = (
            "RETURN_INSERTING"
        )

        return self.get_status()

    def return_insert_complete(self):
        """
        Tray를 선반에 삽입하고 Gripper 해제 완료.

        실제 Stage는 아직 Tray Slot에 있으므로
        다음 반환 대기 전에 Handoff로 복귀해야 한다.
        """

        tray_id = self.current_return_tray

        if tray_id is None:
            raise ValueError(
                "현재 회수 중인 Tray가 없습니다."
            )

        if tray_id in self.return_queue:
            self.return_queue.remove(
                tray_id
            )

        if tray_id not in self.returned_trays:
            self.returned_trays.append(
                tray_id
            )

        # 실제 Handoff 복귀가 끝날 때까지 현재 Tray를 유지한다.
        # 그래야 Frontend가 다음 Tray를 너무 일찍 탐색하지 않는다.
        self.current_return_tray = tray_id
        self.return_state = "RETURN_TO_HANDOFF"

        return self.get_status()

    def return_handoff_arrived(self):
        """
        Tray 복귀 후 Stage가 다시
        Conveyor Handoff 위치에 도착.
        """

        # 이제 실제 Handoff에 도착했으므로
        # 다음 반환 Tray를 받을 수 있다.
        self.current_return_tray = None

        if (
            len(self.returned_trays)
            == len(self.supplied_trays)
            and len(self.supplied_trays) > 0
        ):
            self.return_state = "ALL_RETURNED"

        else:
            self.return_state = "RETURN_WAIT"

        return self.get_status()

    # ========================================================
    # STATUS
    # ========================================================

    def get_status(self):
        return {
            "supply_state":
                self.supply_state,

            "supply_queue":
                list(self.supply_queue),

            "supply_index":
                self.supply_index,

            "current_supply_tray":
                self.get_current_supply_tray(),

            "supplied_trays":
                list(self.supplied_trays),

            "return_state":
                self.return_state,

            "current_phase":
                self.current_phase,

            "current_step":
                self.current_step,

            "current_message":
                self.current_message,

            "current_detail":
                dict(self.current_detail),

            "last_validation":
                (
                    dict(self.last_validation)
                    if isinstance(
                        self.last_validation,
                        dict,
                    )
                    else None
                ),

            "return_queue":
                list(self.return_queue),

            "current_return_tray":
                self.current_return_tray,

            "returned_trays":
                list(self.returned_trays),

            "supply_complete":
                (
                    self.supply_state
                    == "SUPPLY_COMPLETE"
                ),

            "all_returned":
                (
                    self.return_state
                    == "ALL_RETURNED"
                ),
        }
