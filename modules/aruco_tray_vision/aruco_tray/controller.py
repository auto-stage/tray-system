from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .geometry import compute_grip_target_camera
from .models import GripTarget3D, MarkerObservation, TrayDefinition
from .safety import PoseCheckResult, check_pose_limits
from .transforms import transform_point_4x4


@dataclass(frozen=True)
class VisionDecision:
    tray: TrayDefinition
    target_camera: GripTarget3D
    pose_check: PoseCheckResult
    target_stage_xyz_mm: np.ndarray | None


def build_vision_decision(
    observation: MarkerObservation,
    trays: dict[int, TrayDefinition],
    pose_limits: dict,
    camera_to_stage_4x4: np.ndarray | None = None,
) -> VisionDecision:
    if observation.marker_id not in trays:
        raise KeyError(f"등록되지 않은 마커 ID: {observation.marker_id}")
    if observation.pose6d is None:
        raise RuntimeError("카메라 캘리브레이션이 필요합니다: 6DoF pose 없음")

    tray = trays[observation.marker_id]
    target_cam = compute_grip_target_camera(observation.pose6d, tray)
    check = check_pose_limits(
        observation.pose6d,
        float(pose_limits["roll_abs_max_deg"]),
        float(pose_limits["pitch_abs_max_deg"]),
        float(pose_limits["yaw_abs_max_deg"]),
    )
    target_stage = None
    if camera_to_stage_4x4 is not None:
        target_stage = transform_point_4x4(camera_to_stage_4x4, target_cam.position_mm)

    return VisionDecision(tray, target_cam, check, target_stage)
