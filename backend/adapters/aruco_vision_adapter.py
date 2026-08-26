from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .vision_adapter import VisionAdapter


# Repository layout:
#   tray-system/
#     backend/adapters/aruco_vision_adapter.py
#     modules/aruco_tray_vision/...
REPO_ROOT = Path(__file__).resolve().parents[2]
ARUCO_MODULE_ROOT = REPO_ROOT / "modules" / "aruco_tray_vision"

# The existing ArUco code is not installed as a site-package.
# Add only its module root so the already-tested aruco_tray package can be reused.
if str(ARUCO_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(ARUCO_MODULE_ROOT))

from aruco_tray.config import load_trays, load_yaml  # noqa: E402
from aruco_tray.controller import build_vision_decision  # noqa: E402
from aruco_tray.vision import ArucoVision  # noqa: E402


_AXIS_INDEX = {
    "x": 0,
    "y": 1,
    "z": 2,
}


class ArucoVisionAdapter(VisionAdapter):
    """
    FastAPI backend adapter for the existing ArUco tray-vision module.

    Responsibilities
    ----------------
    - Open/read the physical camera.
    - Reuse existing ArucoVision detection and 6DoF pose estimation.
    - Reuse existing build_vision_decision() for grip target / pose safety.
    - Expose results in backend-friendly JSON.
    - Convert marker ID to backend TRAY 01~06 ID.
    - Expose Camera->Stage transformed target only when extrinsic calibration
      is explicitly marked calibrated=true.

    Non-responsibilities
    --------------------
    - Camera intrinsic calibration: keep using the existing GUI/calibration code.
    - STM32 motion: handled only by STM32StageAdapter.
    - Part counting / YOLO: not implemented by this adapter.
    """

    def __init__(
        self,
        camera_index: int | None = None,
        camera_profile: str | Path | None = None,
        config_dir: str | Path | None = None,
    ) -> None:
        self.config_dir = (
            self._resolve_path(config_dir)
            if config_dir is not None
            else ARUCO_MODULE_ROOT / "config"
        )

        self.trays_path = self.config_dir / "trays.yaml"
        self.system_path = self.config_dir / "system.yaml"

        if camera_profile is None:
            camera_profile = self.config_dir / "camera_external.yaml"
        self.camera_profile = self._resolve_path(camera_profile)

        self.trays = load_trays(self.trays_path)
        self.system = load_yaml(self.system_path)

        marker_sizes = {
            marker_id: tray.marker_size_mm
            for marker_id, tray in self.trays.items()
        }
        self.vision = ArucoVision(
            marker_sizes_mm=marker_sizes,
            camera_profile=self.camera_profile,
        )

        if camera_index is None:
            profile = load_yaml(self.camera_profile)
            camera_index = int(profile.get("camera_index_hint", 0))

        self.camera_index = int(camera_index)
        self._camera: cv2.VideoCapture | None = None
        self._camera_lock = threading.Lock()
        self._last_error: str | None = None

        integration = self.system.get("integration", {})
        self.stage_axis_mapping = integration.get(
            "stage_axis_mapping",
            {"axis1": "x", "axis2": "z"},
        )
        self.tray_id_mapping = self._load_tray_id_mapping(
            integration.get("tray_id_mapping")
        )

    @staticmethod
    def _resolve_path(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return (REPO_ROOT / candidate).resolve()

    def _load_tray_id_mapping(
        self,
        raw_mapping: dict[Any, Any] | None,
    ) -> dict[int, int]:
        if not raw_mapping:
            # Default: ArUco marker ID == backend tray ID.
            return {
                marker_id: marker_id
                for marker_id in self.trays
            }

        mapping: dict[int, int] = {}
        for marker_id, tray_id in raw_mapping.items():
            mapping[int(marker_id)] = int(tray_id)
        return mapping

    def _camera_to_stage_matrix(self) -> np.ndarray | None:
        integration = self.system.get("integration", {})
        cfg = integration.get("camera_to_stage", {})

        # Safety gate:
        # A placeholder matrix may exist before physical installation, but it
        # MUST NOT be used until calibrated=true is explicitly set.
        if not bool(cfg.get("calibrated", False)):
            return None

        raw_matrix = cfg.get("matrix_4x4")
        if raw_matrix is None:
            return None

        matrix = np.asarray(raw_matrix, dtype=float)
        if matrix.size != 16:
            raise ValueError(
                "camera_to_stage.matrix_4x4 must contain exactly 16 values"
            )
        return matrix.reshape(4, 4)

    def _extrinsic_configured(self) -> bool:
        cfg = (
            self.system
            .get("integration", {})
            .get("camera_to_stage", {})
        )
        raw_matrix = cfg.get("matrix_4x4")
        if raw_matrix is None:
            return False

        try:
            return np.asarray(raw_matrix, dtype=float).size == 16
        except (TypeError, ValueError):
            return False

    def _extrinsic_calibrated(self) -> bool:
        cfg = (
            self.system
            .get("integration", {})
            .get("camera_to_stage", {})
        )
        return bool(cfg.get("calibrated", False)) and self._extrinsic_configured()

    def _ensure_camera_locked(self) -> bool:
        if self._camera is not None and self._camera.isOpened():
            return True

        if self._camera is not None:
            self._camera.release()
            self._camera = None

        camera = cv2.VideoCapture(self.camera_index)
        if not camera.isOpened():
            camera.release()
            self._last_error = (
                f"Camera index {self.camera_index}를 열 수 없습니다."
            )
            return False

        self._camera = camera
        self._last_error = None
        return True

    def _read_frame(self) -> np.ndarray | None:
        with self._camera_lock:
            if not self._ensure_camera_locked():
                return None

            assert self._camera is not None
            ok, frame = self._camera.read()

            if not ok or frame is None:
                self._last_error = (
                    f"Camera index {self.camera_index}에서 "
                    "프레임을 읽지 못했습니다."
                )
                return None

            self._last_error = None
            return frame

    def _backend_tray_id(self, marker_id: int) -> int | None:
        return self.tray_id_mapping.get(int(marker_id))

    @staticmethod
    def _xyz_dict(values: np.ndarray) -> dict[str, float]:
        x, y, z = np.asarray(values, dtype=float).reshape(3)
        return {
            "x": float(x),
            "y": float(y),
            "z": float(z),
        }

    def _physical_stage_axes(
        self,
        target_stage_xyz_mm: np.ndarray | None,
    ) -> dict[str, Any] | None:
        if target_stage_xyz_mm is None:
            return None

        target = np.asarray(
            target_stage_xyz_mm,
            dtype=float,
        ).reshape(3)

        axis1_name = str(
            self.stage_axis_mapping.get("axis1", "x")
        ).lower()
        axis2_name = str(
            self.stage_axis_mapping.get("axis2", "z")
        ).lower()

        if axis1_name not in _AXIS_INDEX or axis2_name not in _AXIS_INDEX:
            raise ValueError(
                "stage_axis_mapping은 x/y/z 중 하나를 사용해야 합니다."
            )

        return {
            "axis1": {
                "source_axis": axis1_name,
                "target_mm": float(target[_AXIS_INDEX[axis1_name]]),
            },
            "axis2": {
                "source_axis": axis2_name,
                "target_mm": float(target[_AXIS_INDEX[axis2_name]]),
            },
        }

    def detect_part_count(
        self,
        part_no: str,
        expected_quantity: int,
    ):
        # This adapter is intentionally only for ArUco tray recognition.
        # Keep the existing /vision/count API alive without pretending that
        # ArUco performs object counting.
        return {
            "success": False,
            "mock": False,
            "supported": False,
            "part_no": part_no,
            "expected_quantity": expected_quantity,
            "message": (
                "ArucoVisionAdapter는 Tray ArUco 검출 전용입니다. "
                "부품 수량 검출은 별도 Vision Adapter가 필요합니다."
            ),
        }

    def detect_tray_aruco(
        self,
        expected_tray_id: int | None = None,
    ):
        frame = self._read_frame()

        if frame is None:
            return {
                "success": False,
                "mock": False,
                "detected": False,
                "camera_connected": False,
                "error_code": "CAMERA_FRAME_UNAVAILABLE",
                "message": self._last_error or "카메라 프레임이 없습니다.",
            }

        observations = self.vision.detect(frame)

        if not observations:
            return {
                "success": True,
                "mock": False,
                "detected": False,
                "camera_connected": True,
                "camera_calibrated": bool(self.vision.calibrated),
                "camera_to_stage_configured": self._extrinsic_configured(),
                "camera_to_stage_calibrated": self._extrinsic_calibrated(),
                "message": "ArUco 마커가 검출되지 않았습니다.",
            }

        registered = [
            obs
            for obs in observations
            if obs.marker_id in self.trays
            and self._backend_tray_id(obs.marker_id) is not None
        ]

        if not registered:
            return {
                "success": False,
                "mock": False,
                "detected": True,
                "camera_connected": True,
                "error_code": "UNREGISTERED_MARKER",
                "detected_aruco_ids": [
                    int(obs.marker_id)
                    for obs in observations
                ],
                "message": "등록된 Tray에 대응하는 ArUco 마커가 없습니다.",
            }

        if expected_tray_id is not None:
            candidates = [
                obs
                for obs in registered
                if self._backend_tray_id(obs.marker_id)
                == int(expected_tray_id)
            ]

            if not candidates:
                return {
                    "success": False,
                    "mock": False,
                    "detected": True,
                    "camera_connected": True,
                    "error_code": "TRAY_ID_MISMATCH",
                    "expected_tray_id": int(expected_tray_id),
                    "detected_tray_ids": [
                        self._backend_tray_id(obs.marker_id)
                        for obs in registered
                    ],
                    "detected_aruco_ids": [
                        int(obs.marker_id)
                        for obs in registered
                    ],
                    "message": (
                        "검출된 Tray가 Backend가 기대한 Tray와 다릅니다."
                    ),
                }

            observation = candidates[0]
        else:
            if len(registered) > 1:
                return {
                    "success": False,
                    "mock": False,
                    "detected": True,
                    "camera_connected": True,
                    "error_code": "AMBIGUOUS_MARKERS",
                    "detected_aruco_ids": [
                        int(obs.marker_id)
                        for obs in registered
                    ],
                    "message": (
                        "등록된 ArUco 마커가 여러 개 보입니다. "
                        "expected_tray_id를 지정해 주세요."
                    ),
                }

            observation = registered[0]

        marker_id = int(observation.marker_id)
        tray = self.trays[marker_id]
        backend_tray_id = self._backend_tray_id(marker_id)

        base_result: dict[str, Any] = {
            "success": True,
            "mock": False,
            "detected": True,
            "camera_connected": True,
            "camera_index": self.camera_index,
            "camera_calibrated": bool(self.vision.calibrated),
            "camera_profile": str(self.camera_profile),
            "aruco_id": marker_id,
            "tray_id": backend_tray_id,
            "tray_label": (
                f"TRAY {backend_tray_id:02d}"
                if backend_tray_id is not None
                else None
            ),
            "tray_code": tray.tray_code,
            "center_px": {
                "u": float(observation.center_u_px),
                "v": float(observation.center_v_px),
            },
            "image_yaw_deg": float(observation.image_yaw_deg),
            "camera_to_stage_configured": self._extrinsic_configured(),
            "camera_to_stage_calibrated": self._extrinsic_calibrated(),
        }

        if expected_tray_id is not None:
            base_result["expected_tray_id"] = int(expected_tray_id)
            base_result["matched_expected_tray"] = (
                backend_tray_id == int(expected_tray_id)
            )

        if observation.pose6d is None:
            base_result.update(
                {
                    "pose_valid": False,
                    "pose_ok": False,
                    "ready_for_stage_correction": False,
                    "message": (
                        "ArUco는 검출됐지만 카메라 Intrinsic "
                        "캘리브레이션이 없어 6DoF를 계산할 수 없습니다."
                    ),
                }
            )
            return base_result

        decision = build_vision_decision(
            observation=observation,
            trays=self.trays,
            pose_limits=self.system["pose_limits"],
            camera_to_stage_4x4=self._camera_to_stage_matrix(),
        )

        pose = observation.pose6d
        stage_axes = self._physical_stage_axes(
            decision.target_stage_xyz_mm
        )

        base_result.update(
            {
                "pose_valid": True,
                "marker_pose_camera_mm": self._xyz_dict(
                    pose.translation_mm
                ),
                "pose_rpy_deg": {
                    "roll": float(pose.roll_deg),
                    "pitch": float(pose.pitch_deg),
                    "yaw": float(pose.yaw_deg),
                },
                "grip_target_camera_mm": self._xyz_dict(
                    decision.target_camera.position_mm
                ),
                "pose_ok": bool(decision.pose_check.ok),
                "pose_reasons": list(decision.pose_check.reasons),
                "grip_target_stage_mm": (
                    self._xyz_dict(decision.target_stage_xyz_mm)
                    if decision.target_stage_xyz_mm is not None
                    else None
                ),
                "physical_stage_axes": stage_axes,
                "ready_for_stage_correction": bool(
                    decision.pose_check.ok
                    and self.vision.calibrated
                    and self._extrinsic_calibrated()
                    and stage_axes is not None
                ),
            }
        )

        if not decision.pose_check.ok:
            base_result["message"] = (
                "ArUco/6DoF 계산은 성공했지만 자세 허용범위를 벗어났습니다."
            )
        elif not self._extrinsic_calibrated():
            base_result["message"] = (
                "Vision 계산은 정상입니다. Camera->Stage Extrinsic이 "
                "미캘리브레이션 상태이므로 실제 Stage 보정은 차단됩니다."
            )
        else:
            base_result["message"] = (
                "Vision 계산 및 Camera->Stage 좌표변환이 정상입니다."
            )

        return base_result

    def get_jpeg_frame(
        self,
        jpeg_quality: int = 85,
    ) -> bytes | None:
        """
        Return one JPEG-encoded frame from the same camera used by ArUco.

        The camera is owned by this adapter so UI preview and ArUco detection
        do not open competing VideoCapture instances.
        """
        frame = self._read_frame()

        if frame is None:
            return None

        quality = max(
            40,
            min(
                int(jpeg_quality),
                95,
            ),
        )

        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                int(cv2.IMWRITE_JPEG_QUALITY),
                quality,
            ],
        )

        if not ok:
            self._last_error = "카메라 프레임 JPEG 인코딩에 실패했습니다."
            return None

        return encoded.tobytes()

    def iter_mjpeg(
        self,
        jpeg_quality: int = 80,
        max_fps: float = 20.0,
    ):
        """
        MJPEG stream generator for the FastAPI UI preview endpoint.
        """
        frame_interval = 1.0 / max(
            1.0,
            float(max_fps),
        )

        while True:
            started = time.monotonic()

            jpeg = self.get_jpeg_frame(
                jpeg_quality=jpeg_quality
            )

            if jpeg is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Cache-Control: no-cache\r\n"
                    b"\r\n"
                    + jpeg
                    + b"\r\n"
                )

            elapsed = (
                time.monotonic()
                - started
            )

            if elapsed < frame_interval:
                time.sleep(
                    frame_interval
                    - elapsed
                )

    def get_camera_status(self):
        with self._camera_lock:
            connected = self._ensure_camera_locked()

        return {
            "connected": bool(connected),
            "mock": False,
            "mode": "aruco",
            "camera_index": self.camera_index,
            "camera_profile": str(self.camera_profile),
            "camera_calibrated": bool(self.vision.calibrated),
            "camera_to_stage_configured": self._extrinsic_configured(),
            "camera_to_stage_calibrated": self._extrinsic_calibrated(),
            "ready_for_stage_correction": bool(
                connected
                and self.vision.calibrated
                and self._extrinsic_calibrated()
            ),
            "last_error": self._last_error,
        }

    def close(self) -> None:
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
                self._camera = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Never raise during interpreter shutdown.
            pass