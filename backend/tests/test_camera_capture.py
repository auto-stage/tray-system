from __future__ import annotations

from collections.abc import Callable
import importlib
from pathlib import Path
import sys
import threading
import time

import cv2
import numpy as np
import pytest

from backend.adapters import aruco_vision_adapter
from backend.adapters import work_order_camera_adapter
from backend.adapters.camera_capture_worker import (
    LatestFrameCaptureWorker,
)
from backend.adapters.aruco_vision_adapter import (
    ArucoVisionAdapter,
)
from backend.adapters.work_order_camera_adapter import (
    WorkOrderCameraAdapter,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


def load_backend_server(monkeypatch):
    monkeypatch.setenv(
        "VISION_MODE",
        "mock",
    )
    monkeypatch.setenv(
        "WORK_ORDER_CAMERA_MODE",
        "off",
    )
    monkeypatch.syspath_prepend(
        str(BACKEND_DIR)
    )
    return importlib.import_module("server")


class FakeVideoCapture:
    def __init__(
        self,
        source,
        *args,
        read_results=None,
        frame_size: tuple[int, int] | None = None,
        failed_set_properties: set[int] | None = None,
        failed_get_properties: set[int] | None = None,
    ):
        self.source = source
        self.args = args
        self.opened = True
        self.release_count = 0
        self.read_count = 0
        self.read_thread_names: list[str] = []
        self.set_calls: list[tuple[int, float]] = []
        self.properties: dict[int, float] = {}
        self.read_results = list(
            read_results or []
        )
        self.frame_size = frame_size
        self.failed_set_properties = set(
            failed_set_properties or set()
        )
        self.failed_get_properties = set(
            failed_get_properties or set()
        )

    def isOpened(self):
        return self.opened

    def release(self):
        if self.opened:
            self.release_count += 1
        self.opened = False

    def set(self, prop, value):
        self.set_calls.append(
            (int(prop), float(value))
        )
        if int(prop) in self.failed_set_properties:
            return False
        self.properties[int(prop)] = float(value)
        return True

    def get(self, prop):
        if int(prop) in self.failed_get_properties:
            raise RuntimeError(
                "property unsupported"
            )
        return self.properties.get(
            int(prop),
            0.0,
        )

    def read(self):
        self.read_count += 1
        self.read_thread_names.append(
            threading.current_thread().name
        )
        if self.read_results:
            return self.read_results.pop(0)

        if self.frame_size is None:
            width = int(
                self.properties.get(
                    cv2.CAP_PROP_FRAME_WIDTH,
                    1280,
                )
            )
            height = int(
                self.properties.get(
                    cv2.CAP_PROP_FRAME_HEIGHT,
                    720,
                )
            )
        else:
            width, height = self.frame_size

        return (
            True,
            np.zeros(
                (height, width, 3),
                dtype=np.uint8,
            ),
        )


def install_capture_factory(
    monkeypatch,
    module,
    builder: Callable[..., FakeVideoCapture]
    | None = None,
):
    created: list[FakeVideoCapture] = []

    def factory(source, *args):
        capture = (
            builder(source, *args)
            if builder is not None
            else FakeVideoCapture(
                source,
                *args,
            )
        )
        created.append(capture)
        return capture

    monkeypatch.setattr(
        module.cv2,
        "VideoCapture",
        factory,
    )
    return created


def wait_until(
    predicate: Callable[[], bool],
    timeout: float = 1.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


@pytest.mark.parametrize(
    ("source", "expected_device"),
    [
        (3, None),
        ("/dev/v4l/by-id/test-camera", "/dev/v4l/by-id/test-camera"),
    ],
)
def test_work_order_accepts_integer_and_string_sources(
    monkeypatch,
    source,
    expected_device,
):
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
    )
    adapter = WorkOrderCameraAdapter(
        camera_index=source,
    )

    frame = adapter.read_frame()

    assert frame.shape[:2] == (720, 1280)
    assert created[0].source == source
    assert adapter.camera_device == expected_device
    assert [
        prop
        for prop, _value in created[0].set_calls
    ] == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
    ]
    assert adapter.effective_capture == {
        "width": 1280.0,
        "height": 720.0,
        "fps": 30.0,
        "fourcc": "MJPG",
        "frame_width": 1280,
        "frame_height": 720,
    }
    assert adapter.capture_warnings == []
    adapter.close()


def test_device_takes_priority_over_index(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
    )
    device = "/dev/v4l/by-id/preferred-camera"
    adapter = WorkOrderCameraAdapter(
        camera_index=7,
        camera_device=device,
    )

    adapter.read_frame()

    assert adapter.camera_index == 7
    assert adapter.camera_source == device
    assert created[0].source == device
    adapter.close()


def test_first_frame_mismatch_is_reported_as_warning(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
        lambda source, *args: FakeVideoCapture(
            source,
            *args,
            frame_size=(640, 480),
        ),
    )
    adapter = WorkOrderCameraAdapter(
        camera_index=1,
    )

    adapter.read_frame()

    assert created[0].source == 1
    assert any(
        "first frame width" in warning
        for warning in adapter.capture_warnings
    )
    assert any(
        "first frame height" in warning
        for warning in adapter.capture_warnings
    )
    adapter.close()


def test_read_failure_releases_and_next_call_reopens(
    monkeypatch,
):
    first_frame = np.zeros(
        (720, 1280, 3),
        dtype=np.uint8,
    )
    build_count = 0

    def builder(source, *args):
        nonlocal build_count
        build_count += 1
        if build_count == 1:
            return FakeVideoCapture(
                source,
                *args,
                read_results=[
                    (True, first_frame),
                    (False, None),
                ],
            )
        return FakeVideoCapture(
            source,
            *args,
        )

    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
        builder,
    )
    adapter = WorkOrderCameraAdapter(
        camera_index=2,
    )

    assert np.array_equal(
        adapter.read_frame(),
        first_frame,
    )
    assert wait_until(
        lambda: len(created) >= 2
    )
    assert created[0].release_count == 1
    assert adapter.read_frame() is not None
    assert len(created) == 2
    assert [
        prop
        for prop, _value in created[1].set_calls
    ] == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
    ]
    adapter.close()


def test_close_is_idempotent(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
    )
    adapter = WorkOrderCameraAdapter()
    adapter.read_frame()

    adapter.close()
    adapter.close()

    assert created[0].release_count == 1
    assert adapter._camera is None


def test_aruco_index_capture_and_effective_properties(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
    )

    status = adapter.get_camera_status()

    assert status["connected"] is True
    assert status["camera_index"] == 4
    assert status["camera_device"] is None
    assert created[0].source == 4
    assert status["effective_capture"][
        "fourcc"
    ] == "MJPG"
    adapter.close()


def test_aruco_applies_manual_focus_after_capture_settings(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
        autofocus=False,
        focus=50,
    )

    status = adapter.get_camera_status()

    assert status["connected"] is True
    assert [
        prop
        for prop, _value in created[0].set_calls
    ] == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_AUTOFOCUS,
        cv2.CAP_PROP_FOCUS,
    ]
    assert status["requested_capture"][
        "autofocus"
    ] is False
    assert status["requested_capture"][
        "focus"
    ] == 50.0
    assert status["effective_capture"][
        "autofocus"
    ] == 0.0
    assert status["effective_capture"][
        "focus"
    ] == 50.0
    adapter.close()


def test_aruco_does_not_force_focus_when_autofocus_is_true(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
        autofocus=True,
        focus=50,
    )

    status = adapter.get_camera_status()

    assert status["connected"] is True
    properties = [
        prop
        for prop, _value in created[0].set_calls
    ]
    assert cv2.CAP_PROP_AUTOFOCUS in properties
    assert cv2.CAP_PROP_FOCUS not in properties
    assert status["effective_capture"][
        "autofocus"
    ] == 1.0
    assert status["effective_capture"][
        "focus"
    ] is None
    adapter.close()


def test_aruco_focus_failure_warns_without_crashing(
    monkeypatch,
):
    install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
        lambda source, *args: FakeVideoCapture(
            source,
            *args,
            failed_set_properties={
                cv2.CAP_PROP_FOCUS,
            },
            failed_get_properties={
                cv2.CAP_PROP_FOCUS,
            },
        ),
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
        autofocus=False,
        focus=50,
    )

    status = adapter.get_camera_status()

    assert status["connected"] is True
    assert any(
        "focus property setting failed"
        in warning
        for warning in status[
            "capture_warnings"
        ]
    )
    assert any(
        "focus property read failed"
        in warning
        for warning in status[
            "capture_warnings"
        ]
    )
    adapter.close()


def test_aruco_read_failure_releases_capture(
    monkeypatch,
):
    first_frame = np.zeros(
        (720, 1280, 3),
        dtype=np.uint8,
    )

    def builder(source, *args):
        return FakeVideoCapture(
            source,
            *args,
            read_results=[
                (True, first_frame),
                (False, None),
            ],
        )

    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
        builder,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
    )

    assert adapter._read_frame() is first_frame
    assert wait_until(
        lambda: len(created) >= 2
    )
    assert created[0].release_count == 1
    assert adapter._read_frame() is not None
    adapter.close()


def test_aruco_profile_device_overrides_index(
    monkeypatch,
):
    install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
    )

    result = adapter.select_camera(
        profile_name="camera_c525.yaml",
        camera_index=9,
    )

    assert result["camera_index"] == 9
    assert result["camera_device"].endswith(
        "B6746330-video-index0"
    )
    assert adapter.camera_source == result[
        "camera_device"
    ]
    assert adapter.requested_capture == {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "fourcc": "MJPG",
        "autofocus": False,
        "focus": 50.0,
    }
    adapter.close()


def test_aruco_reports_calibration_resolution_mismatch(
    monkeypatch,
):
    install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=0,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_laptop.yaml"
        ),
    )

    status = adapter.get_camera_status()

    assert status["camera_calibrated"] is True
    assert (
        status[
            "calibration_resolution_match"
        ]
        is False
    )
    assert status["calibration_mismatch"] is True
    assert (
        status[
            "ready_for_stage_correction"
        ]
        is False
    )
    adapter.close()


def test_aruco_startup_reuses_camera_status_open_path(
    monkeypatch,
):
    server = load_backend_server(
        monkeypatch
    )
    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
        autofocus=False,
        focus=50,
    )
    monkeypatch.setattr(
        server,
        "VISION_MODE",
        "aruco",
    )
    monkeypatch.setattr(
        server,
        "aruco_vision",
        adapter,
    )
    monkeypatch.setattr(
        server,
        "work_order_camera",
        None,
    )

    assert server.initialize_aruco_camera in (
        server.app.router.on_startup
    )
    server.initialize_aruco_camera()

    assert len(created) == 1
    assert [
        prop
        for prop, _value in created[0].set_calls
    ] == [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_AUTOFOCUS,
        cv2.CAP_PROP_FOCUS,
    ]

    assert adapter.get_camera_status()[
        "connected"
    ] is True
    assert len(created) == 1

    server.close_camera_adapters()
    assert created[0].release_count == 1
    assert adapter._camera is None


def test_aruco_startup_failure_does_not_fail_backend(
    monkeypatch,
    capsys,
):
    server = load_backend_server(
        monkeypatch
    )

    class FailingVisionAdapter:
        def __init__(self):
            self.calls = 0

        def get_camera_status(self):
            self.calls += 1
            raise RuntimeError(
                "camera unavailable"
            )

    adapter = FailingVisionAdapter()
    monkeypatch.setattr(
        server,
        "VISION_MODE",
        "aruco",
    )
    monkeypatch.setattr(
        server,
        "aruco_vision",
        adapter,
    )

    server.initialize_aruco_camera()

    assert adapter.calls == 1
    assert "Startup camera initialization failed" in (
        capsys.readouterr().out
    )


def test_aruco_startup_unavailable_then_status_reopens(
    monkeypatch,
):
    server = load_backend_server(
        monkeypatch
    )
    camera_available = False

    def builder(source, *args):
        nonlocal camera_available
        capture = FakeVideoCapture(
            source,
            *args,
        )
        if not camera_available:
            capture.opened = False
        return capture

    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
        builder,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
    )
    monkeypatch.setattr(
        server,
        "VISION_MODE",
        "aruco",
    )
    monkeypatch.setattr(
        server,
        "aruco_vision",
        adapter,
    )

    server.initialize_aruco_camera()

    assert len(created) >= 1
    assert adapter._camera is None
    assert adapter._last_error is not None

    camera_available = True

    status = server.vision_status()

    assert status["connected"] is True
    assert len(created) >= 2
    assert adapter._last_error is None
    adapter.close()


def test_work_order_loads_c270_profile_and_intrinsics():
    adapter = WorkOrderCameraAdapter()

    assert adapter.camera_profile.name == (
        "camera_c270.yaml"
    )
    assert adapter.camera_name == "Logitech C270"
    assert adapter.camera_device.endswith(
        "8EE14010-video-index0"
    )
    assert adapter.requested_capture == {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "fourcc": "MJPG",
    }
    assert adapter.calibrated is True
    assert adapter.calibration_image_width == 1280
    assert adapter.calibration_image_height == 720
    assert adapter.rms_reprojection_error == pytest.approx(
        0.6592492986069917
    )
    assert adapter.camera_matrix.shape == (3, 3)
    assert adapter.distortion_coefficients.shape == (
        5,
        1,
    )
    adapter.close()


def test_work_order_explicit_config_overrides_profile(
    tmp_path,
):
    profile_path = tmp_path / "camera_test.yaml"
    profile_path.write_text(
        """
camera_name: Profile Camera
camera_device: /dev/profile-camera
camera_index_hint: 8
capture:
  width: 640
  height: 480
  fps: 15
  fourcc: YUYV
""".strip(),
        encoding="utf-8",
    )

    adapter = WorkOrderCameraAdapter(
        camera_profile=profile_path,
        camera_device="/dev/explicit-camera",
        camera_index=3,
        width=1280,
        height=720,
        fps=30,
        fourcc="MJPG",
    )

    assert adapter.camera_source == (
        "/dev/explicit-camera"
    )
    assert adapter.camera_index == 3
    assert adapter.requested_capture == {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "fourcc": "MJPG",
    }
    adapter.close()


def test_work_order_profile_path_expands_user(
    monkeypatch,
    tmp_path,
):
    profile_path = tmp_path / "camera_home.yaml"
    profile_path.write_text(
        "camera_device: /dev/from-expanded-profile\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "HOME",
        str(tmp_path),
    )

    adapter = WorkOrderCameraAdapter(
        camera_profile="~/camera_home.yaml"
    )

    assert adapter.camera_profile == profile_path
    assert adapter.camera_source == (
        "/dev/from-expanded-profile"
    )
    adapter.close()


@pytest.mark.parametrize(
    "profile_contents",
    [
        None,
        "- invalid\n- profile\n",
    ],
)
def test_work_order_invalid_or_missing_profile_is_graceful(
    tmp_path,
    profile_contents,
):
    profile_path = tmp_path / "camera_invalid.yaml"
    if profile_contents is not None:
        profile_path.write_text(
            profile_contents,
            encoding="utf-8",
        )

    adapter = WorkOrderCameraAdapter(
        camera_profile=profile_path
    )

    assert adapter.camera_profile_error is not None
    assert adapter.camera_source == 0
    assert adapter.requested_capture == {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "fourcc": "MJPG",
    }
    assert adapter.camera_matrix is None
    assert adapter.distortion_coefficients is None
    adapter.close()


def test_work_order_invalid_profile_values_fall_back(
    tmp_path,
):
    profile_path = tmp_path / "camera_invalid_values.yaml"
    profile_path.write_text(
        """
camera_index_hint: invalid
capture:
  width: invalid
  height: invalid
  fps: invalid
  fourcc: BAD
""".strip(),
        encoding="utf-8",
    )

    adapter = WorkOrderCameraAdapter(
        camera_profile=profile_path
    )

    assert adapter.camera_source == 0
    assert adapter.requested_capture == {
        "width": 1280,
        "height": 720,
        "fps": 30.0,
        "fourcc": "MJPG",
    }
    assert adapter.camera_profile_error is not None
    adapter.close()


def test_latest_frame_worker_overwrites_single_frame():
    next_value = 0

    def read_frame():
        nonlocal next_value
        next_value += 1
        return np.full(
            (2, 2, 1),
            next_value,
            dtype=np.uint8,
        )

    worker = LatestFrameCaptureWorker(
        name="latest-frame-test",
        read_frame=read_frame,
        error_message=lambda: None,
        capture_fps=200,
    )

    worker.start()
    assert wait_until(
        lambda: worker.diagnostics()[
            "frame_id"
        ] >= 3
    )
    first_snapshot = worker.get_latest_frame()
    assert first_snapshot is not None
    first_id = first_snapshot.frame_id
    first_timestamp = first_snapshot.captured_at

    assert wait_until(
        lambda: worker.diagnostics()[
            "frame_id"
        ] > first_id
    )
    latest_snapshot = worker.get_latest_frame()

    assert latest_snapshot is not None
    assert latest_snapshot.frame_id > first_id
    assert (
        latest_snapshot.captured_at
        > first_timestamp
    )
    assert int(latest_snapshot.frame[0, 0, 0]) == (
        latest_snapshot.frame_id
    )
    assert not hasattr(worker, "frame_queue")
    assert worker.stop()
    assert worker.stop()
    assert worker.diagnostics()[
        "capture_running"
    ] is False


def test_latest_frame_worker_recovers_after_failures():
    calls = 0

    def read_frame():
        nonlocal calls
        calls += 1
        if calls <= 2:
            return None
        return np.zeros(
            (2, 2, 3),
            dtype=np.uint8,
        )

    worker = LatestFrameCaptureWorker(
        name="reconnect-test",
        read_frame=read_frame,
        error_message=lambda: "read failed",
        capture_fps=100,
        reconnect_delay=0.01,
    )

    assert worker.start(
        wait_timeout=1.0
    ) is True
    diagnostics = worker.diagnostics()
    assert diagnostics["read_failures"] >= 2
    assert diagnostics["reconnect_count"] == 1
    assert diagnostics["frame_id"] >= 1
    assert diagnostics["last_error"] is None
    assert worker.stop()


def test_camera_workers_are_independent():
    blocked_started = threading.Event()
    unblock = threading.Event()

    def blocked_read():
        blocked_started.set()
        unblock.wait(timeout=1.0)
        return np.zeros(
            (1, 1, 1),
            dtype=np.uint8,
        )

    fast_worker = LatestFrameCaptureWorker(
        name="fast-independent-camera",
        read_frame=lambda: np.zeros(
            (1, 1, 1),
            dtype=np.uint8,
        ),
        error_message=lambda: None,
        capture_fps=100,
    )
    blocked_worker = LatestFrameCaptureWorker(
        name="blocked-independent-camera",
        read_frame=blocked_read,
        error_message=lambda: None,
        capture_fps=30,
    )

    blocked_worker.start()
    assert blocked_started.wait(timeout=0.5)
    fast_worker.start()
    assert wait_until(
        lambda: fast_worker.diagnostics()[
            "frame_id"
        ] >= 3,
        timeout=0.5,
    )

    unblock.set()
    assert blocked_worker.stop()
    assert fast_worker.stop()


def test_aruco_reconnect_reapplies_capture_and_focus(
    monkeypatch,
):
    first_frame = np.zeros(
        (720, 1280, 3),
        dtype=np.uint8,
    )
    build_count = 0

    def builder(source, *args):
        nonlocal build_count
        build_count += 1
        return FakeVideoCapture(
            source,
            *args,
            read_results=(
                [
                    (True, first_frame),
                    (False, None),
                ]
                if build_count == 1
                else None
            ),
        )

    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
        builder,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
        autofocus=False,
        focus=50,
    )

    assert adapter.start(
        wait_timeout=1.0
    )
    assert wait_until(
        lambda: (
            len(created) >= 2
            and len(created[1].set_calls) >= 6
        )
    )
    expected_properties = [
        cv2.CAP_PROP_FOURCC,
        cv2.CAP_PROP_FRAME_WIDTH,
        cv2.CAP_PROP_FRAME_HEIGHT,
        cv2.CAP_PROP_FPS,
        cv2.CAP_PROP_AUTOFOCUS,
        cv2.CAP_PROP_FOCUS,
    ]
    assert [
        prop
        for prop, _value in created[1].set_calls
    ] == expected_properties
    assert created[1].properties[
        cv2.CAP_PROP_AUTOFOCUS
    ] == 0.0
    assert created[1].properties[
        cv2.CAP_PROP_FOCUS
    ] == 50.0
    adapter.close()


def test_consumers_do_not_read_video_capture_on_request_thread(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
    )
    adapter = WorkOrderCameraAdapter(
        camera_index=2,
    )

    assert adapter.start(
        wait_timeout=1.0
    )
    assert adapter.get_status()["connected"] is True
    assert adapter.read_frame() is not None
    assert adapter.get_jpeg_frame() is not None

    assert created[0].read_thread_names
    assert set(created[0].read_thread_names) == {
        "work-order-camera-capture"
    }
    adapter.close()


def test_aruco_consumers_use_background_latest_frame(
    monkeypatch,
):
    created = install_capture_factory(
        monkeypatch,
        aruco_vision_adapter,
    )
    adapter = ArucoVisionAdapter(
        camera_index=4,
        camera_profile=(
            "modules/aruco_tray_vision/"
            "config/camera_external.yaml"
        ),
    )

    assert adapter.start(
        wait_timeout=1.0
    )
    assert adapter.get_camera_status()[
        "connected"
    ] is True
    assert adapter.detect_tray_aruco()[
        "camera_connected"
    ] is True
    assert adapter.get_jpeg_frame(
        annotate=False
    ) is not None

    assert created[0].read_thread_names
    assert set(created[0].read_thread_names) == {
        "aruco-camera-capture"
    }
    adapter.close()


def test_work_order_startup_and_shutdown_worker(
    monkeypatch,
):
    server = load_backend_server(
        monkeypatch
    )
    created = install_capture_factory(
        monkeypatch,
        work_order_camera_adapter,
    )
    adapter = WorkOrderCameraAdapter(
        camera_index=2,
    )
    monkeypatch.setattr(
        server,
        "work_order_camera",
        adapter,
    )

    assert server.initialize_work_order_camera in (
        server.app.router.on_startup
    )
    server.initialize_work_order_camera()

    assert adapter._capture_worker.diagnostics()[
        "capture_running"
    ] is True
    assert len(created) == 1

    server.close_camera_adapters()

    assert adapter._capture_worker.diagnostics()[
        "capture_running"
    ] is False
    assert created[0].release_count == 1
