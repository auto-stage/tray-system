from __future__ import annotations

import cv2
import numpy as np
import yaml

from modules.aruco_tray_vision.aruco_tray import (
    calibration as calibration_module,
)
from modules.aruco_tray_vision.aruco_tray.calibration import (
    ChessboardCalibrator,
)


def fake_corners(
    calibrator: ChessboardCalibrator,
):
    return np.zeros(
        (
            calibrator.inner_cols
            * calibrator.inner_rows,
            1,
            2,
        ),
        dtype=np.float32,
    )


def test_rejects_mixed_sample_resolutions(
    monkeypatch,
):
    calibrator = ChessboardCalibrator(
        9,
        6,
        25.0,
    )
    corners = fake_corners(calibrator)
    monkeypatch.setattr(
        calibrator,
        "detect",
        lambda _frame: (True, corners),
    )

    assert calibrator.add_sample(
        np.zeros(
            (720, 1280, 3),
            dtype=np.uint8,
        )
    )
    assert not calibrator.add_sample(
        np.zeros(
            (480, 640, 3),
            dtype=np.uint8,
        )
    )
    assert calibrator.sample_count == 1
    assert calibrator.image_size == (
        1280,
        720,
    )
    assert "서로 다른 해상도" in str(
        calibrator.last_error
    )


def test_calibration_save_preserves_profile_metadata(
    monkeypatch,
    tmp_path,
):
    profile_path = (
        tmp_path
        / "camera_test.yaml"
    )
    original = {
        "calibrated": False,
        "camera_name": "Test camera",
        "camera_device": (
            "/dev/v4l/by-id/test-camera"
        ),
        "camera_index_hint": 3,
        "capture": {
            "width": 1280,
            "height": 720,
            "fps": 30,
            "fourcc": "MJPG",
        },
        "custom_metadata": {
            "keep": True,
        },
        "image_width": None,
        "image_height": None,
        "rms_reprojection_error": None,
        "camera_matrix": None,
        "distortion_coefficients": None,
    }
    profile_path.write_text(
        yaml.safe_dump(
            original,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    calibrator = ChessboardCalibrator(
        9,
        6,
        25.0,
    )
    corners = fake_corners(calibrator)
    monkeypatch.setattr(
        calibrator,
        "detect",
        lambda _frame: (True, corners),
    )
    frame = np.zeros(
        (720, 1280, 3),
        dtype=np.uint8,
    )
    for _index in range(10):
        assert calibrator.add_sample(
            frame
        )

    matrix = np.array(
        [
            [1000.0, 0.0, 640.0],
            [0.0, 1000.0, 360.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    distortion = np.zeros(
        (5, 1),
        dtype=np.float64,
    )
    monkeypatch.setattr(
        calibration_module.cv2,
        "calibrateCamera",
        lambda *_args, **_kwargs: (
            0.25,
            matrix,
            distortion,
            [],
            [],
        ),
    )

    rms = calibrator.calibrate_and_save(
        profile_path,
        camera_name="Replacement name",
        camera_index=8,
    )
    saved = yaml.safe_load(
        profile_path.read_text(
            encoding="utf-8"
        )
    )

    assert rms == 0.25
    assert saved["camera_device"] == (
        original["camera_device"]
    )
    assert saved["camera_name"] == (
        original["camera_name"]
    )
    assert saved["camera_index_hint"] == (
        original["camera_index_hint"]
    )
    assert saved["capture"] == (
        original["capture"]
    )
    assert saved["custom_metadata"] == {
        "keep": True,
    }
    assert saved["calibrated"] is True
    assert saved["image_width"] == 1280
    assert saved["image_height"] == 720
    assert saved[
        "rms_reprojection_error"
    ] == 0.25
    assert saved["camera_matrix"] == (
        matrix.tolist()
    )
    assert saved[
        "distortion_coefficients"
    ] == [0.0] * 5
