from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any

import cv2
import numpy as np
import yaml

from .camera_capture_worker import LatestFrameCaptureWorker


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAMERA_PROFILE = (
    REPO_ROOT
    / "modules"
    / "aruco_tray_vision"
    / "config"
    / "camera_c270.yaml"
)


def _decode_fourcc(value: float) -> str:
    raw = int(value)
    if raw <= 0:
        return ""
    return "".join(
        chr((raw >> (8 * index)) & 0xFF)
        for index in range(4)
    )


class WorkOrderCameraAdapter:
    """Shared fixed-camera frame source for work-order OCR and inspection."""

    def __init__(
        self,
        camera_index: int | str | None = None,
        camera_device: str | None = None,
        camera_profile: str | Path | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        fourcc: str | None = None,
    ) -> None:
        profile_path_raw = (
            camera_profile
            if camera_profile is not None
            else DEFAULT_CAMERA_PROFILE
        )
        expanded_profile_path = Path(
            os.path.expandvars(
                os.path.expanduser(
                    str(profile_path_raw)
                )
            )
        )
        self.camera_profile = (
            expanded_profile_path
            if expanded_profile_path.is_absolute()
            else (
                REPO_ROOT
                / expanded_profile_path
            ).resolve()
        )
        self.camera_profile_error: str | None = None
        profile: dict[str, Any] = {}
        try:
            with self.camera_profile.open(
                "r",
                encoding="utf-8",
            ) as profile_file:
                loaded_profile = (
                    yaml.safe_load(profile_file)
                    or {}
                )
            if not isinstance(
                loaded_profile,
                dict,
            ):
                raise ValueError(
                    "camera profile root must be a mapping"
                )
            profile = loaded_profile
        except Exception as error:
            self.camera_profile_error = str(error)
            print(
                "[WORK ORDER CAMERA WARNING] Camera profile "
                f"load failed: {self.camera_profile}: {error}"
            )

        profile_capture = profile.get(
            "capture",
            {},
        )
        if not isinstance(profile_capture, dict):
            self.camera_profile_error = (
                "camera profile capture must be a mapping"
            )
            profile_capture = {}

        def record_profile_error(
            message: str,
        ) -> None:
            self.camera_profile_error = (
                f"{self.camera_profile_error}; {message}"
                if self.camera_profile_error
                else message
            )

        def profile_number(
            mapping: dict[str, Any],
            key: str,
            default: int | float,
            convert,
        ):
            try:
                return convert(
                    mapping.get(
                        key,
                        default,
                    )
                )
            except Exception as error:
                record_profile_error(
                    f"invalid profile {key}: {error}"
                )
                return convert(default)

        self.camera_name = str(
            profile.get(
                "camera_name",
                self.camera_profile.stem,
            )
        )
        self.calibrated = bool(
            profile.get(
                "calibrated",
                False,
            )
        )
        self.calibration_image_width = profile.get(
            "image_width"
        )
        self.calibration_image_height = profile.get(
            "image_height"
        )
        self.rms_reprojection_error = profile.get(
            "rms_reprojection_error"
        )
        try:
            camera_matrix_raw = profile.get(
                "camera_matrix"
            )
            distortion_raw = profile.get(
                "distortion_coefficients"
            )
            self.camera_matrix = (
                np.asarray(
                    camera_matrix_raw,
                    dtype=np.float64,
                ).reshape(3, 3)
                if camera_matrix_raw is not None
                else None
            )
            self.distortion_coefficients = (
                np.asarray(
                    distortion_raw,
                    dtype=np.float64,
                ).reshape(-1, 1)
                if distortion_raw is not None
                else None
            )
        except Exception as error:
            self.camera_matrix = None
            self.distortion_coefficients = None
            record_profile_error(
                "camera intrinsic metadata load failed: "
                f"{error}"
            )

        profile_index = profile_number(
            profile,
            "camera_index_hint",
            0,
            int,
        )
        if camera_device:
            self.camera_device = str(camera_device)
            self.camera_index = (
                int(camera_index)
                if (
                    camera_index is not None
                    and not isinstance(
                        camera_index,
                        str,
                    )
                )
                else None
            )
        elif isinstance(camera_index, str):
            self.camera_device = camera_index
            self.camera_index = None
        else:
            profile_device = profile.get(
                "camera_device"
            )
            self.camera_device = (
                str(profile_device)
                if (
                    profile_device
                    and camera_index is None
                )
                else None
            )
            self.camera_index = int(
                camera_index
                if camera_index is not None
                else profile_index
            )

        self.camera_source: int | str = (
            self.camera_device
            if self.camera_device is not None
            else int(self.camera_index or 0)
        )
        self.width = int(
            width
            if width is not None
            else profile_number(
                profile_capture,
                "width",
                1280,
                int,
            )
        )
        self.height = int(
            height
            if height is not None
            else profile_number(
                profile_capture,
                "height",
                720,
                int,
            )
        )
        self.fps = float(
            fps
            if fps is not None
            else profile_number(
                profile_capture,
                "fps",
                30.0,
                float,
            )
        )
        selected_fourcc = str(
            fourcc
            if fourcc is not None
            else profile_capture.get(
                "fourcc",
                "MJPG",
            )
        ).strip().upper()
        if len(selected_fourcc) != 4:
            if fourcc is not None:
                raise ValueError(
                    "fourcc는 정확히 4글자여야 합니다."
                )
            record_profile_error(
                "invalid profile fourcc: must be exactly 4 characters"
            )
            selected_fourcc = "MJPG"
        self.fourcc = selected_fourcc

        autofocus_raw = profile_capture.get("autofocus")
        self.autofocus = (
            bool(autofocus_raw)
            if autofocus_raw is not None
            else None
        )
        focus_raw = profile_capture.get("focus_absolute")
        self.focus_absolute = (
            float(focus_raw)
            if focus_raw is not None
            else None
        )
        self.undistort_requested = bool(
            profile_capture.get("undistort", True)
        )
        self._undistort_map1 = None
        self._undistort_map2 = None
        self._undistort_active = False
        self._undistort_reason = "not configured"
        self._configure_undistortion()

        self.requested_capture: dict[str, Any] = {
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "fourcc": self.fourcc,
        }
        self.effective_capture: dict[str, Any] = {
            "width": None,
            "height": None,
            "fps": None,
            "fourcc": None,
            "frame_width": None,
            "frame_height": None,
        }
        self.capture_warnings: list[str] = []

        self._camera: cv2.VideoCapture | None = None
        self._pending_frame = None
        self._camera_lock = threading.Lock()
        self._last_error: str | None = None
        self._capture_worker = LatestFrameCaptureWorker(
            name="work-order-camera-capture",
            read_frame=self._capture_next_frame,
            error_message=lambda: self._last_error,
            capture_fps=self.fps,
        )

    def _configure_undistortion(self) -> None:
        self._undistort_map1 = None
        self._undistort_map2 = None
        self._undistort_active = False

        if not self.undistort_requested:
            self._undistort_reason = "disabled by profile"
            return
        if not self.calibrated:
            self._undistort_reason = "camera profile is not calibrated"
            return
        if self.camera_matrix is None or self.distortion_coefficients is None:
            self._undistort_reason = "camera matrix/distortion coefficients are missing"
            return
        try:
            calibration_width = int(self.calibration_image_width)
            calibration_height = int(self.calibration_image_height)
        except (TypeError, ValueError):
            self._undistort_reason = "calibration image size is missing"
            return
        if (calibration_width, calibration_height) != (self.width, self.height):
            self._undistort_reason = (
                "capture/calibration resolution mismatch: "
                f"capture={self.width}x{self.height}, "
                f"calibration={calibration_width}x{calibration_height}"
            )
            return
        self._undistort_map1, self._undistort_map2 = cv2.initUndistortRectifyMap(
            self.camera_matrix,
            self.distortion_coefficients,
            None,
            self.camera_matrix,
            (self.width, self.height),
            cv2.CV_32FC1,
        )
        self._undistort_active = True
        self._undistort_reason = "active"

    def _apply_optional_camera_controls_locked(self, camera: cv2.VideoCapture) -> None:
        if self.autofocus is not None:
            try:
                camera.set(
                    cv2.CAP_PROP_AUTOFOCUS,
                    1.0 if self.autofocus else 0.0,
                )
            except Exception as error:
                self.capture_warnings.append(
                    f"autofocus control failed: {error}"
                )

        if self.autofocus is False and self.focus_absolute is not None:
            try:
                camera.set(cv2.CAP_PROP_FOCUS, float(self.focus_absolute))
            except Exception as error:
                self.capture_warnings.append(
                    f"focus control failed: {error}"
                )

    def _prepare_frame(self, frame):
        if (
            self._undistort_active
            and self._undistort_map1 is not None
            and self._undistort_map2 is not None
        ):
            frame_height, frame_width = frame.shape[:2]
            if (frame_width, frame_height) == (self.width, self.height):
                return cv2.remap(
                    frame,
                    self._undistort_map1,
                    self._undistort_map2,
                    interpolation=cv2.INTER_LINEAR,
                )
        return frame

    def _open_video_capture(self):
        if (
            isinstance(self.camera_source, str)
            and self.camera_source.startswith("/dev/")
        ):
            return cv2.VideoCapture(
                self.camera_source,
                cv2.CAP_V4L2,
            )
        return cv2.VideoCapture(self.camera_source)

    def _release_camera_locked(self) -> None:
        if self._camera is not None:
            self._camera.release()
        self._camera = None
        self._pending_frame = None

    def _update_capture_warnings_locked(self) -> None:
        warnings: list[str] = []
        effective = self.effective_capture

        for key in ("width", "height"):
            actual = effective.get(key)
            requested = self.requested_capture[key]
            if (
                actual is not None
                and int(round(float(actual))) != int(requested)
            ):
                warnings.append(
                    f"{key} requested={requested}, effective={actual}"
                )

        actual_fps = effective.get("fps")
        if (
            actual_fps is not None
            and abs(float(actual_fps) - self.fps) > 0.5
        ):
            warnings.append(
                f"fps requested={self.fps}, effective={actual_fps}"
            )

        actual_fourcc = str(
            effective.get("fourcc") or ""
        ).upper()
        if actual_fourcc != self.fourcc:
            warnings.append(
                f"fourcc requested={self.fourcc}, "
                f"effective={actual_fourcc or 'UNKNOWN'}"
            )

        frame_width = effective.get("frame_width")
        frame_height = effective.get("frame_height")
        if (
            frame_width is not None
            and int(frame_width) != self.width
        ):
            warnings.append(
                f"first frame width requested={self.width}, "
                f"actual={frame_width}"
            )
        if (
            frame_height is not None
            and int(frame_height) != self.height
        ):
            warnings.append(
                f"first frame height requested={self.height}, "
                f"actual={frame_height}"
            )

        self.capture_warnings = warnings

    def _ensure_camera_locked(self) -> bool:
        if (
            self._camera is not None
            and self._camera.isOpened()
        ):
            return True

        self._release_camera_locked()
        camera = self._open_video_capture()
        if not camera.isOpened():
            camera.release()
            self._last_error = (
                f"작업지시서 Camera source "
                f"{self.camera_source!r}를 열 수 없습니다."
            )
            return False

        camera.set(
            cv2.CAP_PROP_FOURCC,
            float(
                cv2.VideoWriter_fourcc(
                    *self.fourcc
                )
            ),
        )
        camera.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            float(self.width),
        )
        camera.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            float(self.height),
        )
        camera.set(
            cv2.CAP_PROP_FPS,
            float(self.fps),
        )
        self._apply_optional_camera_controls_locked(camera)

        self.effective_capture.update(
            {
                "width": float(
                    camera.get(
                        cv2.CAP_PROP_FRAME_WIDTH
                    )
                ),
                "height": float(
                    camera.get(
                        cv2.CAP_PROP_FRAME_HEIGHT
                    )
                ),
                "fps": float(
                    camera.get(
                        cv2.CAP_PROP_FPS
                    )
                ),
                "fourcc": _decode_fourcc(
                    camera.get(
                        cv2.CAP_PROP_FOURCC
                    )
                ),
                "frame_width": None,
                "frame_height": None,
            }
        )

        ok, frame = camera.read()
        if (
            not ok
            or frame is None
            or not hasattr(frame, "shape")
            or len(frame.shape) < 2
            or frame.shape[0] <= 0
            or frame.shape[1] <= 0
        ):
            camera.release()
            self._last_error = (
                f"작업지시서 Camera source "
                f"{self.camera_source!r}에서 "
                "첫 프레임을 읽지 못했습니다."
            )
            self._camera = None
            self._pending_frame = None
            return False

        frame = self._prepare_frame(frame)
        frame_height, frame_width = frame.shape[:2]
        self.effective_capture[
            "frame_width"
        ] = int(frame_width)
        self.effective_capture[
            "frame_height"
        ] = int(frame_height)
        self._update_capture_warnings_locked()
        for warning in self.capture_warnings:
            print(
                "[WORK ORDER CAMERA WARNING]",
                warning,
            )

        self._camera = camera
        self._pending_frame = frame
        self._last_error = None
        return True

    def _capture_next_frame(self):
        with self._camera_lock:
            if not self._ensure_camera_locked():
                return None

            assert self._camera is not None
            if self._pending_frame is not None:
                frame = self._pending_frame
                self._pending_frame = None
                return frame

            ok, frame = self._camera.read()
            if (
                not ok
                or frame is None
                or not hasattr(frame, "shape")
                or len(frame.shape) < 2
                or frame.shape[0] <= 0
                or frame.shape[1] <= 0
            ):
                self._last_error = (
                    f"작업지시서 Camera source "
                    f"{self.camera_source!r}에서 "
                    "프레임을 읽지 못했습니다."
                )
                self._release_camera_locked()
                return None

            frame = self._prepare_frame(frame)
            frame_height, frame_width = (
                frame.shape[:2]
            )
            self.effective_capture[
                "frame_width"
            ] = int(frame_width)
            self.effective_capture[
                "frame_height"
            ] = int(frame_height)
            self._update_capture_warnings_locked()
            self._last_error = None
            return frame

    def start(
        self,
        wait_timeout: float = 1.0,
    ) -> bool:
        return self._capture_worker.start(
            wait_timeout=wait_timeout,
        )

    def _read_frame(
        self,
        *,
        copy: bool = False,
        wait_timeout: float = 1.0,
    ):
        self.start(wait_timeout=0.0)
        snapshot = (
            self._capture_worker.get_latest_frame(
                wait_timeout=wait_timeout,
                copy=copy,
            )
        )
        if snapshot is None:
            worker_error = (
                self._capture_worker.diagnostics().get(
                    "last_error"
                )
            )
            if worker_error:
                self._last_error = str(worker_error)
            return None
        return snapshot.frame

    def _stop_capture_worker(self) -> None:
        self._capture_worker.request_stop()
        stopped = self._capture_worker.join(
            timeout=2.0
        )
        with self._camera_lock:
            self._release_camera_locked()
        if not stopped:
            self._capture_worker.join(
                timeout=1.0
            )
        self._capture_worker.clear_latest_frame()

    def read_frame(self, copy: bool = True):
        """Return the latest BGR frame for OCR/part-inspection consumers.

        The fixed camera is shared by work-order OCR and future tray part
        inspection. The capture worker owns VideoCapture.read(); consumers
        receive the latest frame after the worker lock has been released.
        """
        return self._read_frame(
            copy=copy
        )

    @staticmethod
    def discover_camera_sources() -> list[dict[str, Any]]:
        """List Linux V4L camera nodes without opening either role camera."""
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        by_id_dir = Path("/dev/v4l/by-id")
        if by_id_dir.is_dir():
            for path in sorted(by_id_dir.iterdir()):
                if not path.exists():
                    continue
                resolved = str(path.resolve())
                seen.add(resolved)
                sources.append(
                    {
                        "label": path.name,
                        "device": str(path),
                        "resolved_device": resolved,
                        "stable_id": True,
                    }
                )
        for path in sorted(Path("/dev").glob("video*")):
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            sources.append(
                {
                    "label": path.name,
                    "device": str(path),
                    "resolved_device": resolved,
                    "stable_id": False,
                }
            )
        return sources

    def select_camera(
        self,
        *,
        camera_index: int | None = None,
        camera_device: str | None = None,
        width: int | None = None,
        height: int | None = None,
        fps: float | None = None,
        fourcc: str | None = None,
    ) -> dict[str, Any]:
        selected_fourcc = str(fourcc or self.fourcc).strip().upper()
        if len(selected_fourcc) != 4:
            raise ValueError("fourcc는 정확히 4글자여야 합니다.")
        next_device = str(camera_device).strip() if camera_device else None
        next_index = int(camera_index) if camera_index is not None else self.camera_index
        if next_device is None and next_index is None:
            raise ValueError("camera_device 또는 camera_index가 필요합니다.")

        self._stop_capture_worker()
        with self._camera_lock:
            self.camera_device = next_device
            self.camera_index = next_index
            self.camera_source = next_device if next_device is not None else int(next_index)
            self.width = int(width) if width is not None else self.width
            self.height = int(height) if height is not None else self.height
            self.fps = float(fps) if fps is not None else self.fps
            self.fourcc = selected_fourcc
            self.requested_capture = {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "fourcc": self.fourcc,
            }
            self.effective_capture = {
                "width": None,
                "height": None,
                "fps": None,
                "fourcc": None,
                "frame_width": None,
                "frame_height": None,
            }
            self.capture_warnings = []
            self._configure_undistortion()
            self._last_error = None
        self._capture_worker.set_capture_fps(self.fps)
        connected = self.start(wait_timeout=1.0)
        return {
            "success": True,
            "connected": connected,
            "camera_index": self.camera_index,
            "camera_device": self.camera_device,
            "camera_source": self.camera_source,
            "requested_capture": dict(self.requested_capture),
            "message": (
                "Work Order / Inspection Camera 설정을 적용했습니다."
                if connected
                else "설정을 적용했지만 camera frame은 아직 수신되지 않습니다."
            ),
        }

    def get_status(self) -> dict[str, Any]:
        connected = self.start(
            wait_timeout=1.0
        )
        frame = self._read_frame(
            wait_timeout=0.0
        )
        capture_diagnostics = (
            self._capture_worker.diagnostics()
        )
        common = {
            "mode": "camera",
            "camera_index": self.camera_index,
            "camera_device": self.camera_device,
            "camera_source": self.camera_source,
            "requested_capture": dict(
                self.requested_capture
            ),
            "effective_capture": dict(
                self.effective_capture
            ),
            "capture_warnings": list(
                self.capture_warnings
            ),
            "camera_profile": str(
                self.camera_profile
            ),
            "camera_profile_name": (
                self.camera_profile.name
            ),
            "camera_profile_error": (
                self.camera_profile_error
            ),
            "camera_name": self.camera_name,
            "calibrated": self.calibrated,
            "camera_calibrated": (
                self.calibrated
            ),
            "calibration_image_width": (
                self.calibration_image_width
            ),
            "calibration_image_height": (
                self.calibration_image_height
            ),
            "rms_reprojection_error": (
                self.rms_reprojection_error
            ),
            "camera_matrix_loaded": (
                self.camera_matrix is not None
            ),
            "distortion_coefficients_loaded": (
                self.distortion_coefficients
                is not None
            ),
            "autofocus_requested": self.autofocus,
            "focus_absolute_requested": self.focus_absolute,
            "undistort_requested": self.undistort_requested,
            "undistort_active": self._undistort_active,
            "undistort_reason": self._undistort_reason,
            "capture_running": capture_diagnostics[
                "capture_running"
            ],
            "frame_id": capture_diagnostics[
                "frame_id"
            ],
            "captured_at_monotonic": capture_diagnostics[
                "captured_at_monotonic"
            ],
            "frame_age_ms": capture_diagnostics[
                "frame_age_ms"
            ],
            "capture_fps": capture_diagnostics[
                "capture_fps"
            ],
            "read_failures": capture_diagnostics[
                "read_failures"
            ],
            "reconnect_count": capture_diagnostics[
                "reconnect_count"
            ],
            "last_error": (
                capture_diagnostics[
                    "last_error"
                ]
                or self._last_error
            ),
        }

        if not connected or frame is None:
            return {
                **common,
                "connected": False,
                "error": (
                    capture_diagnostics[
                        "last_error"
                    ]
                    or self._last_error
                ),
            }

        height, width = frame.shape[:2]
        return {
            **common,
            "connected": True,
            "image_width": int(width),
            "image_height": int(height),
            "error": None,
        }

    def get_jpeg_frame(
        self,
        jpeg_quality: int = 92,
    ) -> bytes | None:
        frame = self._read_frame()
        if frame is None:
            return None

        quality = max(
            50,
            min(int(jpeg_quality), 100),
        )
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [
                cv2.IMWRITE_JPEG_QUALITY,
                quality,
            ],
        )
        if not ok:
            self._last_error = (
                "작업지시서 카메라 JPEG "
                "인코딩에 실패했습니다."
            )
            return None

        return encoded.tobytes()

    def iter_mjpeg(
        self,
        jpeg_quality: int = 85,
        max_fps: float = 15.0,
    ):
        frame_interval = 1.0 / max(
            1.0,
            float(max_fps),
        )

        while True:
            started = time.monotonic()
            jpeg = self.get_jpeg_frame(
                jpeg_quality=jpeg_quality
            )
            if jpeg is None:
                time.sleep(0.15)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )

            elapsed = time.monotonic() - started
            if elapsed < frame_interval:
                time.sleep(
                    frame_interval - elapsed
                )

    def close(self) -> None:
        self._stop_capture_worker()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            # Never raise during interpreter shutdown.
            pass
