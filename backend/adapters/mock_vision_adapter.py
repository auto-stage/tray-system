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
        self,
        expected_tray_id: int | None = None,
    ):
        tray_id = (
            int(expected_tray_id)
            if expected_tray_id is not None
            else 1
        )

        return {
            "success": True,
            "mock": True,
            "detected": True,
            "tray_id": tray_id,
            "tray_label": f"TRAY {tray_id:02d}",
            "aruco_id": tray_id,
            "matched_expected_tray": True,
        }


    def get_camera_status(
        self
    ):
        return {
            "connected": False,
            "mock": True,
            "mode": "mock",
        }