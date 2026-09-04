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


def test_weights_are_configured_for_all_parts():
    catalog = load_parts_catalog(backend_dir / "config" / "parts.yaml")

    for class_key, config in catalog.items():
        weight_g = config.get("weight_g")
        assert weight_g is not None, f"{class_key}.weight_g가 등록되어야 합니다."
        assert float(weight_g) > 0.0, f"{class_key}.weight_g는 0보다 커야 합니다."

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
        tray_id=1,
        items=[
            backend_server.FinalVerificationItem(part_no="B001", quantity=2),
            backend_server.FinalVerificationItem(part_no="W001", quantity=1),
        ],
        tolerance_g=5.0,
    )
    result = backend_server.final_verification_check(request)
    assert result["success"] is True
    assert result["passed"] is True
    expected_parts = (
        float(backend_server.parts_catalog["t_bolt"]["weight_g"]) * 2
        + float(backend_server.parts_catalog["t_nut"]["weight_g"])
    )
    assert result["expected_parts_weight_g"] == expected_parts
    assert result["measured_parts_weight_g"] == expected_parts
    assert result["measured_total_weight_g"] == (
        float(backend_server.FINAL_TRAY_WEIGHT_G) + expected_parts
    )


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
        tray_id=4,
        items=[backend_server.FinalVerificationItem(part_no="N001", quantity=1)],
        tolerance_g=5.0,
    )
    result = backend_server.final_verification_check(request)
    assert result["success"] is True
    assert result["passed"] is False
    assert result["difference_g"] == 10.0
