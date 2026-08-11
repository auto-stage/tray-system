from __future__ import annotations

import math
import numpy as np

from .models import GripTarget3D, Pose6D, TrayDefinition


def rotation_matrix_to_euler_zyx_deg(R: np.ndarray) -> tuple[float, float, float]:
    """Return roll(X), pitch(Y), yaw(Z) in degrees using ZYX decomposition.

    R is marker-frame -> camera-frame rotation matrix.
    OpenCV camera frame convention is x:right, y:down, z:forward.
    """
    R = np.asarray(R, dtype=float).reshape(3, 3)
    sy = math.sqrt(R[0, 0] ** 2 + R[1, 0] ** 2)
    singular = sy < 1e-8

    if not singular:
        roll = math.atan2(R[2, 1], R[2, 2])
        pitch = math.atan2(-R[2, 0], sy)
        yaw = math.atan2(R[1, 0], R[0, 0])
    else:
        roll = math.atan2(-R[1, 2], R[1, 1])
        pitch = math.atan2(-R[2, 0], sy)
        yaw = 0.0

    return tuple(map(math.degrees, (roll, pitch, yaw)))


def compute_grip_target_camera(pose: Pose6D, tray: TrayDefinition) -> GripTarget3D:
    """Rigid-body transform: P_grip_cam = P_marker_cam + R_marker_to_cam * offset_marker."""
    offset_cam = pose.rotation_matrix @ tray.grip_offset_marker_mm.reshape(3)
    position = pose.translation_mm.reshape(3) + offset_cam
    return GripTarget3D(
        marker_id=pose.marker_id,
        tray_code=tray.tray_code,
        position_mm=position,
        rotation_matrix=pose.rotation_matrix.copy(),
        roll_deg=pose.roll_deg,
        pitch_deg=pose.pitch_deg,
        yaw_deg=pose.yaw_deg,
    )
