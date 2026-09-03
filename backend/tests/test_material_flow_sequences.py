from __future__ import annotations

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from adapters.mock_gripper_adapter import MockGripperAdapter
from adapters.mock_gripper_stepper_adapter import MockGripperStepperAdapter
from adapters.mock_loadcell_adapter import MockLoadCellAdapter
from adapters.mock_stage_adapter import MockStageAdapter
from workflow.material_flow_controller import MaterialFlowController
from workflow.material_flow_executor import MaterialFlowExecutor


def _build_executor():
    controller = MaterialFlowController()
    stage = MockStageAdapter()
    assert stage.home()["success"] is True
    loadcell = MockLoadCellAdapter()
    gripper = MockGripperAdapter(loadcell=loadcell)
    stepper = MockGripperStepperAdapter()
    executor = MaterialFlowExecutor(
        material_flow=controller,
        stage=stage,
        gripper=gripper,
        loadcell=loadcell,
        gripper_stepper=stepper,
        alignment_callback=None,
        grip_settle_sec=0.0,
        stepper_settle_sec=0.0,
    )
    return controller, stage, executor


def test_full_supply_and_return_cycle_with_alignment_disabled():
    controller, stage, executor = _build_executor()

    status = controller.start([
        {"tray": 1},
        {"tray": "TRAY 02"},
        {"tray": 1},
    ])
    assert status["supply_queue"] == [1, 2]

    assert executor.supply_current_tray()["success"] is True
    assert executor.supply_current_tray()["success"] is True
    assert controller.get_status()["supply_complete"] is True
    assert controller.get_status()["supplied_trays"] == [1, 2]

    # 회수 순서는 고정하지 않는다.
    controller.enqueue_return(2)
    controller.enqueue_return(1)

    assert executor.return_current_tray(2)["success"] is True
    assert executor.return_current_tray(1)["success"] is True
    assert controller.get_status()["all_returned"] is True
    assert controller.get_status()["returned_trays"] == [2, 1]
    assert stage.current_tray is None


def test_aborted_material_flow_does_not_start_supply_motion():
    controller, stage, executor = _build_executor()
    controller.start([{"tray": 1}])
    controller.abort()

    result = executor.supply_current_tray()

    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["step"] == "START"
    assert stage.current_tray is None
