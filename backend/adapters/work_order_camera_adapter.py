from __future__ import annotations

import threading
import time
from typing import Any

import cv2


class WorkOrderCameraAdapter:
    """Fixed camera used only for work-order image capture/OCR input."""

    def __init__(
        self,
        camera_index: int = 0,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        self.camera_index = int(camera_index)
        self.width = int(width) if width else None
        self.height = int(height) if height else None

        self._camera: cv2.VideoCapture | None = None
        self._camera_lock = threading.Lock()
        self._last_error: str | None = None

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
                f"작업지시서 Camera index {self.camera_index}를 열 수 없습니다."
            )
            return False

        if self.width:
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        if self.height:
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))

        self._camera = camera
        self._last_error = None
        return True

    def _read_frame(self):
        with self._camera_lock:
            if not self._ensure_camera_locked():
                return None

            assert self._camera is not None
            ok, frame = self._camera.read()
            if not ok or frame is None:
                self._last_error = (
                    f"작업지시서 Camera index {self.camera_index}에서 "
                    "프레임을 읽지 못했습니다."
                )
                return None

            self._last_error = None
            return frame

    def get_status(self) -> dict[str, Any]:
        frame = self._read_frame()
        if frame is None:
            return {
                "connected": False,
                "mode": "camera",
                "camera_index": self.camera_index,
                "error": self._last_error,
            }

        height, width = frame.shape[:2]
        return {
            "connected": True,
            "mode": "camera",
            "camera_index": self.camera_index,
            "image_width": int(width),
            "image_height": int(height),
            "error": None,
        }

    def get_jpeg_frame(self, jpeg_quality: int = 92) -> bytes | None:
        frame = self._read_frame()
        if frame is None:
            return None

        quality = max(50, min(int(jpeg_quality), 100))
        ok, encoded = cv2.imencode(
            ".jpg",
            frame,
            [cv2.IMWRITE_JPEG_QUALITY, quality],
        )
        if not ok:
            self._last_error = "작업지시서 카메라 JPEG 인코딩에 실패했습니다."
            return None

        return encoded.tobytes()

    def iter_mjpeg(self, jpeg_quality: int = 85):
        while True:
            jpeg = self.get_jpeg_frame(jpeg_quality=jpeg_quality)
            if jpeg is None:
                time.sleep(0.15)
                continue

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            time.sleep(0.03)

    def close(self) -> None:
        with self._camera_lock:
            if self._camera is not None:
                self._camera.release()
                self._camera = None

    def __del__(self) -> None:
        self.close()
