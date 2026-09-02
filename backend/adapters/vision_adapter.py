class VisionAdapter:

    def detect_part_count(
        self,
        part_no: str,
        expected_quantity: int
    ):
        """
        피킹 트레이 안의 특정 품목 개수를 확인한다.

        나중에 YOLO 또는 객체검출 코드 연결.
        """
        raise NotImplementedError


    def detect_tray_aruco(
        self,
        expected_tray_id: int | None = None,
    ):
        """
        카메라에서 ArUco 마커를 읽어서 현재 Tray ID를 반환한다.

        expected_tray_id가 주어지면 검출된 Tray가 작업 대상과
        일치하는지도 함께 검증한다.
        """
        raise NotImplementedError


    def get_camera_status(
        self
    ):
        """
        카메라 연결 상태 반환.
        """
        raise NotImplementedError