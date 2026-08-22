from .vision_adapter import VisionAdapter


class MockVisionAdapter(VisionAdapter):

    def detect_part_count(
        self,
        part_no: str,
        expected_quantity: int
    ):
        return {
            "success": True,
            "mock": True,
            "part_no": part_no,
            "expected_quantity": expected_quantity,
            "detected_quantity": expected_quantity,
            "matched": True
        }


    def detect_tray_aruco(
        self
    ):
        return {
            "success": True,
            "mock": True,
            "detected": True,
            "tray_id": 1,
            "aruco_id": 1
        }


    def get_camera_status(
        self
    ):
        return {
            "connected": False,
            "mock": Trues
        }