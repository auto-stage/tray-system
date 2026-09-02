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
        self.sample_image_sizes: list[tuple[int, int]] = []
        self.image_size: tuple[int, int] | None = None
        self.last_error: str | None = None

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
        frame_size = (
            int(frame.shape[1]),
            int(frame.shape[0]),
        )
        if (
            self.image_size is not None
            and frame_size != self.image_size
        ):
            self.last_error = (
                "서로 다른 해상도의 캘리브레이션 샘플은 "
                "혼합할 수 없습니다: "
                f"기존={self.image_size[0]}x{self.image_size[1]}, "
                f"현재={frame_size[0]}x{frame_size[1]}"
            )
            return False

        found, refined = self.detect(frame)
        if not found or refined is None:
            self.last_error = "체커보드를 검출하지 못했습니다."
            return False
        self.image_size = frame_size
        self.object_points.append(self.obj_template.copy())
        self.image_points.append(refined.copy())
        self.sample_image_sizes.append(frame_size)
        self.last_error = None
        return True

    def clear(self) -> None:
        self.object_points.clear()
        self.image_points.clear()
        self.sample_image_sizes.clear()
        self.image_size = None
        self.last_error = None

    def calibrate_and_save(self, path: str | Path, camera_name: str, camera_index: int) -> float:
        if self.sample_count < 10:
            raise RuntimeError("유효한 체커보드 샘플이 최소 10장 필요합니다.")
        if self.image_size is None:
            raise RuntimeError("이미지 크기를 알 수 없습니다.")
        if (
            len(set(self.sample_image_sizes)) != 1
            or self.sample_image_sizes[0]
            != self.image_size
        ):
            raise RuntimeError(
                "서로 다른 해상도의 캘리브레이션 샘플은 "
                "혼합할 수 없습니다. 샘플을 초기화한 뒤 "
                "동일한 해상도로 다시 수집하세요."
            )
        rms, K, D, _rvecs, _tvecs = cv2.calibrateCamera(
            self.object_points, self.image_points, self.image_size, None, None
        )
        p = Path(path)
        data = {}
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
            if not isinstance(existing, dict):
                raise RuntimeError(
                    f"기존 camera profile 형식이 올바르지 않습니다: {p}"
                )
            data.update(existing)

        data.setdefault(
            "camera_name",
            camera_name,
        )
        data.setdefault(
            "camera_index_hint",
            int(camera_index),
        )
        data.update({
            "calibrated": True,
            "image_width": int(self.image_size[0]),
            "image_height": int(self.image_size[1]),
            "rms_reprojection_error": float(rms),
            "camera_matrix": K.tolist(),
            "distortion_coefficients": D.reshape(-1).tolist(),
        })
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        return float(rms)
