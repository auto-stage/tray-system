class WorkflowController:

    def __init__(self):
        self.state = "IDLE"
        self.current_item_index = 0
        self.items = []

    def start_work(self, items):
        """
        작업 시작.
        분석/검토가 끝난 작업 품목 목록을 받아서
        첫 번째 품목부터 시작한다.
        """

        self.items = items
        self.current_item_index = 0

        if not self.items:
            self.state = "IDLE"

            return self.get_status()

        self.state = "TRAY_MOVING"

        return self.get_status()

    def get_current_item(self):
        """
        현재 작업 중인 품목 반환.
        """

        if not self.items:
            return None

        if self.current_item_index >= len(self.items):
            return None

        return self.items[
            self.current_item_index
        ]

    def tray_arrived(self):
        """
        레일이 현재 Tray 위치에 도착했을 때.
        """

        self.state = "PICKING"

        return self.get_status()

    def start_vision_check(self):
        """
        피킹 후 카메라 검증 단계 진입.
        """

        self.state = "VISION_CHECK"

        return self.get_status()

    def vision_passed(self):
        """
        현재 품목의 Vision 검증이 성공했을 때.
        """

        self.state = "ITEM_COMPLETE"

        return self.get_status()

    def next_item(self):
        """
        현재 품목 완료 후 다음 품목으로 이동.
        """

        next_index = (
            self.current_item_index + 1
        )

        if next_index < len(self.items):

            self.current_item_index = next_index
            self.state = "TRAY_MOVING"

        else:

            self.state = "FINAL_VERIFICATION"

        return self.get_status()

    def final_verification_passed(self):
        """
        모든 품목 피킹 후 최종 검증 완료.
        """

        self.state = "TRAY_RETURN"

        return self.get_status()

    def tray_return_complete(self):
        """
        Tray 복귀 완료.
        """

        self.state = "RELOCATION"

        return self.get_status()

    def relocation_complete(self):
        """
        재배치 완료.
        """

        self.state = "INVENTORY_UPDATE"

        return self.get_status()

    def inventory_complete(self):
        """
        재고 차감 완료.
        """

        self.state = "HISTORY_SAVE"

        return self.get_status()

    def history_complete(self):
        """
        작업 이력 저장 완료.
        """

        self.state = "WORK_COMPLETE"

        return self.get_status()

    def pause(self):
        """
        현재 Workflow 일시정지.
        """
        return {
            **self.get_status(),
            "paused": True
        }

    def get_status(self):
        """
        UI가 사용할 현재 Workflow 상태.
        """

        return {
            "state": self.state,

            "current_item_index":
                self.current_item_index,

            "total_items":
                len(self.items),

            "current_item":
                self.get_current_item()
        }