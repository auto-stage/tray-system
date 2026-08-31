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

from aruco_tray.calibration import ChessboardCalibrator  # noqa: E402
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
    - Use ArUco marker ID as the canonical tray identity.
    - Convert camera-frame grip target into the moving X/Z carriage frame.
    - Expose X/Z correction deltas only when carriage alignment and tray
      geometry are explicitly calibrated.

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
            if tray.enabled
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

        # 기존 calibration.py의 ChessboardCalibrator를 Backend에서도 재사용.
        # 기본값은 기존 코드와 동일한 9x6 inner corners / 25 mm square.
        self._calibrator = ChessboardCalibrator(
            inner_cols=9,
            inner_rows=6,
            square_mm=25.0,
        )
        self._calibration_lock = threading.Lock()

        integration = self.system.get("integration", {})
        self.camera_mount_mode = str(
            integration.get(
                "camera_mount_mode",
                "MOVING_XZ_CARRIAGE",
            )
        ).strip().upper()

    @staticmethod
    def _resolve_path(path: str | Path) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return (REPO_ROOT / candidate).resolve()

    def _backend_tray_id(
        self,
        marker_id: int,
    ) -> int | None:
        """
        ArUco marker ID is the canonical/fixed tray identity.

        Human-facing names such as tray_code/display_name are configurable
        and must never affect physical mapping or Vision identity.
        """
        tray = self.trays.get(
            int(marker_id)
        )
        if (
            tray is None
            or not tray.enabled
        ):
            return None
        return int(marker_id)

    @staticmethod
    def _xyz_dict(
        values: np.ndarray,
    ) -> dict[str, float]:
        x, y, z = np.asarray(
            values,
            dtype=float,
        ).reshape(3)
        return {
            "x": float(x),
            "y": float(y),
            "z": float(z),
        }

    def _moving_alignment_cfg(
        self,
    ) -> dict[str, Any]:
        return (
            self.system
            .get("integration", {})
            .get(
                "moving_camera_alignment",
                {},
            )
        )

    def _moving_alignment_configured(
        self,
    ) -> bool:
        cfg = self._moving_alignment_cfg()
        raw_matrix = cfg.get(
            "camera_to_carriage_4x4"
        )
        reference = cfg.get(
            "gripper_reference_carriage_mm"
        )

        if (
            raw_matrix is None
            or not isinstance(
                reference,
                dict,
            )
        ):
            return False

        try:
            matrix_ok = (
                np.asarray(
                    raw_matrix,
                    dtype=float,
                ).size
                == 16
            )
            reference_ok = all(
                key in reference
                for key in (
                    "x",
                    "y",
                    "z",
                )
            )
            return bool(
                matrix_ok
                and reference_ok
            )
        except (
            TypeError,
            ValueError,
        ):
            return False

    def _moving_alignment_calibrated(
        self,
    ) -> bool:
        cfg = self._moving_alignment_cfg()
        return bool(
            cfg.get(
                "calibrated",
                False,
            )
        ) and self._moving_alignment_configured()

    def _camera_to_carriage_matrix(
        self,
    ) -> np.ndarray | None:
        if not self._moving_alignment_calibrated():
            return None

        matrix = np.asarray(
            self._moving_alignment_cfg()[
                "camera_to_carriage_4x4"
            ],
            dtype=float,
        )

        if matrix.size != 16:
            raise ValueError(
                "moving_camera_alignment."
                "camera_to_carriage_4x4 must contain "
                "exactly 16 values"
            )

        return matrix.reshape(
            4,
            4,
        )

    def _gripper_reference_carriage_mm(
        self,
    ) -> np.ndarray | None:
        if not self._moving_alignment_configured():
            return None

        raw = (
            self._moving_alignment_cfg()
            .get(
                "gripper_reference_carriage_mm",
                {},
            )
        )

        return np.array(
            [
                float(raw["x"]),
                float(raw["y"]),
                float(raw["z"]),
            ],
            dtype=float,
        )

    def _alignment_tolerance_mm(
        self,
    ) -> dict[str, float | None]:
        raw = (
            self._moving_alignment_cfg()
            .get(
                "stage_alignment_tolerance_mm",
                {},
            )
        )

        def optional_float(
            value: Any,
        ) -> float | None:
            if value is None:
                return None
            return float(value)

        return {
            "x": optional_float(
                raw.get("x")
            ),
            "z": optional_float(
                raw.get("z")
            ),
        }

    @staticmethod
    def _transform_point_4x4(
        matrix_4x4: np.ndarray,
        point_xyz_mm: np.ndarray,
    ) -> np.ndarray:
        point = np.asarray(
            point_xyz_mm,
            dtype=float,
        ).reshape(3)

        homogeneous = np.concatenate(
            [
                point,
                np.array(
                    [1.0],
                    dtype=float,
                ),
            ]
        )

        transformed = (
            np.asarray(
                matrix_4x4,
                dtype=float,
            ).reshape(
                4,
                4,
            )
            @ homogeneous
        )

        return transformed[:3]

    def _compute_moving_alignment(
        self,
        target_camera_mm: np.ndarray,
    ) -> dict[str, Any]:
        """
        Convert the grip target into the moving carriage frame.

        Carriage frame convention:
          +X = physical Stage X positive direction
          +Z = physical Stage Z positive direction
          +Y = gripper approach/depth direction

        The camera is rigidly mounted to the X/Z moving carriage, therefore
        there is no single fixed Camera->Stage absolute transform. Instead,
        the fixed Camera->Carriage transform produces a RELATIVE X/Z
        correction. The current Stage X/Z position can later be added by the
        Stage coordinator to obtain an absolute motion target.
        """
        matrix = (
            self._camera_to_carriage_matrix()
        )
        reference = (
            self._gripper_reference_carriage_mm()
        )

        if (
            matrix is None
            or reference is None
        ):
            return {
                "target_carriage_mm": None,
                "gripper_reference_carriage_mm": None,
                "alignment_error_carriage_mm": None,
                "stage_correction_delta_mm": None,
                "depth_error_mm": None,
                "alignment_ok": None,
            }

        target_carriage = (
            self._transform_point_4x4(
                matrix,
                target_camera_mm,
            )
        )
        error = (
            target_carriage
            - reference
        )

        correction = {
            "x": float(error[0]),
            "z": float(error[2]),
        }

        tolerance = (
            self._alignment_tolerance_mm()
        )

        if (
            tolerance["x"] is None
            or tolerance["z"] is None
        ):
            alignment_ok = None
        else:
            alignment_ok = bool(
                abs(correction["x"])
                <= tolerance["x"]
                and abs(correction["z"])
                <= tolerance["z"]
            )

        return {
            "target_carriage_mm": self._xyz_dict(
                target_carriage
            ),
            "gripper_reference_carriage_mm": self._xyz_dict(
                reference
            ),
            "alignment_error_carriage_mm": self._xyz_dict(
                error
            ),
            "stage_correction_delta_mm": correction,
            "depth_error_mm": float(
                error[1]
            ),
            "alignment_ok": alignment_ok,
        }

    def _gripper_depth_status(
        self,
    ) -> dict[str, Any]:
        cfg = (
            self.system
            .get("integration", {})
            .get(
                "gripper_depth_axis",
                {},
            )
        )

        mode = str(
            cfg.get(
                "mode",
                "UNDECIDED",
            )
        ).strip().upper()

        return {
            "mode": mode,
            "calibrated": bool(
                cfg.get(
                    "calibrated",
                    False,
                )
            ),
            "axis_in_carriage": cfg.get(
                "axis_in_carriage",
                "y",
            ),
            "motion_command_available": (
                mode
                not in {
                    "",
                    "UNDECIDED",
                    "NONE",
                }
                and bool(
                    cfg.get(
                        "calibrated",
                        False,
                    )
                )
            ),
        }

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

    def get_correction_loop_config(self) -> dict[str, Any]:
        integration = self.system.get("integration", {})
        loop = integration.get("correction_loop", {})
        alignment = integration.get("moving_camera_alignment", {})

        tolerance = alignment.get("stage_alignment_tolerance_mm", {})
        max_single = alignment.get("max_single_correction_mm", {})

        def optional_float(value: Any) -> float | None:
            if value is None:
                return None
            return float(value)

        return {
            "enabled": bool(loop.get("enabled", False)),
            "max_iterations": max(1, int(loop.get("max_iterations", 2))),
            "reobserve_after_move": bool(loop.get("reobserve_after_move", True)),
            "tolerance_mm": {
                "x": optional_float(tolerance.get("x")),
                "z": optional_float(tolerance.get("z")),
            },
            "max_single_correction_mm": {
                "x": optional_float(max_single.get("x")),
                "z": optional_float(max_single.get("z")),
            },
        }

    def list_camera_profiles(self) -> dict[str, Any]:
        profiles: list[dict[str, Any]] = []

        for path in sorted(
            self.config_dir.glob("camera_*.yaml")
        ):
            try:
                cfg = load_yaml(path)
            except Exception as error:
                profiles.append(
                    {
                        "file": path.name,
                        "error": str(error),
                    }
                )
                continue

            profiles.append(
                {
                    "file": path.name,
                    "camera_name": str(
                        cfg.get(
                            "camera_name",
                            path.stem,
                        )
                    ),
                    "camera_index_hint": int(
                        cfg.get(
                            "camera_index_hint",
                            0,
                        )
                    ),
                    "calibrated": bool(
                        cfg.get(
                            "calibrated",
                            False,
                        )
                    ),
                    "image_width": cfg.get(
                        "image_width"
                    ),
                    "image_height": cfg.get(
                        "image_height"
                    ),
                    "rms_reprojection_error": cfg.get(
                        "rms_reprojection_error"
                    ),
                    "selected": (
                        path.resolve()
                        == self.camera_profile.resolve()
                    ),
                }
            )

        return {
            "success": True,
            "camera_index": self.camera_index,
            "camera_profile": self.camera_profile.name,
            "profiles": profiles,
        }

    def select_camera(
        self,
        profile_name: str,
        camera_index: int | None = None,
    ) -> dict[str, Any]:
        # 파일명만 허용하여 config 디렉터리 밖 접근을 막는다.
        safe_name = Path(
            str(profile_name)
        ).name

        if (
            safe_name != str(profile_name)
            or not safe_name.startswith("camera_")
            or not safe_name.endswith(".yaml")
        ):
            raise ValueError(
                "camera_*.yaml 형식의 프로파일만 선택할 수 있습니다."
            )

        profile_path = (
            self.config_dir
            / safe_name
        ).resolve()

        if not profile_path.is_file():
            raise FileNotFoundError(
                f"카메라 프로파일을 찾을 수 없습니다: {safe_name}"
            )

        cfg = load_yaml(profile_path)

        next_index = (
            int(camera_index)
            if camera_index is not None
            else int(
                cfg.get(
                    "camera_index_hint",
                    0,
                )
            )
        )

        # 스트림과 ArUco가 같은 VideoCapture를 공유하므로
        # 소스 변경 시 기존 카메라를 먼저 안전하게 닫는다.
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
                self._camera = None

            self.camera_index = next_index
            self.camera_profile = profile_path
            self._last_error = None

        self.vision.set_camera_profile(
            self.camera_profile
        )

        with self._calibration_lock:
            self._calibrator.clear()

        return {
            "success": True,
            "message": "카메라 설정을 적용했습니다.",
            "camera_index": self.camera_index,
            "camera_profile": self.camera_profile.name,
            "camera_calibrated": bool(
                self.vision.calibrated
            ),
        }

    def get_calibration_status(
        self,
    ) -> dict[str, Any]:
        try:
            cfg = load_yaml(
                self.camera_profile
            )
        except Exception:
            cfg = {}

        with self._calibration_lock:
            sample_count = (
                self._calibrator.sample_count
            )
            inner_cols = (
                self._calibrator.inner_cols
            )
            inner_rows = (
                self._calibrator.inner_rows
            )
            square_mm = (
                self._calibrator.square_mm
            )

        return {
            "success": True,
            "camera_profile": self.camera_profile.name,
            "camera_index": self.camera_index,
            "calibrated": bool(
                self.vision.calibrated
            ),
            "sample_count": sample_count,
            "minimum_samples": 10,
            "pattern": {
                "inner_cols": inner_cols,
                "inner_rows": inner_rows,
                "square_mm": float(
                    square_mm
                ),
            },
            "image_width": cfg.get(
                "image_width"
            ),
            "image_height": cfg.get(
                "image_height"
            ),
            "rms_reprojection_error": cfg.get(
                "rms_reprojection_error"
            ),
        }

    def add_calibration_sample(
        self,
    ) -> dict[str, Any]:
        frame = self._read_frame()

        if frame is None:
            return {
                "success": False,
                "found": False,
                "sample_count": (
                    self._calibrator.sample_count
                ),
                "message": (
                    self._last_error
                    or "카메라 프레임을 읽지 못했습니다."
                ),
            }

        with self._calibration_lock:
            found = (
                self._calibrator.add_sample(
                    frame
                )
            )
            sample_count = (
                self._calibrator.sample_count
            )

        return {
            "success": bool(found),
            "found": bool(found),
            "sample_count": sample_count,
            "minimum_samples": 10,
            "message": (
                "체커보드 샘플을 추가했습니다."
                if found
                else (
                    "9x6 체커보드를 검출하지 못했습니다. "
                    "각도/거리/조명을 바꿔 다시 시도하세요."
                )
            ),
        }

    def clear_calibration_samples(
        self,
    ) -> dict[str, Any]:
        with self._calibration_lock:
            self._calibrator.clear()

        return {
            "success": True,
            "sample_count": 0,
            "message": "캘리브레이션 샘플을 초기화했습니다.",
        }

    def run_intrinsic_calibration(
        self,
    ) -> dict[str, Any]:
        try:
            current_cfg = load_yaml(
                self.camera_profile
            )
        except Exception:
            current_cfg = {}

        camera_name = str(
            current_cfg.get(
                "camera_name",
                self.camera_profile.stem,
            )
        )

        with self._calibration_lock:
            rms = (
                self._calibrator.calibrate_and_save(
                    path=self.camera_profile,
                    camera_name=camera_name,
                    camera_index=self.camera_index,
                )
            )

        # 저장 직후 기존 ArucoVision 객체가 새 Intrinsic을 다시 읽는다.
        self.vision.set_camera_profile(
            self.camera_profile
        )

        return {
            "success": True,
            "message": "카메라 Intrinsic 캘리브레이션을 저장했습니다.",
            "camera_profile": self.camera_profile.name,
            "camera_index": self.camera_index,
            "camera_calibrated": bool(
                self.vision.calibrated
            ),
            "rms_reprojection_error": float(
                rms
            ),
            "sample_count": (
                self._calibrator.sample_count
            ),
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
                "camera_mount_mode": self.camera_mount_mode,
                "moving_camera_alignment_configured":
                    self._moving_alignment_configured(),
                "moving_camera_alignment_calibrated":
                    self._moving_alignment_calibrated(),
                # Legacy aliases kept temporarily for existing UI compatibility.
                "camera_to_stage_configured":
                    self._moving_alignment_configured(),
                "camera_to_stage_calibrated":
                    self._moving_alignment_calibrated(),
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
            "tray_label": tray.display_name or tray.tray_code,
            "tray_code": tray.tray_code,
            "tray_display_name": tray.display_name or tray.tray_code,
            "tray_geometry_calibrated": bool(
                tray.geometry_calibrated
            ),
            "center_px": {
                "u": float(observation.center_u_px),
                "v": float(observation.center_v_px),
            },
            "image_yaw_deg": float(observation.image_yaw_deg),
            "camera_mount_mode": self.camera_mount_mode,
            "moving_camera_alignment_configured":
                self._moving_alignment_configured(),
            "moving_camera_alignment_calibrated":
                self._moving_alignment_calibrated(),
            # Legacy aliases kept temporarily for existing UI compatibility.
            "camera_to_stage_configured":
                self._moving_alignment_configured(),
            "camera_to_stage_calibrated":
                self._moving_alignment_calibrated(),
            "gripper_depth_axis": self._gripper_depth_status(),
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
            camera_to_stage_4x4=None,
        )

        pose = observation.pose6d
        alignment = self._compute_moving_alignment(
            decision.target_camera.position_mm
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
                "grip_target_stage_mm": None,
                "target_carriage_mm":
                    alignment["target_carriage_mm"],
                "gripper_reference_carriage_mm":
                    alignment["gripper_reference_carriage_mm"],
                "alignment_error_carriage_mm":
                    alignment["alignment_error_carriage_mm"],
                "stage_correction_delta_mm":
                    alignment["stage_correction_delta_mm"],
                "depth_error_mm":
                    alignment["depth_error_mm"],
                "alignment_ok":
                    alignment["alignment_ok"],
                "ready_for_stage_correction": bool(
                    decision.pose_check.ok
                    and self.vision.calibrated
                    and tray.geometry_calibrated
                    and self._moving_alignment_calibrated()
                    and alignment["stage_correction_delta_mm"] is not None
                ),
            }
        )

        if not decision.pose_check.ok:
            base_result["message"] = (
                "ArUco/6DoF 계산은 성공했지만 자세 허용범위를 벗어났습니다."
            )
        elif not tray.geometry_calibrated:
            base_result["message"] = (
                "Vision 계산은 정상입니다. 해당 Tray의 marker_size / "
                "marker_to_grip 실측이 확정되지 않아 X/Z 자동 보정은 차단됩니다."
            )
        elif not self._moving_alignment_calibrated():
            base_result["message"] = (
                "Vision 계산은 정상입니다. 이동부 Camera->Carriage "
                "캘리브레이션이 없어 X/Z 자동 보정은 차단됩니다."
            )
        else:
            base_result["message"] = (
                "이동부 카메라 기준 X/Z 보정량 계산이 준비되었습니다. "
                "깊이 방향은 전후진 액추에이터 선정 전까지 모니터링만 합니다."
            )

        return base_result

    def get_jpeg_frame(
        self,
        jpeg_quality: int = 85,
        annotate: bool = True,
    ) -> bytes | None:
        """
        Return one JPEG-encoded frame from the same camera used by ArUco.

        The camera is owned by this adapter so UI preview and ArUco detection
        do not open competing VideoCapture instances.
        """
        frame = self._read_frame()

        if frame is None:
            return None

        if annotate:
            observations = self.vision.detect(
                frame
            )
            frame = self.vision.draw(
                frame,
                observations,
            )

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
        annotate: bool = True,
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
                jpeg_quality=jpeg_quality,
                annotate=annotate,
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

        try:
            profile_cfg = load_yaml(
                self.camera_profile
            )
        except Exception:
            profile_cfg = {}

        return {
            "connected": bool(connected),
            "mock": False,
            "mode": "aruco",
            "camera_index": self.camera_index,
            "camera_profile": str(self.camera_profile),
            "camera_profile_name": self.camera_profile.name,
            "camera_calibrated": bool(self.vision.calibrated),
            "image_width": profile_cfg.get("image_width"),
            "image_height": profile_cfg.get("image_height"),
            "rms_reprojection_error": profile_cfg.get(
                "rms_reprojection_error"
            ),
            "camera_mount_mode": self.camera_mount_mode,
            "moving_camera_alignment_configured":
                self._moving_alignment_configured(),
            "moving_camera_alignment_calibrated":
                self._moving_alignment_calibrated(),
            "gripper_depth_axis": self._gripper_depth_status(),
            # Legacy aliases kept temporarily for existing UI compatibility.
            "camera_to_stage_configured":
                self._moving_alignment_configured(),
            "camera_to_stage_calibrated":
                self._moving_alignment_calibrated(),
            "ready_for_stage_correction": bool(
                connected
                and self.vision.calibrated
                and self._moving_alignment_calibrated()
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