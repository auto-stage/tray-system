from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any, Callable


@dataclass(frozen=True)
class LatestFrameSnapshot:
    frame: Any
    frame_id: int
    captured_at: float


class LatestFrameCaptureWorker:
    """Continuously overwrite one latest frame; never queue camera frames."""

    def __init__(
        self,
        *,
        name: str,
        read_frame: Callable[[], Any | None],
        error_message: Callable[[], str | None],
        capture_fps: float,
        reconnect_delay: float = 0.2,
    ) -> None:
        self.name = str(name)
        self._read_frame = read_frame
        self._error_message = error_message
        self._frame_interval = 1.0 / max(
            1.0,
            float(capture_fps),
        )
        self._reconnect_delay = max(
            0.01,
            float(reconnect_delay),
        )

        self._condition = threading.Condition()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._latest_frame: Any | None = None
        self._frame_id = 0
        self._captured_at: float | None = None
        self._previous_capture_at: float | None = None
        self._capture_fps: float | None = None
        self._connected = False
        self._read_failures = 0
        self._reconnect_count = 0
        self._failure_pending_reconnect = False
        self._last_error: str | None = None

    def set_capture_fps(
        self,
        capture_fps: float,
    ) -> None:
        self._frame_interval = 1.0 / max(
            1.0,
            float(capture_fps),
        )

    def start(
        self,
        wait_timeout: float = 0.0,
    ) -> bool:
        with self._lifecycle_lock:
            if (
                self._thread is None
                or not self._thread.is_alive()
            ):
                self._stop_event.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name=self.name,
                    daemon=True,
                )
                self._thread.start()

        if wait_timeout > 0:
            self.get_latest_frame(
                wait_timeout=wait_timeout,
            )

        with self._condition:
            return bool(self._connected)

    def request_stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

    def join(
        self,
        timeout: float | None = None,
    ) -> bool:
        with self._lifecycle_lock:
            thread = self._thread

        if (
            thread is not None
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=timeout)

        return bool(
            thread is None
            or not thread.is_alive()
        )

    def stop(
        self,
        timeout: float | None = 2.0,
    ) -> bool:
        self.request_stop()
        stopped = self.join(timeout=timeout)
        if stopped:
            self.clear_latest_frame()
        return stopped

    def clear_latest_frame(self) -> None:
        with self._condition:
            self._latest_frame = None
            self._captured_at = None
            self._connected = False
            self._capture_fps = None
            self._previous_capture_at = None
            self._condition.notify_all()

    def get_latest_frame(
        self,
        *,
        wait_timeout: float = 0.0,
        copy: bool = False,
    ) -> LatestFrameSnapshot | None:
        deadline = (
            time.monotonic() + float(wait_timeout)
            if wait_timeout > 0
            else None
        )

        with self._condition:
            while (
                self._latest_frame is None
                and not self._stop_event.is_set()
                and deadline is not None
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)

            if self._latest_frame is None:
                return None

            frame = self._latest_frame
            if copy:
                frame = frame.copy()
            return LatestFrameSnapshot(
                frame=frame,
                frame_id=self._frame_id,
                captured_at=float(self._captured_at),
            )

    def diagnostics(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._condition:
            captured_at = self._captured_at
            return {
                "capture_running": bool(
                    self._thread is not None
                    and self._thread.is_alive()
                    and not self._stop_event.is_set()
                ),
                "connected": bool(self._connected),
                "frame_id": int(self._frame_id),
                "captured_at_monotonic": captured_at,
                "frame_age_ms": (
                    max(0.0, (now - captured_at) * 1000.0)
                    if captured_at is not None
                    else None
                ),
                "capture_fps": self._capture_fps,
                "read_failures": int(self._read_failures),
                "reconnect_count": int(self._reconnect_count),
                "last_error": self._last_error,
            }

    def _record_failure(
        self,
        error: str | None,
    ) -> None:
        with self._condition:
            self._latest_frame = None
            self._captured_at = None
            self._connected = False
            self._capture_fps = None
            self._previous_capture_at = None
            self._read_failures += 1
            self._failure_pending_reconnect = True
            self._last_error = error
            self._condition.notify_all()

    def _publish_frame(
        self,
        frame: Any,
        captured_at: float,
    ) -> None:
        with self._condition:
            if self._failure_pending_reconnect:
                self._reconnect_count += 1
                self._failure_pending_reconnect = False

            if self._previous_capture_at is not None:
                elapsed = captured_at - self._previous_capture_at
                if elapsed > 0:
                    instant_fps = 1.0 / elapsed
                    self._capture_fps = (
                        instant_fps
                        if self._capture_fps is None
                        else (
                            self._capture_fps * 0.9
                            + instant_fps * 0.1
                        )
                    )

            self._previous_capture_at = captured_at
            self._latest_frame = frame
            self._frame_id += 1
            self._captured_at = captured_at
            self._connected = True
            self._last_error = None
            self._condition.notify_all()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                started = time.monotonic()
                try:
                    frame = self._read_frame()
                except Exception as error:
                    self._record_failure(str(error))
                    self._stop_event.wait(
                        self._reconnect_delay
                    )
                    continue

                if self._stop_event.is_set():
                    break

                if frame is None:
                    self._record_failure(
                        self._error_message()
                    )
                    self._stop_event.wait(
                        self._reconnect_delay
                    )
                    continue

                self._publish_frame(
                    frame,
                    time.monotonic(),
                )

                remaining = (
                    self._frame_interval
                    - (time.monotonic() - started)
                )
                if remaining > 0:
                    self._stop_event.wait(remaining)
        finally:
            with self._condition:
                self._connected = False
                self._condition.notify_all()
