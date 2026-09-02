from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class TrayDefinition:
    # marker_id is the only fixed system identity.
    # All human-facing names and tray geometry remain configurable.
    marker_id: int
    tray_code: str
    marker_size_mm: float
    grip_offset_marker_mm: np.ndarray
    display_name: str = ""
    enabled: bool = True
    geometry_calibrated: bool = False


@dataclass(frozen=True)
class Pose6D:
    marker_id: int
    translation_mm: np.ndarray       # marker origin expressed in camera frame
    rotation_matrix: np.ndarray      # marker frame -> camera frame
    roll_deg: float                  # ZYX Euler decomposition, X rotation
    pitch_deg: float                 # ZYX Euler decomposition, Y rotation
    yaw_deg: float                   # ZYX Euler decomposition, Z rotation
    image_yaw_deg: float             # simple 2D preview angle; usable before calibration


@dataclass(frozen=True)
class MarkerObservation:
    marker_id: int
    center_u_px: float
    center_v_px: float
    corners_px: np.ndarray
    image_yaw_deg: float
    pose6d: Pose6D | None = None
    rvec: np.ndarray | None = None
    tvec: np.ndarray | None = None

    @property
    def has_metric_pose(self) -> bool:
        return self.pose6d is not None


@dataclass(frozen=True)
class GripTarget3D:
    marker_id: int
    tray_code: str
    position_mm: np.ndarray          # grip point expressed in camera frame
    rotation_matrix: np.ndarray      # desired tray/grip orientation reference
    roll_deg: float
    pitch_deg: float
    yaw_deg: float