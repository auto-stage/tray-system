from __future__ import annotations

import argparse


def run_self_test() -> None:
    import numpy as np
    from aruco_tray.geometry import compute_grip_target_camera
    from aruco_tray.models import Pose6D, TrayDefinition

    tray = TrayDefinition(
        marker_id=1,
        tray_code="A-01",
        marker_size_mm=50.0,
        grip_offset_marker_mm=np.array([80.0, 30.0, 10.0], dtype=float),
    )
    pose = Pose6D(
        marker_id=1,
        translation_mm=np.array([100.0, 200.0, 600.0], dtype=float),
        rotation_matrix=np.eye(3, dtype=float),
        roll_deg=0.0,
        pitch_deg=0.0,
        yaw_deg=0.0,
        image_yaw_deg=0.0,
    )
    target = compute_grip_target_camera(pose, tray)
    assert np.allclose(target.position_mm, [180.0, 230.0, 610.0])
    print("[SELF-TEST] 6DoF pose -> 3D grip target: PASS")
    print("[SELF-TEST] GUI 실행: python main.py")


def main() -> None:
    parser = argparse.ArgumentParser(description="ArUco tray 6DoF system")
    parser.add_argument("--self-test", action="store_true", help="GUI 없이 핵심 수학 로직 검사")
    args = parser.parse_args()
    if args.self_test:
        run_self_test()
        return

    from gui_app import run_gui
    run_gui()


if __name__ == "__main__":
    main()
