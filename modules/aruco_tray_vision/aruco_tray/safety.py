from __future__ import annotations

from dataclasses import dataclass

from .models import Pose6D


@dataclass(frozen=True)
class PoseCheckResult:
    ok: bool
    reasons: tuple[str, ...]


def check_pose_limits(pose: Pose6D, roll_max_deg: float, pitch_max_deg: float, yaw_max_deg: float) -> PoseCheckResult:
    reasons: list[str] = []
    if abs(pose.roll_deg) > roll_max_deg:
        reasons.append(f"Roll {pose.roll_deg:+.2f}° > ±{roll_max_deg:.2f}°")
    if abs(pose.pitch_deg) > pitch_max_deg:
        reasons.append(f"Pitch {pose.pitch_deg:+.2f}° > ±{pitch_max_deg:.2f}°")
    if abs(pose.yaw_deg) > yaw_max_deg:
        reasons.append(f"Yaw {pose.yaw_deg:+.2f}° > ±{yaw_max_deg:.2f}°")
    return PoseCheckResult(ok=not reasons, reasons=tuple(reasons))
