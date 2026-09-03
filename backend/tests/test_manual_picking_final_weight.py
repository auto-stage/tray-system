from pathlib import Path
import sys


backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from parts_db import load_parts_catalog
from workflow.workflow_controller import WorkflowController


def test_manual_picking_can_skip_middle_inspection():
    workflow = WorkflowController()
    workflow.start_work([{"part_no": "B001", "quantity": 2}])
    assert workflow.tray_arrived()["state"] == "PICKING"
    assert workflow.picking_complete()["state"] == "ITEM_COMPLETE"
    assert workflow.next_item()["state"] == "FINAL_VERIFICATION"


def test_temporary_weights_are_configured_for_all_parts():
    catalog = load_parts_catalog(backend_dir / "config" / "parts.yaml")
    expected = {
        "flange_nut": 10.0,
        "t_bolt": 20.0,
        "socket_head_bolt": 30.0,
        "corner_bracket": 40.0,
        "t_nut": 50.0,
        "l_bracket": 60.0,
    }
    assert {
        key: float(value["weight_g"])
        for key, value in catalog.items()
    } == expected


def test_mock_final_weight_uses_expected_total(monkeypatch):
    import server as backend_server

    class FakeFinalLoadCell:
        def __init__(self):
            self.weight = 0.0
        def set_mock_weight(self, weight_g):
            self.weight = float(weight_g)
        def read_weight(self):
            return {"success": True, "weight_g": self.weight, "mock": True}

    fake = FakeFinalLoadCell()
    monkeypatch.setattr(backend_server, "FINAL_LOADCELL_MODE", "mock")
    monkeypatch.setattr(backend_server, "MOCK_FINAL_WEIGHT_OFFSET_G", 0.0)
    monkeypatch.setattr(backend_server, "FINAL_VISION_ENABLED", False)
    monkeypatch.setattr(backend_server, "final_loadcell", fake)
    monkeypatch.setattr(
        backend_server,
        "parts_catalog",
        load_parts_catalog(backend_dir / "config" / "parts.yaml"),
    )

    request = backend_server.FinalVerificationRequest(
        items=[
            backend_server.FinalVerificationItem(part_no="B001", quantity=2),
            backend_server.FinalVerificationItem(part_no="W001", quantity=1),
        ],
        tolerance_g=5.0,
    )
    result = backend_server.final_verification_check(request)
    assert result["success"] is True
    assert result["passed"] is True
    assert result["expected_weight_g"] == 90.0
    assert result["measured_weight_g"] == 90.0


def test_mock_final_weight_offset_can_force_fail(monkeypatch):
    import server as backend_server

    class FakeFinalLoadCell:
        def __init__(self):
            self.weight = 0.0
        def set_mock_weight(self, weight_g):
            self.weight = float(weight_g)
        def read_weight(self):
            return {"success": True, "weight_g": self.weight, "mock": True}

    fake = FakeFinalLoadCell()
    monkeypatch.setattr(backend_server, "FINAL_LOADCELL_MODE", "mock")
    monkeypatch.setattr(backend_server, "MOCK_FINAL_WEIGHT_OFFSET_G", 10.0)
    monkeypatch.setattr(backend_server, "FINAL_VISION_ENABLED", False)
    monkeypatch.setattr(backend_server, "final_loadcell", fake)
    monkeypatch.setattr(
        backend_server,
        "parts_catalog",
        load_parts_catalog(backend_dir / "config" / "parts.yaml"),
    )

    request = backend_server.FinalVerificationRequest(
        items=[backend_server.FinalVerificationItem(part_no="N001", quantity=1)],
        tolerance_g=5.0,
    )
    result = backend_server.final_verification_check(request)
    assert result["success"] is True
    assert result["passed"] is False
    assert result["difference_g"] == 10.0
