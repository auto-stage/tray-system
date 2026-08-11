from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np
import yaml


class ChessboardCalibrator:
    def __init__(self, inner_cols: int = 9, inner_rows: int = 6, square_mm: float = 25.0):
        self.inner_cols = inner_cols
        self.inner_rows = inner_rows
        self.square_mm = square_mm
        self.pattern_size = (inner_cols, inner_rows)
        self.obj_template = np.zeros((inner_cols * inner_rows, 3), np.float32)
        self.obj_template[:, :2] = np.mgrid[0:inner_cols, 0:inner_rows].T.reshape(-1, 2)
        self.obj_template *= square_mm
        self.object_points: list[np.ndarray] = []
        self.image_points: list[np.ndarray] = []
        self.image_size: tuple[int, int] | None = None

    @property
    def sample_count(self) -> int:
        return len(self.object_points)

    def detect(self, frame: np.ndarray):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        found, corners = cv2.findChessboardCorners(gray, self.pattern_size, flags)
        if not found:
            return False, None
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
        refined = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        return True, refined

    def add_sample(self, frame: np.ndarray) -> bool:
        found, refined = self.detect(frame)
        if not found or refined is None:
            return False
        self.image_size = (frame.shape[1], frame.shape[0])
        self.object_points.append(self.obj_template.copy())
        self.image_points.append(refined.copy())
        return True

    def clear(self) -> None:
        self.object_points.clear()
        self.image_points.clear()
        self.image_size = None

    def calibrate_and_save(self, path: str | Path, camera_name: str, camera_index: int) -> float:
        if self.sample_count < 10:
            raise RuntimeError("유효한 체커보드 샘플이 최소 10장 필요합니다.")
        if self.image_size is None:
            raise RuntimeError("이미지 크기를 알 수 없습니다.")
        rms, K, D, _rvecs, _tvecs = cv2.calibrateCamera(
            self.object_points, self.image_points, self.image_size, None, None
        )
        data = {
            "calibrated": True,
            "camera_name": camera_name,
            "camera_index_hint": int(camera_index),
            "image_width": int(self.image_size[0]),
            "image_height": int(self.image_size[1]),
            "rms_reprojection_error": float(rms),
            "camera_matrix": K.tolist(),
            "distortion_coefficients": D.reshape(-1).tolist(),
        }
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        return float(rms)
