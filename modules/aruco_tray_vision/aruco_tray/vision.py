from __future__ import annotations

from pathlib import Path
import math
import cv2
import numpy as np
import yaml

from .geometry import rotation_matrix_to_euler_zyx_deg
from .models import MarkerObservation, Pose6D


class ArucoVision:
    def __init__(self, marker_sizes_mm: dict[int, float], camera_profile: str | Path | None = None):
        self.marker_sizes_mm = marker_sizes_mm
        dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        self.detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())
        self.camera_profile = Path(camera_profile) if camera_profile else None
        self.camera_matrix: np.ndarray | None = None
        self.distortion: np.ndarray | None = None
        self.calibrated = False
        self.reload_calibration()

    def set_camera_profile(self, path: str | Path) -> None:
        self.camera_profile = Path(path)
        self.reload_calibration()

    def reload_calibration(self) -> None:
        self.calibrated = False
        self.camera_matrix = None
        self.distortion = None
        if self.camera_profile is None or not self.camera_profile.exists():
            return
        with self.camera_profile.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if not cfg.get("calibrated", False):
            return
        self.camera_matrix = np.asarray(cfg["camera_matrix"], dtype=np.float64).reshape(3, 3)
        self.distortion = np.asarray(cfg["distortion_coefficients"], dtype=np.float64).reshape(-1, 1)
        self.calibrated = True

    @staticmethod
    def _image_yaw_deg(corners: np.ndarray) -> float:
        c = corners.reshape(4, 2)
        dx = float(c[1, 0] - c[0, 0])
        dy = float(c[1, 1] - c[0, 1])
        return math.degrees(math.atan2(dy, dx))

    def detect(self, frame: np.ndarray) -> list[MarkerObservation]:
        corners, ids, _rejected = self.detector.detectMarkers(frame)
        if ids is None:
            return []

        result: list[MarkerObservation] = []
        for idx, marker_id_raw in enumerate(ids.flatten()):
            marker_id = int(marker_id_raw)
            c = np.asarray(corners[idx], dtype=np.float32).reshape(4, 2)
            center = c.mean(axis=0)
            image_yaw = self._image_yaw_deg(c)
            pose = None
            rvec_out = None
            tvec_out = None

            if self.calibrated and marker_id in self.marker_sizes_mm:
                size = float(self.marker_sizes_mm[marker_id])
                half = size / 2.0
                # Required order for SOLVEPNP_IPPE_SQUARE:
                # top-left, top-right, bottom-right, bottom-left in marker coordinates.
                object_points = np.array([
                    [-half, +half, 0.0],
                    [+half, +half, 0.0],
                    [+half, -half, 0.0],
                    [-half, -half, 0.0],
                ], dtype=np.float32)

                ok, rvec, tvec = cv2.solvePnP(
                    object_points,
                    c,
                    self.camera_matrix,
                    self.distortion,
                    flags=cv2.SOLVEPNP_IPPE_SQUARE,
                )
                if ok:
                    R, _ = cv2.Rodrigues(rvec)
                    roll, pitch, yaw = rotation_matrix_to_euler_zyx_deg(R)
                    rvec_out = rvec.reshape(3).astype(float)
                    tvec_out = tvec.reshape(3).astype(float)
                    pose = Pose6D(
                        marker_id=marker_id,
                        translation_mm=tvec_out.copy(),
                        rotation_matrix=R.astype(float),
                        roll_deg=float(roll),
                        pitch_deg=float(pitch),
                        yaw_deg=float(yaw),
                        image_yaw_deg=float(image_yaw),
                    )

            result.append(MarkerObservation(
                marker_id=marker_id,
                center_u_px=float(center[0]),
                center_v_px=float(center[1]),
                corners_px=c.copy(),
                image_yaw_deg=float(image_yaw),
                pose6d=pose,
                rvec=rvec_out,
                tvec=tvec_out,
            ))
        return result

    def draw(self, frame: np.ndarray, observations: list[MarkerObservation], axis_length_mm: float = 30.0) -> np.ndarray:
        out = frame.copy()
        for obs in observations:
            pts = obs.corners_px.astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(out, [pts], True, (0, 255, 0), 2)
            center = (int(obs.center_u_px), int(obs.center_v_px))
            cv2.circle(out, center, 4, (0, 0, 255), -1)
            x0, y0 = map(int, obs.corners_px[0])
            label = f"ID {obs.marker_id} imgYaw={obs.image_yaw_deg:+.1f}"
            if obs.pose6d is not None:
                p = obs.pose6d
                label += f" RPY=({p.roll_deg:+.1f},{p.pitch_deg:+.1f},{p.yaw_deg:+.1f})"
                if self.camera_matrix is not None and self.distortion is not None and obs.rvec is not None and obs.tvec is not None:
                    cv2.drawFrameAxes(
                        out,
                        self.camera_matrix,
                        self.distortion,
                        obs.rvec.reshape(3, 1),
                        obs.tvec.reshape(3, 1),
                        axis_length_mm,
                        2,
                    )
            cv2.putText(out, label, (x0, max(22, y0 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2, cv2.LINE_AA)
        return out
