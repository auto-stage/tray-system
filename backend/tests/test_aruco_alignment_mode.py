from pathlib import Path
import sys

import pytest
import yaml

from backend.services.aruco_alignment_mode import (
    load_alignment_mode,
    normalize_alignment_mode,
    resolve_alignment_mode,
)


def _backend_server():
    backend_dir = Path(__file__).resolve().parents[1]
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    import server
    return server


@pytest.mark.parametrize(("raw", "expected"), [
    ("disabled", "disabled"),
    ("OBSERVE_ONLY", "observe_only"),
    (" closed_loop ", "closed_loop"),
])
def test_normalize_alignment_mode(raw, expected):
    assert normalize_alignment_mode(raw) == expected


def test_invalid_alignment_mode_is_rejected():
    with pytest.raises(ValueError):
        normalize_alignment_mode("automatic")


def test_explicit_mode_overrides_legacy_enabled():
    assert (
        resolve_alignment_mode({"mode": "observe_only", "enabled": True})
        == "observe_only"
    )


def test_environment_override_has_highest_priority():
    assert (
        resolve_alignment_mode(
            {"mode": "closed_loop", "enabled": True},
            override="disabled",
        )
        == "disabled"
    )


@pytest.mark.parametrize(("enabled", "expected"), [
    (False, "disabled"),
    (True, "closed_loop"),
    ("false", "disabled"),
    ("true", "closed_loop"),
])
def test_legacy_enabled_fallback(enabled, expected):
    assert resolve_alignment_mode({"enabled": enabled}) == expected


def test_load_alignment_mode_from_yaml(tmp_path: Path):
    path = tmp_path / "system.yaml"
    path.write_text(
        yaml.safe_dump(
            {"integration": {"correction_loop": {"mode": "observe_only"}}}
        ),
        encoding="utf-8",
    )
    assert load_alignment_mode(path) == "observe_only"


def _set_observe_only(monkeypatch, backend_server, vision):
    monkeypatch.setattr(backend_server, "ARUCO_ALIGNMENT_MODE", "observe_only")
    monkeypatch.setattr(backend_server, "VISION_MODE", "aruco")
    monkeypatch.setattr(backend_server, "aruco_camera_enabled", True)
    monkeypatch.setattr(backend_server, "aruco_vision", vision)


def test_observe_only_accepts_tray_id_without_stage_calibration(monkeypatch):
    backend_server = _backend_server()

    class FakeArucoVision:
        def detect_tray_aruco(self, expected_tray_id):
            return {
                "success": True,
                "detected": True,
                "aruco_id": 3,
                "tray_id": expected_tray_id,
                "pose_valid": False,
                "alignment_ok": None,
                "ready_for_stage_correction": False,
                "stage_correction_delta_mm": None,
                "message": "Tray ID 식별 성공, Stage calibration 미완료",
            }

    _set_observe_only(monkeypatch, backend_server, FakeArucoVision())

    result = backend_server.vision_align(
        backend_server.VisionAlignRequest(expected_tray_id=3)
    )

    assert result["success"] is True
    assert result["mode"] == "observe_only"
    assert result["observed"] is True
    assert result["stage_moved"] is False
    assert result["correction_available"] is False
    assert result["aruco_id"] == 3
    assert result["tray_id"] == 3


def test_observe_only_material_flow_passes_after_correct_tray_id(monkeypatch):
    backend_server = _backend_server()

    class FakeArucoVision:
        def detect_tray_aruco(self, expected_tray_id):
            return {
                "success": True,
                "detected": True,
                "aruco_id": expected_tray_id,
                "tray_id": expected_tray_id,
                "pose_valid": False,
                "alignment_ok": None,
                "ready_for_stage_correction": False,
            }

    _set_observe_only(monkeypatch, backend_server, FakeArucoVision())
    result = backend_server.material_flow_alignment_callback(2)

    assert result["success"] is True
    assert result["alignment_gate_passed"] is True
    assert result["stage_moved"] is False
    assert result["tray_id"] == 2


def test_observe_only_rejects_tray_id_mismatch(monkeypatch):
    backend_server = _backend_server()

    class FakeArucoVision:
        def detect_tray_aruco(self, expected_tray_id):
            return {
                "success": False,
                "detected": True,
                "error_code": "TRAY_ID_MISMATCH",
                "expected_tray_id": expected_tray_id,
                "detected_tray_ids": [4],
                "detected_aruco_ids": [4],
                "message": "검출된 Tray가 Backend가 기대한 Tray와 다릅니다.",
            }

    _set_observe_only(monkeypatch, backend_server, FakeArucoVision())
    result = backend_server.material_flow_alignment_callback(3)

    assert result["success"] is False
    assert result["error"] == "TRAY_ID_MISMATCH"
    assert result["observed"] is True
    assert result["stage_moved"] is False


def test_observe_only_material_flow_callback_is_defined(monkeypatch):
    backend_server = _backend_server()
    called = []

    def fake_callback(tray_id):
        called.append(tray_id)
        return {"success": True}

    monkeypatch.setattr(backend_server, "ARUCO_ALIGNMENT_MODE", "observe_only")
    monkeypatch.setattr(
        backend_server,
        "material_flow_alignment_callback",
        fake_callback,
    )

    callback = backend_server.build_material_flow_alignment_callback()
    assert callback is not None
    assert callback(2)["success"] is True
    assert called == [2]


def test_disabled_material_flow_has_no_alignment_callback(monkeypatch):
    backend_server = _backend_server()
    monkeypatch.setattr(backend_server, "ARUCO_ALIGNMENT_MODE", "disabled")
    assert backend_server.build_material_flow_alignment_callback() is None


def test_closed_loop_safety_switch_blocks_motion(monkeypatch):
    backend_server = _backend_server()

    class FakeArucoVision:
        def get_correction_loop_config(self):
            return {"enabled": False}

    class FailIfMovedStage:
        def move_relative(self, dx, dz):
            raise AssertionError("Stage must not move while closed-loop is disabled")

    monkeypatch.setattr(backend_server, "ARUCO_ALIGNMENT_MODE", "closed_loop")
    monkeypatch.setattr(backend_server, "VISION_MODE", "aruco")
    monkeypatch.setattr(backend_server, "aruco_camera_enabled", True)
    monkeypatch.setattr(backend_server, "aruco_vision", FakeArucoVision())
    monkeypatch.setattr(backend_server, "stage", FailIfMovedStage())

    result = backend_server.vision_align(
        backend_server.VisionAlignRequest(expected_tray_id=1)
    )

    assert result["success"] is False
    assert result["error"] == "CORRECTION_LOOP_DISABLED"


def test_closed_loop_moves_stage_and_reobserves(monkeypatch):
    backend_server = _backend_server()

    class FakeStage:
        def __init__(self):
            self.moves = []

        def move_relative(self, dx, dz):
            self.moves.append((dx, dz))
            return {"success": True}

    class FakeArucoVision:
        def __init__(self):
            self.calls = 0

        def get_correction_loop_config(self):
            return {
                "enabled": True,
                "max_iterations": 2,
                "reobserve_after_move": True,
                "tolerance_mm": {"x": 0.5, "z": 0.5},
                "max_single_correction_mm": {"x": 10.0, "z": 10.0},
            }

        def detect_tray_aruco(self, expected_tray_id):
            self.calls += 1
            if self.calls == 1:
                return {
                    "success": True,
                    "detected": True,
                    "tray_id": expected_tray_id,
                    "ready_for_stage_correction": True,
                    "stage_correction_delta_mm": {"x": 2.0, "z": -1.0},
                }
            return {
                "success": True,
                "detected": True,
                "tray_id": expected_tray_id,
                "ready_for_stage_correction": True,
                "stage_correction_delta_mm": {"x": 0.1, "z": 0.1},
            }

    fake_stage = FakeStage()
    fake_vision = FakeArucoVision()

    monkeypatch.setattr(backend_server, "ARUCO_ALIGNMENT_MODE", "closed_loop")
    monkeypatch.setattr(backend_server, "VISION_MODE", "aruco")
    monkeypatch.setattr(backend_server, "aruco_camera_enabled", True)
    monkeypatch.setattr(backend_server, "aruco_vision", fake_vision)
    monkeypatch.setattr(backend_server, "stage", fake_stage)

    result = backend_server.vision_align(
        backend_server.VisionAlignRequest(expected_tray_id=2)
    )

    assert result["success"] is True
    assert result["aligned"] is True
    assert result["verified"] is True
    assert fake_stage.moves == [(2.0, -1.0)]
    assert fake_vision.calls == 2
