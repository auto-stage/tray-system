from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.relocation import build_relocation_plan
from services.inventory import (
    get_all_inventory,
    get_item,
    consume_inventory,
)
from services.work_history import (
    get_history,
    add_history,
)

from adapters.mock_stage_adapter import MockStageAdapter
from adapters.mock_vision_adapter import MockVisionAdapter
from adapters.mock_loadcell_adapter import MockLoadCellAdapter
from adapters.mock_part_inspection_adapter import MockPartInspectionAdapter
from adapters.work_order_camera_adapter import WorkOrderCameraAdapter
from services.inspection_service import InspectionService
from services.aruco_alignment_mode import load_alignment_mode
from workflow.workflow_controller import WorkflowController
from workflow.material_flow_controller import MaterialFlowController
from workflow.material_flow_executor import MaterialFlowExecutor
from parts_db import (
    find_part,
    find_part_by_identifier,
    load_parts_catalog,
)

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import shutil
import os
import yaml
import cv2
import uuid


# ============================================================
# 기본 경로
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

ARUCO_SYSTEM_CONFIG_PATH = (
    BASE_DIR.parent
    / "modules"
    / "aruco_tray_vision"
    / "config"
    / "system.yaml"
)

ARUCO_ALIGNMENT_MODE = load_alignment_mode(
    ARUCO_SYSTEM_CONFIG_PATH,
    override=os.getenv("ARUCO_ALIGNMENT_MODE"),
)

print("[ARUCO ALIGNMENT]", f"mode={ARUCO_ALIGNMENT_MODE}")


def build_material_flow_alignment_callback():
    if ARUCO_ALIGNMENT_MODE == "disabled":
        return None

    # observe_only / closed_loop 모두 단일 callback으로 라우팅하고,
    # 실제 모드 분기는 material_flow_alignment_callback() 내부에서 수행한다.
    return lambda tray_id: material_flow_alignment_callback(tray_id)


UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

RACK_LAYOUT_PATH = DATA_DIR / "rack_layout.json"
PARTS_CONFIG_PATH = BASE_DIR / "config" / "parts.yaml"
CAMERAS_CONFIG_PATH = BASE_DIR / "config" / "cameras.yaml"
PART_CLASSIFIER_PROFILE_PATH = DATA_DIR / "part_classifier_profile.json"
PART_CAPTURE_ROOT = BASE_DIR.parent / "captures" / "part_inspection"


def load_camera_role_config() -> dict:
    if not CAMERAS_CONFIG_PATH.is_file():
        return {}
    with CAMERAS_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    cameras = raw.get("cameras", {})
    return cameras if isinstance(cameras, dict) else {}


CAMERA_ROLE_CONFIG = load_camera_role_config()
ARUCO_ROLE_CONFIG = dict(CAMERA_ROLE_CONFIG.get("aruco", {}) or {})
WORK_ORDER_ROLE_CONFIG = dict(CAMERA_ROLE_CONFIG.get("work_order", {}) or {})
WORK_ORDER_OCR_CONFIG = dict(WORK_ORDER_ROLE_CONFIG.get("ocr", {}) or {})
WORK_ORDER_INSPECTION_CONFIG = dict(
    WORK_ORDER_ROLE_CONFIG.get("inspection", {}) or {}
)
YOLO_CONFIG = dict(WORK_ORDER_INSPECTION_CONFIG.get("yolo", {}) or {})
YOLO_DATA_ROOT = (BASE_DIR.parent / str(YOLO_CONFIG.get("dataset_root", "data/part_yolo"))).resolve()
work_order_ocr_runtime = {
    "enabled": bool(WORK_ORDER_OCR_CONFIG.get("enabled", True)),
    "status": "idle",
    "last_result": None,
    "last_error": None,
}
part_inspection_enabled = bool(WORK_ORDER_INSPECTION_CONFIG.get("enabled", True))


def camera_identity(source):
    if isinstance(source, int):
        source = f"/dev/video{source}"
    return os.path.realpath(str(source))


def optional_bool_env(
    name: str,
    default: str,
) -> bool | None:
    raw = os.getenv(
        name,
        default,
    ).strip().lower()
    if raw in {
        "",
        "none",
        "null",
    }:
        return None
    if raw in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True
    if raw in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False
    raise ValueError(
        f"{name} must be true, false, or none"
    )


def optional_float_env(
    name: str,
    default: str,
) -> float | None:
    raw = os.getenv(
        name,
        default,
    ).strip()
    if raw.lower() in {
        "",
        "none",
        "null",
    }:
        return None
    return float(raw)


# ============================================================
# FastAPI
# ============================================================

app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8443",
        "http://127.0.0.1:8443",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# 현재 장치 연결
# ============================================================
#
# 지금은 실제 STM32 / 실제 Vision 장비가 없으므로 Mock 사용.
#
# 나중 실제 STM32 연결 시:
#
# from adapters.stm32_stage_adapter import STM32StageAdapter
#
# stage = STM32StageAdapter(
#     port="COM3",
#     baudrate=115200,
# )
#
# 로 교체하면 됨.
# ============================================================

STAGE_MODE = os.getenv(
    "STAGE_MODE",
    "mock",
).strip().lower()

if STAGE_MODE == "stm32":

    # pyserial is only required when real STM32 mode is selected.
    # Mock development can therefore run before the Stage serial stack is installed.
    from adapters.stm32_stage_adapter import STM32StageAdapter

    stage = STM32StageAdapter(
        port=(
            os.getenv(
                "STAGE_SERIAL_PORT"
            )
            or None
        ),
        baudrate=115200,
    )

    print(
        "[STAGE] 실제 STM32 모드"
    )

else:

    stage = MockStageAdapter()

    print(
        "[STAGE] MOCK 모드"
    )

# Tray ArUco 검출용 Vision
VISION_MODE = os.getenv(
    "VISION_MODE",
    "mock",
).strip().lower()
VISION_STREAM_FPS = float(
    os.getenv(
        "VISION_STREAM_FPS",
        "15",
    )
)

if VISION_MODE == "aruco":

    # 실제 ArUco 모드에서만 의존성을 불러온다.
    from adapters.aruco_vision_adapter import ArucoVisionAdapter

    camera_index_raw = os.getenv(
        "VISION_CAMERA_INDEX"
    )
    vision_camera_device = (
        os.getenv(
            "VISION_CAMERA_DEVICE"
        )
        or None
    )

    aruco_vision = ArucoVisionAdapter(
        camera_index=(
            int(camera_index_raw)
            if camera_index_raw is not None
            else None
        ),
        camera_device=vision_camera_device,
        camera_profile=(
            os.getenv(
                "VISION_CAMERA_PROFILE"
            )
            or ARUCO_ROLE_CONFIG.get("profile")
            or None
        ),
        width=int(
            os.getenv(
                "VISION_CAMERA_WIDTH",
                "1280",
            )
        ),
        height=int(
            os.getenv(
                "VISION_CAMERA_HEIGHT",
                "720",
            )
        ),
        fps=float(
            os.getenv(
                "VISION_CAMERA_FPS",
                "30",
            )
        ),
        fourcc=os.getenv(
            "VISION_CAMERA_FOURCC",
            "MJPG",
        ),
        autofocus=optional_bool_env(
            "VISION_CAMERA_AUTOFOCUS",
            "false",
        ),
        focus=optional_float_env(
            "VISION_CAMERA_FOCUS",
            "50",
        ),
    )

    print(
        "[VISION] 실제 ArUco 모드",
        f"source={aruco_vision.camera_source}",
        f"capture={aruco_vision.requested_capture}",
    )

else:

    aruco_vision = MockVisionAdapter()

    print(
        "[VISION] MOCK 모드"
    )

aruco_camera_enabled = bool(
    VISION_MODE == "aruco" and ARUCO_ROLE_CONFIG.get("enabled", True)
)

workflow = WorkflowController()
material_flow = MaterialFlowController()

# 작업지시 OCR과 Part Inspection이 공유하는 고정 카메라.
# ArUco 이동부 카메라와는 별도 role/config/device로 관리한다.
WORK_ORDER_CAMERA_MODE = os.getenv(
    "WORK_ORDER_CAMERA_MODE",
    (
        "camera"
        if WORK_ORDER_ROLE_CONFIG.get("enabled", True)
        else "off"
    ),
).strip().lower()
WORK_ORDER_CAMERA_STREAM_FPS = float(
    os.getenv(
        "WORK_ORDER_CAMERA_STREAM_FPS",
        "15",
    )
)

def create_work_order_camera(
    *,
    camera_index: int | None = None,
    camera_device: str | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    fourcc: str | None = None,
) -> WorkOrderCameraAdapter:
    work_order_camera_index_raw = os.getenv(
        "WORK_ORDER_CAMERA_INDEX"
    )
    configured_device = os.getenv("WORK_ORDER_CAMERA_DEVICE") or None
    profile_name = (
        os.getenv("WORK_ORDER_CAMERA_PROFILE")
        or WORK_ORDER_ROLE_CONFIG.get("profile")
        or None
    )
    return WorkOrderCameraAdapter(
        camera_index=(
            camera_index
            if camera_index is not None
            else int(work_order_camera_index_raw)
            if work_order_camera_index_raw is not None
            else None
        ),
        camera_device=camera_device or configured_device,
        camera_profile=profile_name,
        width=(
            width
            if width is not None
            else
            int(os.environ[
                "WORK_ORDER_CAMERA_WIDTH"
            ])
            if "WORK_ORDER_CAMERA_WIDTH"
            in os.environ
            else None
        ),
        height=(
            height
            if height is not None
            else
            int(os.environ[
                "WORK_ORDER_CAMERA_HEIGHT"
            ])
            if "WORK_ORDER_CAMERA_HEIGHT"
            in os.environ
            else None
        ),
        fps=(
            fps
            if fps is not None
            else
            float(os.environ[
                "WORK_ORDER_CAMERA_FPS"
            ])
            if "WORK_ORDER_CAMERA_FPS"
            in os.environ
            else None
        ),
        fourcc=(
            fourcc
            if fourcc is not None
            else
            os.environ[
                "WORK_ORDER_CAMERA_FOURCC"
            ]
            if "WORK_ORDER_CAMERA_FOURCC"
            in os.environ
            else None
        ),
    )


work_order_camera_enabled = WORK_ORDER_CAMERA_MODE == "camera"
work_order_camera = (
    create_work_order_camera()
    if work_order_camera_enabled
    else None
)

if work_order_camera is not None:

    print(
        "[WORK ORDER CAMERA] 실제 카메라 모드",
        f"source={work_order_camera.camera_source}",
        f"capture={work_order_camera.requested_capture}",
    )

    if VISION_MODE == "aruco" and (
        camera_identity(
            aruco_vision.camera_source
        )
        == camera_identity(
            work_order_camera.camera_source
        )
    ):
        print(
            "[WARNING] 작업지시서 카메라와 ArUco 카메라가 "
            "같은 물리 device를 사용합니다. 최종 2-camera 운용에서는 "
            "서로 다른 device를 지정하세요."
        )
else:
    print("[WORK ORDER / INSPECTION CAMERA] 비활성 상태 (UI에서 선택 가능)")


@app.on_event("startup")
def initialize_aruco_camera():
    global aruco_camera_enabled
    if VISION_MODE != "aruco":
        return
    aruco_camera_enabled = True

    try:
        status = (
            aruco_vision.get_camera_status()
        )
    except Exception as error:
        print(
            "[VISION CAMERA WARNING] Startup camera "
            f"initialization failed: {error}"
        )
        return

    if status.get("connected"):
        print(
            "[VISION] ArUco camera initialized "
            "during startup",
            f"source={status.get('camera_source')}",
        )
        return

    print(
        "[VISION CAMERA WARNING] Startup camera "
        "initialization did not connect; later requests "
        "will retry",
        f"error={status.get('last_error')}",
    )


@app.on_event("startup")
def initialize_work_order_camera():
    if work_order_camera is None:
        return

    try:
        status = work_order_camera.get_status()
    except Exception as error:
        print(
            "[WORK ORDER CAMERA WARNING] Startup camera "
            f"initialization failed: {error}"
        )
        return

    if status.get("connected"):
        print(
            "[WORK ORDER CAMERA] Camera initialized "
            "during startup",
            f"source={status.get('camera_source')}",
        )
        return

    print(
        "[WORK ORDER CAMERA WARNING] Startup camera "
        "initialization did not connect; capture worker "
        "will retry",
        f"error={status.get('error')}",
    )


@app.on_event("shutdown")
def close_camera_adapters():
    close_aruco = getattr(
        aruco_vision,
        "close",
        None,
    )
    if callable(close_aruco):
        close_aruco()

    if work_order_camera is not None:
        work_order_camera.close()

    yolo_adapter = globals().get("yolo_vision")
    if yolo_adapter is not None:
        yolo_adapter.close()


# ============================================================
# 부품 검수 / Load Cell
# ============================================================
#
# OpenCV Adapter는 공유 고정 카메라의 실제 frame만 Reference로 저장한다.
# Mock은 API/UI 구조 확인용이며 실제 검증 통계에는 절대 포함하지 않는다.
# ============================================================

LOADCELL_MODE = os.getenv(
    "LOADCELL_MODE",
    "mock",
).strip().lower()

if LOADCELL_MODE == "stm32":
    if STAGE_MODE != "stm32":
        raise RuntimeError(
            "LOADCELL_MODE=stm32 사용 시 "
            "STAGE_MODE=stm32도 함께 설정해야 합니다."
        )

    from adapters.stm32_loadcell_adapter import STM32LoadCellAdapter

    loadcell = STM32LoadCellAdapter(
        stage=stage,
    )

    print(
        "[LOAD CELL] STM32 HX711 실제 모드"
    )

elif LOADCELL_MODE == "mock":
    loadcell = MockLoadCellAdapter()
    print(
        "[LOAD CELL] MOCK 모드"
    )
else:
    raise RuntimeError(
        f"지원하지 않는 LOADCELL_MODE: {LOADCELL_MODE}"
    )


# ============================================================
# 최종 검수 박스 Load Cell / HX711 #2
#
# 기존 캐리지 Load Cell과는 완전히 별도 장치이다.
# 기본 모드는 LOADCELL_MODE를 따라가며 필요 시
# FINAL_LOADCELL_MODE 환경변수로 독립 설정할 수 있다.
# ============================================================

FINAL_LOADCELL_MODE = os.getenv(
    "FINAL_LOADCELL_MODE",
    LOADCELL_MODE,
).strip().lower()

# Mock 최종 중량 검수에서 PASS/FAIL 시나리오를 만들기 위한 오프셋.
# 0g이면 예상 중량과 동일(PASS), 예: 10g이면 tolerance 5g 기준 FAIL.
try:
    MOCK_FINAL_WEIGHT_OFFSET_G = float(
        os.getenv("MOCK_FINAL_WEIGHT_OFFSET_G", "0")
    )
except ValueError as error:
    raise RuntimeError(
        "MOCK_FINAL_WEIGHT_OFFSET_G는 숫자여야 합니다."
    ) from error

if FINAL_LOADCELL_MODE == "stm32":
    if STAGE_MODE != "stm32":
        raise RuntimeError(
            "FINAL_LOADCELL_MODE=stm32 사용 시 "
            "STAGE_MODE=stm32도 함께 설정해야 합니다."
        )

    from adapters.stm32_final_loadcell_adapter import (
        STM32FinalLoadCellAdapter,
    )

    final_loadcell = STM32FinalLoadCellAdapter(
        stage=stage,
    )

    print(
        "[FINAL LOAD CELL] STM32 HX711 #2 실제 모드"
    )

elif FINAL_LOADCELL_MODE == "mock":
    from adapters.mock_final_loadcell_adapter import (
        MockFinalLoadCellAdapter,
    )

    final_loadcell = MockFinalLoadCellAdapter()

    print(
        "[FINAL LOAD CELL] MOCK 모드"
    )

else:
    raise RuntimeError(
        "지원하지 않는 FINAL_LOADCELL_MODE: "
        f"{FINAL_LOADCELL_MODE}"
    )


GRIPPER_SERVO_BYPASS = (
    os.getenv("GRIPPER_SERVO_BYPASS", "0")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

print(
    "[MATERIAL FLOW] "
    f"gripper_servo_bypass={'ON' if GRIPPER_SERVO_BYPASS else 'OFF'}"
)


# ============================================================
# Material Flow 장치 구성
#
# UI / Executor는 실제 장비와 Mock 장비를 구분하지 않는다.
# Adapter만 실행 모드에 따라 교체한다.
# ============================================================

gripper = None
gripper_stepper = None
material_flow_executor = None


if (
    STAGE_MODE == "stm32"
    and LOADCELL_MODE == "stm32"
):

    from adapters.stm32_gripper_adapter import STM32GripperAdapter
    from adapters.stm32_gripper_stepper_adapter import STM32GripperStepperAdapter

    # Stage / Servo / Load Cell / Stepper는
    # 하나의 실제 STM32 Serial transport를 공유한다.
    gripper = STM32GripperAdapter(
        stage=stage,
    )
    gripper_stepper = STM32GripperStepperAdapter(
        stage=stage,
    )

    material_flow_executor = MaterialFlowExecutor(
        material_flow=material_flow,
        stage=stage,
        gripper=gripper,
        loadcell=loadcell,
        gripper_stepper=gripper_stepper,
        gripper_servo_bypass=GRIPPER_SERVO_BYPASS,
        # 실제 Stage에서는 ArUco 정렬을 BYPASS하지 않는다.
        # 함수 정의는 파일 뒤쪽에 있지만 callback은 실행 시점에 조회된다.
        alignment_callback=build_material_flow_alignment_callback(),
    )

    print(
        "[MATERIAL FLOW] "
        "STM32 실제 Executor 활성"
    )


elif (
    STAGE_MODE == "mock"
    and LOADCELL_MODE == "mock"
):

    from adapters.mock_gripper_adapter import MockGripperAdapter
    from adapters.mock_gripper_stepper_adapter import MockGripperStepperAdapter

    gripper = MockGripperAdapter(
        loadcell=loadcell,
    )

    gripper_stepper = MockGripperStepperAdapter()

    material_flow_executor = MaterialFlowExecutor(
        material_flow=material_flow,
        stage=stage,
        gripper=gripper,
        loadcell=loadcell,
        gripper_stepper=gripper_stepper,
        gripper_servo_bypass=GRIPPER_SERVO_BYPASS,
        # Mock 장치 시험은 기존 BYPASS를 유지하되, 실제 ArUco 카메라를
        # 선택한 경우에는 동일한 정렬 callback으로 통합 시퀀스를 검증한다.
        alignment_callback=build_material_flow_alignment_callback(),
    )

    print(
        "[MATERIAL FLOW] "
        "MOCK Executor 활성"
    )


else:

    print(
        "[MATERIAL FLOW] "
        "Stage/LoadCell 혼합 모드이므로 "
        "Executor 비활성",
        f"STAGE_MODE={STAGE_MODE}",
        f"LOADCELL_MODE={LOADCELL_MODE}",
    )

PART_INSPECTION_MODE = os.getenv(
    "PART_INSPECTION_MODE",
    str(WORK_ORDER_INSPECTION_CONFIG.get("detector", "opencv_baseline")),
).strip().lower()

# 현재 운용은 작업자가 화면의 지시 수량만큼 직접 피킹한다.
# 모델의 정확한 개수 계수 능력이 확보될 때까지 중간 검수는 기본 OFF.
MID_INSPECTION_ENABLED = (
    os.getenv("MID_INSPECTION_ENABLED", "0")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

TRAY_VISION_ENABLED = (
    os.getenv("TRAY_VISION_ENABLED", "0")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

FINAL_VISION_ENABLED = (
    os.getenv("FINAL_VISION_ENABLED", "0")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)

print(
    "[INSPECTION CONFIG]",
    f"mid_inspection={'ON' if MID_INSPECTION_ENABLED else 'OFF'}",
    f"tray_vision={'ON' if TRAY_VISION_ENABLED else 'OFF'}",
    f"final_vision={'ON' if FINAL_VISION_ENABLED else 'OFF'}",
)


def read_work_order_inspection_frame(copy: bool = True):
    """Shared C920 frame; no inspection consumer owns a VideoCapture."""
    if work_order_camera is None or not work_order_camera_enabled:
        return None
    return work_order_camera.read_frame(copy=copy)


from adapters.yolo_part_inspection_adapter import YoloModelError, YoloPartInspectionAdapter
from services.yolo_dataset import YoloDatasetError, YoloDatasetService
from services.yolo_training import YoloTrainingError, YoloTrainingService

parts_catalog = load_parts_catalog(PARTS_CONFIG_PATH)
yolo_vision = YoloPartInspectionAdapter(
    frame_source=read_work_order_inspection_frame,
    parts=parts_catalog,
    confidence_threshold=float(YOLO_CONFIG.get("confidence_threshold", 0.25)),
    max_inference_fps=float(YOLO_CONFIG.get("max_inference_fps", 2.0)),
    validation_state_path=YOLO_DATA_ROOT / "state" / "validation.json",
)
yolo_dataset = YoloDatasetService(
    root=YOLO_DATA_ROOT,
    frame_source=read_work_order_inspection_frame,
    parts=parts_catalog,
)
yolo_training = YoloTrainingService(
    root=YOLO_DATA_ROOT,
    worker_path=BASE_DIR / "yolo_train_worker.py",
)

if PART_INSPECTION_MODE == "mock":
    part_vision = MockPartInspectionAdapter()
    print("[PART INSPECTION] MOCK 모드")
elif PART_INSPECTION_MODE in {"opencv", "opencv_baseline"}:
    from adapters.opencv_part_inspection_adapter import OpenCVPartInspectionAdapter

    part_vision = OpenCVPartInspectionAdapter(
        frame_source=read_work_order_inspection_frame,
        parts=parts_catalog,
        state_path=PART_CLASSIFIER_PROFILE_PATH,
        capture_root=PART_CAPTURE_ROOT,
        roi=WORK_ORDER_INSPECTION_CONFIG.get("roi"),
    )
    print("[PART INSPECTION] OpenCV baseline 모드")
elif PART_INSPECTION_MODE == "yolo":
    part_vision = yolo_vision
    active_model = yolo_training.active_model_path()
    if active_model is not None:
        try:
            yolo_vision.load_model(active_model)
        except Exception as error:
            print(f"[PART INSPECTION WARNING] Active YOLO model load failed: {error}")
    print("[PART INSPECTION] YOLO 모드")
else:
    raise RuntimeError(
        "PART_INSPECTION_MODE는 mock, opencv_baseline 또는 yolo여야 합니다."
    )

inspection_service = InspectionService(
    loadcell=loadcell,
    part_vision=part_vision,
    parts_config_path=PARTS_CONFIG_PATH,
)


# ============================================================
# Rack Layout 저장 / 복원
# ============================================================
DEFAULT_RACK_LAYOUT = [
    "TRAY 05",
    "TRAY 06",
    "TRAY 03",
    "TRAY 04",
    "TRAY 01",
    "TRAY 02",
]


def load_rack_layout():
    """
    마지막으로 저장된 실제 Rack 배치를 읽는다.

    파일이 없으면 기본 배치를 생성한다.
    """

    if not RACK_LAYOUT_PATH.exists():

        data = {
            "slots": DEFAULT_RACK_LAYOUT
        }

        with open(
            RACK_LAYOUT_PATH,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        return data

    try:

        with open(
            RACK_LAYOUT_PATH,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

    except (
        json.JSONDecodeError,
        OSError,
    ):

        data = {
            "slots": DEFAULT_RACK_LAYOUT
        }

    slots = data.get(
        "slots",
        DEFAULT_RACK_LAYOUT
    )

    if (
        not isinstance(slots, list)
        or len(slots) != 6
    ):

        slots = DEFAULT_RACK_LAYOUT

    return {
        "slots": slots
    }


def save_rack_layout(
    slots: list[str]
):
    """
    현재 Rack 배치를 JSON 파일에 저장한다.

    프로그램을 껐다 켜도 이 값을 다시 불러온다.
    """

    data = {
        "slots": slots
    }

    with open(
        RACK_LAYOUT_PATH,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return data


class RackLayoutRequest(BaseModel):
    slots: list[str]


@app.get("/rack-layout")
def get_rack_layout():

    return {
        "success": True,
        "data": load_rack_layout(),
    }


@app.post("/rack-layout")
def update_rack_layout(
    request: RackLayoutRequest
):

    if len(request.slots) != 6:

        return {
            "success": False,
            "message": (
                "Rack slot은 정확히 6개여야 합니다."
            ),
        }

    if len(set(request.slots)) != 6:

        return {
            "success": False,
            "message": (
                "Rack 배치에 중복 Tray가 있습니다."
            ),
        }

    allowed = {
        "TRAY 01",
        "TRAY 02",
        "TRAY 03",
        "TRAY 04",
        "TRAY 05",
        "TRAY 06",
    }

    if set(request.slots) != allowed:

        return {
            "success": False,
            "message": (
                "TRAY 01~06이 각각 한 번씩 "
                "포함되어야 합니다."
            ),
        }

    saved = save_rack_layout(
        request.slots
    )

    return {
        "success": True,
        "data": saved,
    }


# ============================================================
# 분석 진행상태
# ============================================================

analysis_progress = {}


def set_analysis_progress(
    analysis_id: str,
    step: int,
    message: str,
    done: bool = False,
    error: str | None = None,
):

    analysis_progress[analysis_id] = {
        "step": step,
        "message": message,
        "done": done,
        "error": error,
    }


def run_ocr_process(
    analysis_id: str,
    ocr_script: Path,
    save_path: Path,
):
    """
    Windows에서도 안정적으로 OCR 프로세스를 실행한다.

    work_order_ocr.py stdout의:

        __PROGRESS__:N:메시지

    형식을 읽어서 실제 진행률을 UI에 전달한다.
    """

    process = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ocr_script),
            str(save_path),
        ],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    assert process.stdout is not None

    for raw_line in process.stdout:

        line = raw_line.rstrip()

        # OCR 로그를 Python 터미널에도 그대로 표시
        print(
            line,
            flush=True,
        )

        if line.startswith(
            "__PROGRESS__:"
        ):

            parts = line.split(
                ":",
                2
            )

            if len(parts) == 3:

                try:

                    step = int(
                        parts[1]
                    )

                except ValueError:

                    continue

                set_analysis_progress(
                    analysis_id,
                    step,
                    parts[2],
                )

    return process.wait()


# ============================================================
# 기본
# ============================================================

@app.get("/")
def root():

    return {
        "message": "Python backend is running"
    }


# ============================================================
# 작업지시서 고정 카메라 API
# ============================================================

class WorkOrderCameraSelectRequest(BaseModel):
    enabled: bool = True
    camera_index: int | None = None
    camera_device: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    fourcc: str | None = None


class CameraEnabledRequest(BaseModel):
    enabled: bool


class WorkOrderFeatureEnabledRequest(BaseModel):
    feature: str
    enabled: bool


def work_order_camera_resource_warning() -> str | None:
    if (
        VISION_MODE == "aruco"
        and work_order_camera is not None
        and camera_identity(aruco_vision.camera_source)
        == camera_identity(work_order_camera.camera_source)
    ):
        return (
            "ArUco / Stage Camera와 Work Order / Inspection Camera가 "
            "같은 물리 device를 가리킵니다. 서로 다른 source를 선택하세요."
        )
    return None


def disabled_work_order_camera_status() -> dict:
    requested = (
        dict(work_order_camera.requested_capture)
        if work_order_camera is not None
        else {}
    )
    return {
        "connected": False,
        "enabled": False,
        "mode": "off",
        "role": "work_order_inspection",
        "camera_index": getattr(work_order_camera, "camera_index", None),
        "camera_device": getattr(work_order_camera, "camera_device", None),
        "camera_source": getattr(work_order_camera, "camera_source", None),
        "requested_capture": requested,
        "effective_capture": {},
        "available_sources": WorkOrderCameraAdapter.discover_camera_sources(),
        "shared_consumers": ["ocr", "part_inspection"],
        "ocr": dict(work_order_ocr_runtime),
        "inspection": {
            **inspection_service.get_status()["part_vision"],
            "enabled": part_inspection_enabled,
        },
        "message": "Camera not connected. UI에서 고정 카메라를 선택하고 Enable 하세요.",
    }

@app.get("/work-order-camera/status")
def work_order_camera_status():
    if work_order_camera is None or not work_order_camera_enabled:
        return disabled_work_order_camera_status()

    status = work_order_camera.get_status()
    return {
        **status,
        "enabled": True,
        "role": "work_order_inspection",
        "available_sources": WorkOrderCameraAdapter.discover_camera_sources(),
        "shared_consumers": ["ocr", "part_inspection"],
        "ocr": dict(work_order_ocr_runtime),
        "inspection": {
            **inspection_service.get_status()["part_vision"],
            "enabled": part_inspection_enabled,
        },
        "resource_warning": work_order_camera_resource_warning(),
    }


@app.get("/work-order-camera/sources")
def work_order_camera_sources():
    return {
        "success": True,
        "sources": WorkOrderCameraAdapter.discover_camera_sources(),
    }


@app.post("/work-order-camera/select")
def work_order_camera_select(request: WorkOrderCameraSelectRequest):
    global work_order_camera, work_order_camera_enabled

    try:
        if work_order_camera is None:
            work_order_camera = create_work_order_camera(
                camera_index=request.camera_index,
                camera_device=request.camera_device,
                width=request.width,
                height=request.height,
                fps=request.fps,
                fourcc=request.fourcc,
            )
            work_order_camera_enabled = bool(request.enabled)
            connected = (
                work_order_camera.start(wait_timeout=1.0)
                if work_order_camera_enabled
                else False
            )
            result = {
                "success": True,
                "connected": connected,
                "message": "Work Order / Inspection Camera 설정을 적용했습니다.",
            }
        else:
            result = work_order_camera.select_camera(
                camera_index=request.camera_index,
                camera_device=request.camera_device,
                width=request.width,
                height=request.height,
                fps=request.fps,
                fourcc=request.fourcc,
            )
            work_order_camera_enabled = bool(request.enabled)
            if not work_order_camera_enabled:
                work_order_camera.close()
                result["connected"] = False
        result["enabled"] = work_order_camera_enabled
        result["resource_warning"] = work_order_camera_resource_warning()
        return result
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/work-order-camera/enabled")
def set_work_order_camera_enabled(request: CameraEnabledRequest):
    global work_order_camera, work_order_camera_enabled

    if request.enabled and work_order_camera is None:
        work_order_camera = create_work_order_camera()
    work_order_camera_enabled = bool(request.enabled)
    if work_order_camera is not None:
        if work_order_camera_enabled:
            work_order_camera.start(wait_timeout=1.0)
        else:
            work_order_camera.close()
    return work_order_camera_status()


@app.post("/work-order-camera/feature-enabled")
def set_work_order_feature_enabled(request: WorkOrderFeatureEnabledRequest):
    global part_inspection_enabled
    feature = request.feature.strip().lower()
    if feature == "ocr":
        work_order_ocr_runtime["enabled"] = bool(request.enabled)
    elif feature in {"inspection", "part_inspection"}:
        part_inspection_enabled = bool(request.enabled)
    else:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 feature입니다: {feature}")
    return work_order_camera_status()


@app.get("/work-order-camera/stream")
def work_order_camera_stream():
    if work_order_camera is None or not work_order_camera_enabled:
        raise HTTPException(
            status_code=503,
            detail="작업지시서 카메라가 비활성화되어 있습니다.",
        )

    return StreamingResponse(
        work_order_camera.iter_mjpeg(
            max_fps=(
                WORK_ORDER_CAMERA_STREAM_FPS
            ),
        ),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )


@app.get("/work-order-camera/snapshot")
def work_order_camera_snapshot():
    if work_order_camera is None or not work_order_camera_enabled:
        raise HTTPException(
            status_code=503,
            detail="작업지시서 카메라가 비활성화되어 있습니다.",
        )

    jpeg = work_order_camera.get_jpeg_frame(jpeg_quality=94)
    if jpeg is None:
        raise HTTPException(
            status_code=503,
            detail="작업지시서 카메라 프레임을 읽지 못했습니다.",
        )

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ============================================================
# 작업지시서 실제 진행상태 조회
# ============================================================

@app.get(
    "/analysis-progress/{analysis_id}"
)
def get_analysis_progress(
    analysis_id: str
):

    return analysis_progress.get(
        analysis_id,
        {
            "step": 0,
            "message": "분석 시작 대기",
            "done": False,
            "error": None,
        },
    )


# ============================================================
# 작업지시서 분석
# ============================================================

@app.post("/analyze-work-order")
async def analyze_work_order(
    file: UploadFile = File(...),
    analysis_id: str = Form(...),
):
    if not work_order_ocr_runtime["enabled"]:
        return {
            "success": False,
            "message": "Work Order OCR가 비활성화되어 있습니다.",
        }

    work_order_ocr_runtime.update(
        status="running",
        last_error=None,
    )

    save_path = (
        UPLOAD_DIR
        / file.filename
    )

    with open(
        save_path,
        "wb"
    ) as buffer:

        shutil.copyfileobj(
            file.file,
            buffer
        )

    set_analysis_progress(
        analysis_id,
        1,
        "문서 입력 완료",
    )

    print()
    print(
        "======================================"
    )
    print(
        "React UI에서 작업지시서 수신"
    )
    print(
        "파일 :",
        save_path
    )
    print(
        "======================================"
    )
    print()

    ocr_script = (
        BASE_DIR
        / "work_order_ocr.py"
    )

    if not ocr_script.exists():

        message = (
            "work_order_ocr.py를 "
            "찾을 수 없습니다."
        )

        set_analysis_progress(
            analysis_id,
            1,
            "분석 실패",
            done=True,
            error=message,
        )
        work_order_ocr_runtime.update(status="error", last_error=message)

        return {
            "success": False,
            "message": message,
        }

    return_code = (
        await asyncio.to_thread(
            run_ocr_process,
            analysis_id,
            ocr_script,
            save_path,
        )
    )

    if return_code != 0:

        message = (
            "작업지시서 분석 중 "
            "오류가 발생했습니다."
        )

        current_step = (
            analysis_progress
            .get(
                analysis_id,
                {}
            )
            .get(
                "step",
                1
            )
        )

        set_analysis_progress(
            analysis_id,
            current_step,
            "분석 실패",
            done=True,
            error=message,
        )
        work_order_ocr_runtime.update(status="error", last_error=message)

        return {
            "success": False,
            "message": message,
        }

    result_path = (
        BASE_DIR
        / "analysis_result.json"
    )

    if not result_path.exists():

        message = (
            "analysis_result.json이 "
            "생성되지 않았습니다."
        )

        set_analysis_progress(
            analysis_id,
            5,
            "분석 실패",
            done=True,
            error=message,
        )
        work_order_ocr_runtime.update(status="error", last_error=message)

        return {
            "success": False,
            "message": message,
        }

    with open(
        result_path,
        "r",
        encoding="utf-8"
    ) as f:

        analysis_data = (
            json.load(f)
        )

    # --------------------------------------------------------
    # 6단계: 현재 재고 비교
    # --------------------------------------------------------

    inventory = (
        get_all_inventory()
    )

    for item in analysis_data.get(
        "items",
        []
    ):

        part_no = item.get(
            "part_no"
        )

        quantity = item.get(
            "quantity"
        )

        inventory_item = (
            inventory.get(
                part_no
            )
        )

        stock = (
            int(
                inventory_item.get(
                    "stock",
                    0
                )
            )
            if inventory_item
            else 0
        )

        item["stock"] = stock

        if (
            quantity is not None
            and
            int(quantity) > stock
        ):

            item["status"] = "CHECK"
            item[
                "inventory_status"
            ] = "SHORTAGE"

        else:

            item[
                "inventory_status"
            ] = "OK"

    analysis_data["all_ok"] = (
        len(
            analysis_data.get(
                "items",
                []
            )
        ) > 0
        and
        all(
            item.get("status") == "OK"
            and
            item.get(
                "inventory_status"
            ) == "OK"
            for item
            in analysis_data.get(
                "items",
                []
            )
        )
    )

    set_analysis_progress(
        analysis_id,
        6,
        "재고 확인 완료",
        done=True,
    )
    work_order_ocr_runtime.update(
        status="complete",
        last_error=None,
        last_result={
            "item_count": len(analysis_data.get("items", [])),
            "all_ok": bool(analysis_data.get("all_ok")),
        },
    )

    return {
        "success": True,
        "data": analysis_data,
    }


# ============================================================
# REVIEW 수정값 검증
# ============================================================

class ItemValidationRequest(
    BaseModel
):
    part_no: str
    name: str
    spec: str
    quantity: int


@app.post("/validate-item")
def validate_item(
    request: ItemValidationRequest
):

    if request.quantity <= 0:

        return {
            "valid": False,
            "message": (
                "수량은 1개 이상이어야 합니다."
            ),
        }

    matched = find_part(
        request.part_no,
        request.name,
        request.spec,
    )

    if matched is None:

        return {
            "valid": False,
            "message": (
                "등록되지 않은 품목 정보입니다. "
                "품번, 품명, 규격/사양 조합을 "
                "확인해주세요."
            ),
        }

    return {
        "valid": True,
        "message": "등록된 품목입니다.",
        "part": matched,
    }


# ============================================================
# 재고 조회
# ============================================================

@app.get("/inventory")
def inventory_all():

    return {
        "success": True,
        "data": get_all_inventory(),
    }


@app.get(
    "/inventory/{part_no}"
)
def inventory_item(
    part_no: str
):

    item = get_item(
        part_no
    )

    if item is None:

        return {
            "success": False,
            "message": (
                "등록되지 않은 품번입니다."
            ),
        }

    return {
        "success": True,
        "data": item,
    }


# ============================================================
# 작업 최종 완료 → 재고 차감
# ============================================================

class InventoryConsumeItem(
    BaseModel
):
    part_no: str
    quantity: int


class InventoryConsumeRequest(
    BaseModel
):
    work_id: str
    items: list[
        InventoryConsumeItem
    ]


@app.post("/inventory/consume")
def inventory_consume(
    request:
        InventoryConsumeRequest
):

    return consume_inventory(
        work_id=request.work_id,
        items=[
            {
                "part_no":
                    item.part_no,
                "quantity":
                    item.quantity,
            }
            for item
            in request.items
        ],
    )


# ============================================================
# 작업 이력
# ============================================================

class WorkHistoryItem(
    BaseModel
):
    part_no: str
    name: str
    spec: str
    quantity: int
    tray: str


class WorkHistoryCreateRequest(
    BaseModel
):
    work_id: str
    items: list[
        WorkHistoryItem
    ]
    used_trays: list[str]
    result: str = "COMPLETED"
    duration_seconds: int = 0


@app.get("/history")
def history_all():

    return {
        "success": True,
        "data": get_history(),
    }


@app.post("/history")
def history_create(
    request:
        WorkHistoryCreateRequest
):

    return add_history(
        work_id=request.work_id,
        items=[
            {
                "part_no":
                    item.part_no,
                "name":
                    item.name,
                "spec":
                    item.spec,
                "quantity":
                    item.quantity,
                "tray":
                    item.tray,
            }
            for item
            in request.items
        ],
        used_trays=
            request.used_trays,
        result=
            request.result,
        duration_seconds=
            request.duration_seconds,
    )


# ============================================================
# Vision API
# ============================================================

class VisionCountRequest(
    BaseModel
):
    part_no: str
    expected_quantity: int


class CameraSelectRequest(
    BaseModel
):
    profile_name: str
    camera_index: int | None = None


@app.get("/vision/status")
def vision_status():
    if VISION_MODE != "aruco":
        return {
            **aruco_vision.get_camera_status(),
            "enabled": False,
            "role": "aruco_stage",
        }
    if not aruco_camera_enabled:
        return {
            "connected": False,
            "enabled": False,
            "mode": "aruco",
            "role": "aruco_stage",
            "camera_index": getattr(aruco_vision, "camera_index", None),
            "camera_device": getattr(aruco_vision, "camera_device", None),
            "message": "ArUco / Stage Camera가 비활성화되어 있습니다.",
        }
    return {
        **aruco_vision.get_camera_status(),
        "enabled": True,
        "role": "aruco_stage",
    }


@app.post("/vision/camera/enabled")
def set_aruco_camera_enabled(request: CameraEnabledRequest):
    global aruco_camera_enabled
    if VISION_MODE != "aruco" or not hasattr(aruco_vision, "start"):
        raise HTTPException(
            status_code=503,
            detail="VISION_MODE=aruco에서만 ArUco camera Enable을 변경할 수 있습니다.",
        )
    aruco_camera_enabled = bool(request.enabled)
    if aruco_camera_enabled:
        aruco_vision.start(wait_timeout=1.0)
    else:
        aruco_vision.close()
    return vision_status()


@app.get("/vision/stream")
def vision_stream(
    annotate: bool = True,
):

    if not aruco_camera_enabled:
        raise HTTPException(status_code=503, detail="ArUco / Stage Camera가 비활성화되어 있습니다.")

    if not hasattr(
        aruco_vision,
        "iter_mjpeg",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "영상 스트림을 사용할 수 있습니다."
            ),
        )

    return StreamingResponse(
        aruco_vision.iter_mjpeg(
            annotate=annotate,
            max_fps=VISION_STREAM_FPS,
        ),
        media_type=(
            "multipart/x-mixed-replace; "
            "boundary=frame"
        ),
        headers={
            "Cache-Control": (
                "no-store, no-cache, "
                "must-revalidate"
            ),
        },
    )


@app.get("/vision/snapshot")
def vision_snapshot(
    annotate: bool = True,
):

    if not hasattr(
        aruco_vision,
        "get_jpeg_frame",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "스냅샷을 사용할 수 있습니다."
            ),
        )

    jpeg = aruco_vision.get_jpeg_frame(
        jpeg_quality=90,
        annotate=annotate,
    )

    if jpeg is None:
        raise HTTPException(
            status_code=503,
            detail="카메라 프레임을 읽지 못했습니다.",
        )

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
        },
    )


@app.get("/vision/camera/profiles")
def vision_camera_profiles():

    if not hasattr(
        aruco_vision,
        "list_camera_profiles",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "카메라 설정을 사용할 수 있습니다."
            ),
        )

    return (
        aruco_vision.list_camera_profiles()
    )


@app.post("/vision/camera/select")
def vision_camera_select(
    request:
        CameraSelectRequest
):
    global aruco_camera_enabled

    if not hasattr(
        aruco_vision,
        "select_camera",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "카메라 설정을 사용할 수 있습니다."
            ),
        )

    try:
        result = aruco_vision.select_camera(
            profile_name=
                request.profile_name,
            camera_index=
                request.camera_index,
        )
        aruco_camera_enabled = True

        if (
            work_order_camera is not None
            and camera_identity(
                aruco_vision.camera_source
            )
            == camera_identity(
                work_order_camera.camera_source
            )
        ):
            warning = (
                "ArUco / Stage Camera와 Work Order / Inspection Camera가 "
                "같은 물리 device를 사용합니다. 역할별로 서로 다른 "
                "device를 선택하거나 한쪽 Camera를 Disable 하세요."
            )
            print(
                "[WARNING]",
                warning,
            )
            result[
                "resource_warning"
            ] = warning

        return result
    except (
        ValueError,
        FileNotFoundError,
    ) as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


@app.get("/vision/calibration/status")
def vision_calibration_status():

    if not hasattr(
        aruco_vision,
        "get_calibration_status",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "캘리브레이션을 사용할 수 있습니다."
            ),
        )

    return (
        aruco_vision.get_calibration_status()
    )


@app.post("/vision/calibration/sample")
def vision_calibration_sample():

    if not hasattr(
        aruco_vision,
        "add_calibration_sample",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "캘리브레이션을 사용할 수 있습니다."
            ),
        )

    result = (
        aruco_vision.add_calibration_sample()
    )

    if not result.get(
        "success",
        False,
    ):
        return result

    return result


@app.post("/vision/calibration/clear")
def vision_calibration_clear():

    if not hasattr(
        aruco_vision,
        "clear_calibration_samples",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "캘리브레이션을 사용할 수 있습니다."
            ),
        )

    return (
        aruco_vision.clear_calibration_samples()
    )


@app.post("/vision/calibration/run")
def vision_calibration_run():

    if not hasattr(
        aruco_vision,
        "run_intrinsic_calibration",
    ):
        raise HTTPException(
            status_code=503,
            detail=(
                "실제 ArUco 카메라 모드에서만 "
                "캘리브레이션을 사용할 수 있습니다."
            ),
        )

    try:
        return (
            aruco_vision.run_intrinsic_calibration()
        )
    except RuntimeError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error),
        ) from error


class InspectionRunRequest(BaseModel):
    part_no: str | None = None
    class_key: str | None = None
    expected_quantity: int


class InspectionDebugRequest(BaseModel):
    action: str
    class_key: str | None = None
    condition: dict | None = None


class YoloCaptureRequest(BaseModel):
    suggested_class_key: str | None = None
    capture_group: str | None = None
    auto_label: bool = False


class YoloAnnotationRequest(BaseModel):
    boxes: list[dict] = Field(default_factory=list)
    state: str = "MANUAL"


class YoloSplitRequest(BaseModel):
    train_ratio: float = 0.8
    seed: int = 42


class YoloTrainingRequest(BaseModel):
    base_model_id: str
    epochs: int = 50
    image_size: int = 640
    batch: int = 4


class YoloModelActivateRequest(BaseModel):
    model_id: str


class YoloConfidenceRequest(BaseModel):
    confidence_threshold: float


class YoloClassificationTestRequest(BaseModel):
    ground_truth_class_key: str


class FinalLoadCellCalibrationRequest(BaseModel):
    known_weight_g: float = Field(
        gt=0.0,
        description="Calibration 기준추 무게(g)",
    )


@app.get("/final-loadcell/status")
def final_loadcell_status():

    return final_loadcell.get_status()


@app.get("/final-loadcell/raw")
def final_loadcell_raw():

    return final_loadcell.read_raw()


@app.post("/final-loadcell/tare")
def final_loadcell_tare():

    return final_loadcell.tare()


@app.post("/final-loadcell/calibrate")
def final_loadcell_calibrate(
    request: FinalLoadCellCalibrationRequest,
):

    return final_loadcell.calibrate(
        request.known_weight_g
    )


@app.get("/final-loadcell/weight")
def final_loadcell_weight():

    return final_loadcell.read_weight()


class FinalVerificationItem(BaseModel):
    part_no: str
    quantity: int = Field(ge=1)


class FinalVerificationRequest(BaseModel):
    items: list[FinalVerificationItem]
    tolerance_g: float = Field(default=5.0, ge=0.0)


@app.post("/final-verification/check")
def final_verification_check(
    request: FinalVerificationRequest,
):
    """
    작업지시 품목의 예상 총중량과
    최종 박스 HX711 #2 실측 중량을 비교한다.
    """

    if not request.items:
        return {
            "success": False,
            "passed": False,
            "message": "검수할 품목이 없습니다.",
        }

    expected_weight_g = 0.0
    breakdown = []

    for item in request.items:
        part = find_part_by_identifier(
            item.part_no,
            catalog=parts_catalog,
        )

        if part is None:
            return {
                "success": False,
                "passed": False,
                "message": (
                    f"등록되지 않은 품번입니다: "
                    f"{item.part_no}"
                ),
            }

        unit_weight = part.get("weight_g")

        if unit_weight is None:
            return {
                "success": False,
                "passed": False,
                "message": (
                    f"{item.part_no} "
                    "실측 단위중량(weight_g)이 "
                    "등록되지 않았습니다."
                ),
            }

        unit_weight_g = float(unit_weight)

        if unit_weight_g <= 0.0:
            return {
                "success": False,
                "passed": False,
                "message": (
                    f"{item.part_no} "
                    "weight_g 값이 올바르지 않습니다."
                ),
            }

        total_weight_g = (
            unit_weight_g
            * item.quantity
        )

        expected_weight_g += total_weight_g

        breakdown.append({
            "part_no": item.part_no,
            "name": part.get("display_name"),
            "quantity": item.quantity,
            "unit_weight_g": unit_weight_g,
            "expected_weight_g": total_weight_g,
        })

    # Mock 모드에서는 실제 HX711 #2가 없으므로 계산된 예상 중량을
    # Mock 센서에 주입해 UI의 최종 무게 검수 흐름을 실제처럼 검증한다.
    # MOCK_FINAL_WEIGHT_OFFSET_G로 정상/불량 시나리오를 선택할 수 있다.
    if (
        FINAL_LOADCELL_MODE == "mock"
        and hasattr(final_loadcell, "set_mock_weight")
    ):
        final_loadcell.set_mock_weight(
            expected_weight_g + MOCK_FINAL_WEIGHT_OFFSET_G
        )

    measurement = final_loadcell.read_weight()

    if not measurement.get("success"):
        return {
            "success": False,
            "passed": False,
            "message": measurement.get(
                "message",
                "최종 Load Cell 측정 실패",
            ),
            "loadcell": measurement,
        }

    measured_weight_g = float(
        measurement["weight_g"]
    )

    difference_g = abs(
        measured_weight_g
        - expected_weight_g
    )

    loadcell_passed = (
        difference_g
        <= request.tolerance_g
    )

    vision_result = {
        "enabled": False,
        "status": "SKIPPED",
        "passed": None,
        "message": "최종 Vision 검수가 비활성화되어 있습니다.",
    }

    # Vision OFF이면 최종 판정은 Load Cell만 사용한다.
    vision_passed = True

    if FINAL_VISION_ENABLED:
        if PART_INSPECTION_MODE != "yolo":
            vision_result = {
                "enabled": True,
                "status": "ERROR",
                "passed": False,
                "message": (
                    "최종 Vision 검수 ON 상태에서는 "
                    "PART_INSPECTION_MODE=yolo가 필요합니다."
                ),
            }
            vision_passed = False

        else:
            latest = yolo_vision.latest_result()

            if not latest.get("success", False):
                vision_result = {
                    "enabled": True,
                    "status": "ERROR",
                    "passed": False,
                    "message": latest.get(
                        "message",
                        "최종 Vision 결과를 사용할 수 없습니다.",
                    ),
                }
                vision_passed = False

            else:
                detected_counts = {
                    str(key): int(value)
                    for key, value in dict(
                        latest.get("counts", {})
                    ).items()
                }

                expected_counts = {}

                for item in request.items:
                    part = find_part_by_identifier(
                        item.part_no,
                        catalog=parts_catalog,
                    )

                    class_key = part["class_key"]

                    expected_counts[class_key] = (
                        expected_counts.get(class_key, 0)
                        + item.quantity
                    )

                mismatches = []

                for key in sorted(
                    set(expected_counts)
                    | set(detected_counts)
                ):
                    expected = int(
                        expected_counts.get(key, 0)
                    )
                    detected = int(
                        detected_counts.get(key, 0)
                    )

                    if expected != detected:
                        mismatches.append({
                            "class_key": key,
                            "expected": expected,
                            "detected": detected,
                        })

                vision_passed = not mismatches

                vision_result = {
                    "enabled": True,
                    "status": (
                        "PASS"
                        if vision_passed
                        else "FAIL"
                    ),
                    "passed": vision_passed,
                    "expected_counts": expected_counts,
                    "detected_counts": detected_counts,
                    "mismatches": mismatches,
                }

    passed = bool(
        loadcell_passed
        and vision_passed
    )

    return {
        "success": True,
        "passed": passed,
        "expected_weight_g": round(
            expected_weight_g,
            3,
        ),
        "measured_weight_g": round(
            measured_weight_g,
            3,
        ),
        "difference_g": round(
            difference_g,
            3,
        ),
        "tolerance_g": request.tolerance_g,
        "breakdown": breakdown,
        "loadcell": {
            **measurement,
            "enabled": True,
            "passed": loadcell_passed,
        },
        "vision": vision_result,
        "message": (
            "최종 검수 PASS"
            if passed
            else "최종 검수 FAIL"
        ),
    }


@app.get("/loadcell/status")
def loadcell_status():
    return loadcell.get_status()


@app.post("/loadcell/tare")
def loadcell_tare():
    return loadcell.tare()


@app.get("/inspection/config")
def inspection_config():
    return {
        "success": True,
        "mid_inspection_enabled": MID_INSPECTION_ENABLED,
        "tray_vision_enabled": TRAY_VISION_ENABLED,
        "final_vision_enabled": FINAL_VISION_ENABLED,
    }


@app.get("/inspection/status")
def inspection_status():
    return inspection_service.get_status()


@app.get("/inspection/debug/status")
def inspection_debug_status():
    result = inspection_service.get_debug_status()
    result["inspection"] = {
        **result["inspection"],
        "enabled": part_inspection_enabled,
    }
    return result


@app.post("/inspection/debug/run")
def inspection_debug_run(request: InspectionDebugRequest):
    if not part_inspection_enabled:
        return {
            "success": False,
            "status": "error",
            "error": "PART_INSPECTION_DISABLED",
            "message": "Part Inspection이 비활성화되어 있습니다.",
        }
    return inspection_service.debug_action(
        action=request.action,
        class_key=request.class_key,
        condition=request.condition,
    )


@app.get("/inspection/debug/snapshot")
def inspection_debug_snapshot():
    jpeg = inspection_service.get_debug_jpeg()
    if jpeg is None:
        raise HTTPException(
            status_code=404,
            detail="아직 표시할 실제 분류 debug frame이 없습니다.",
        )
    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


# ============================================================
# YOLO UI workflow (real C920 frame only; no Mock data)
# ============================================================

@app.get("/inspection/yolo/status")
def yolo_status():
    return {
        "success": True,
        "part_inspection_mode": PART_INSPECTION_MODE,
        "classes": [
            {
                "class_id": int(config["yolo_class_id"]),
                "class_key": key,
                "display_name": config["display_name"],
            }
            for key, config in sorted(parts_catalog.items(), key=lambda item: int(item[1]["yolo_class_id"]))
        ],
        "dataset": yolo_dataset.validate(),
        "split": yolo_dataset.split_status(),
        "training": yolo_training.status(),
        "base_models": yolo_training.available_base_models(),
        "models": yolo_training.list_models(),
        "inference": yolo_vision.get_status(),
        "runtime": {"device": "cpu", "cuda_available": False, "external_training_recommended": True},
    }


@app.get("/inspection/yolo/images")
def yolo_images():
    return {"success": True, "images": yolo_dataset.list_images()}


@app.get("/inspection/yolo/images/{image_id}")
def yolo_image(image_id: str):
    try:
        return FileResponse(yolo_dataset.image_path(image_id), media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except YoloDatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/inspection/yolo/capture")
def yolo_capture(request: YoloCaptureRequest):
    if work_order_camera is None or not work_order_camera_enabled:
        raise HTTPException(status_code=503, detail="Camera not connected")
    try:
        item = yolo_dataset.capture(
            suggested_class_key=request.suggested_class_key,
            capture_group=request.capture_group,
        )
        auto_result = None
        if request.auto_label:
            frame = cv2.imread(str(yolo_dataset.image_path(item["image_id"])))
            auto_result = yolo_vision.auto_label(frame)
            if auto_result.get("success") and auto_result.get("detections"):
                item = yolo_dataset.save_annotation(
                    item["image_id"],
                    boxes=[{"class_key": value["class_key"], **value["bbox"], "confidence": value["confidence"]} for value in auto_result["detections"]],
                    state="AUTO_UNREVIEWED",
                )
        return {"success": True, "image": item, "auto_label": auto_result}
    except YoloDatasetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.put("/inspection/yolo/images/{image_id}/annotation")
def yolo_save_annotation(image_id: str, request: YoloAnnotationRequest):
    try:
        return {"success": True, "image": yolo_dataset.save_annotation(image_id, boxes=request.boxes, state=request.state)}
    except YoloDatasetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/inspection/yolo/images/{image_id}")
def yolo_delete_image(image_id: str):
    try:
        yolo_dataset.delete(image_id)
        return {"success": True}
    except YoloDatasetError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.post("/inspection/yolo/images/{image_id}/auto-label")
def yolo_auto_label(image_id: str):
    try:
        frame = cv2.imread(str(yolo_dataset.image_path(image_id)))
        result = yolo_vision.auto_label(frame)
        if not result.get("success"):
            return result
        if not result.get("detections"):
            return {**result, "status": "manual_label_required", "message": "No Detection — MANUAL LABEL REQUIRED"}
        item = yolo_dataset.save_annotation(
            image_id,
            boxes=[{"class_key": value["class_key"], **value["bbox"], "confidence": value["confidence"]} for value in result["detections"]],
            state="AUTO_UNREVIEWED",
        )
        return {**result, "image": item, "message": "Prediction 후보입니다. 검토 후 REVIEWED로 저장하세요."}
    except YoloDatasetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/inspection/yolo/validate")
def yolo_validate():
    return yolo_dataset.validate(force=True)


@app.post("/inspection/yolo/split")
def yolo_split(request: YoloSplitRequest):
    try:
        return yolo_dataset.create_split(train_ratio=request.train_ratio, seed=request.seed)
    except YoloDatasetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/inspection/yolo/export")
def yolo_export():
    try:
        path = yolo_dataset.export_zip()
        return {"success": True, "download_url": "/inspection/yolo/export/download", "file_name": path.name}
    except YoloDatasetError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/inspection/yolo/export/download")
def yolo_export_download():
    path = YOLO_DATA_ROOT / "exports" / "part_yolo_dataset.zip"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Dataset ZIP을 먼저 생성하세요.")
    return FileResponse(path, media_type="application/zip", filename=path.name)


@app.post("/inspection/yolo/training/start")
def yolo_training_start(request: YoloTrainingRequest):
    split = yolo_dataset.split_status()
    try:
        return yolo_training.start(
            dataset_yaml=split.get("dataset_yaml") or "",
            base_model_id=request.base_model_id,
            epochs=request.epochs,
            image_size=request.image_size,
            batch=request.batch,
        )
    except YoloTrainingError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/inspection/yolo/training/status")
def yolo_training_status():
    return yolo_training.status()


@app.post("/inspection/yolo/models/import")
async def yolo_model_import(file: UploadFile = File(...)):
    if not str(file.filename or "").lower().endswith(".pt"):
        raise HTTPException(status_code=400, detail=".pt 모델 파일만 Import할 수 있습니다.")
    temporary = UPLOAD_DIR / f"yolo_model_{uuid.uuid4().hex}.pt"
    try:
        with temporary.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        return yolo_training.import_model(temporary, str(file.filename), yolo_vision)
    except (YoloModelError, YoloTrainingError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        if temporary.is_file():
            temporary.unlink()


@app.post("/inspection/yolo/models/activate")
def yolo_model_activate(request: YoloModelActivateRequest):
    try:
        return yolo_training.activate(request.model_id, yolo_vision)
    except (YoloModelError, YoloTrainingError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/inspection/yolo/confidence")
def yolo_confidence(request: YoloConfidenceRequest):
    return {"success": True, "confidence_threshold": yolo_vision.set_confidence_threshold(request.confidence_threshold)}


@app.get("/inspection/yolo/live")
def yolo_live():
    return yolo_vision.latest_result()


@app.get("/inspection/yolo/live/snapshot")
def yolo_live_snapshot():
    jpeg = yolo_vision.get_debug_jpeg()
    if jpeg is None:
        raise HTTPException(status_code=404, detail="YOLO MODEL NOT READY 또는 아직 추론 frame이 없습니다.")
    return Response(content=jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.post("/inspection/yolo/classification-test")
def yolo_classification_test(request: YoloClassificationTestRequest):
    if PART_INSPECTION_MODE == "mock":
        raise HTTPException(status_code=400, detail="Mock 결과는 YOLO 실물 검증 통계에 포함할 수 없습니다.")
    try:
        return yolo_vision.record_classification_test(request.ground_truth_class_key)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/parts/config")
def parts_config():
    return inspection_service.get_parts_config()


@app.post("/inspection/reload-config")
def inspection_reload_config():
    inspection_service.reload_config()
    return {
        "success": True,
        "configured_parts": inspection_service.get_status()["configured_parts"],
    }


@app.post("/inspection/run")
def inspection_run(request: InspectionRunRequest):
    if not part_inspection_enabled:
        return {
            "success": False,
            "passed": False,
            "matched": False,
            "status": "error",
            "error": "PART_INSPECTION_DISABLED",
            "message": "Part Inspection이 비활성화되어 있습니다.",
        }
    return inspection_service.run(
        part_no=request.part_no,
        class_key=request.class_key,
        expected_quantity=request.expected_quantity,
        vision_enabled=TRAY_VISION_ENABLED,
    )


@app.post("/vision/count")
def vision_count(
    request:
        VisionCountRequest
):
    """Legacy compatibility endpoint.

    The primary quantity source is now the load cell and camera vision is a
    part/appearance cross-check. Existing callers can keep using /vision/count
    during the transition because the response retains matched and
    detected_quantity fields.
    """
    if not part_inspection_enabled:
        return {
            "success": False,
            "passed": False,
            "matched": False,
            "status": "error",
            "error": "PART_INSPECTION_DISABLED",
            "message": "Part Inspection이 비활성화되어 있습니다.",
        }
    return inspection_service.run(
        part_no=request.part_no,
        expected_quantity=request.expected_quantity,
    )


@app.get("/vision/aruco")
def vision_aruco(
    expected_tray_id: int | None = None,
):

    if VISION_MODE == "aruco" and not aruco_camera_enabled:
        return {
            "success": False,
            "detected": False,
            "camera_connected": False,
            "error": "ARUCO_CAMERA_DISABLED",
        }

    return (
        aruco_vision.detect_tray_aruco(
            expected_tray_id=
                expected_tray_id
        )
    )


class VisionAlignRequest(BaseModel):
    expected_tray_id: int


@app.get("/vision/alignment-mode")
def vision_alignment_mode():
    config = (
        aruco_vision.get_correction_loop_config()
        if hasattr(aruco_vision, "get_correction_loop_config")
        else {}
    )

    return {
        "success": True,
        "mode": ARUCO_ALIGNMENT_MODE,
        "vision_mode": VISION_MODE,
        "correction_loop_enabled": config.get("enabled"),
    }


def _observe_only_alignment(
    expected_tray_id: int,
):
    """
    Marker/Tray 관측만 수행한다.

    Stage correction용 calibration/readiness는 성공 조건이 아니다.
    Marker/Tray 식별이 성공하면 observe_only는 성공으로 처리하고
    Stage는 절대 이동시키지 않는다.
    """
    if VISION_MODE != "aruco":
        return {
            "success": False,
            "mode": "observe_only",
            "observed": False,
            "stage_moved": False,
            "error": "ALIGNMENT_UNAVAILABLE",
            "message": "observe_only 모드는 VISION_MODE=aruco가 필요합니다.",
        }

    if not aruco_camera_enabled:
        return {
            "success": False,
            "mode": "observe_only",
            "observed": False,
            "stage_moved": False,
            "error": "ARUCO_CAMERA_DISABLED",
            "message": "ArUco 카메라가 비활성화되어 있습니다.",
        }

    detection = aruco_vision.detect_tray_aruco(
        expected_tray_id=int(expected_tray_id)
    )

    if (
        not detection.get("success")
        or not detection.get("detected")
    ):
        return {
            "success": False,
            "mode": "observe_only",
            "observed": bool(detection.get("detected")),
            "stage_moved": False,
            "aligned": False,
            "error": detection.get(
                "error_code",
                detection.get("error", "VISION_FAILED"),
            ),
            "message": detection.get(
                "message",
                "ArUco Marker를 검출하지 못했습니다.",
            ),
            "final_detection": detection,
        }

    detected_tray_id = detection.get("tray_id")

    if detected_tray_id is None:
        return {
            "success": False,
            "mode": "observe_only",
            "observed": True,
            "stage_moved": False,
            "aligned": False,
            "error": "TRAY_ID_UNAVAILABLE",
            "message": "ArUco는 검출됐지만 Tray ID를 확인할 수 없습니다.",
            "final_detection": detection,
        }

    if int(detected_tray_id) != int(expected_tray_id):
        return {
            "success": False,
            "mode": "observe_only",
            "observed": True,
            "stage_moved": False,
            "aligned": False,
            "error": "TRAY_ID_MISMATCH",
            "message": (
                f"요청 Tray ID={expected_tray_id}, "
                f"검출 Tray ID={detected_tray_id} 불일치"
            ),
            "final_detection": detection,
        }

    alignment_ok = detection.get("alignment_ok")

    return {
        "success": True,
        "mode": "observe_only",
        "bypassed": False,
        "observed": True,
        "stage_moved": False,
        "aligned": alignment_ok,
        "verified": alignment_ok is True,
        "correction_available": bool(
            detection.get("ready_for_stage_correction")
        ),
        "aruco_id": detection.get(
            "aruco_id",
            detection.get("marker_id"),
        ),
        "tray_id": detected_tray_id,
        "final_detection": detection,
        "message": (
            "ArUco Marker/Tray 관측 성공. "
            "observe_only 모드이므로 Stage 보정 이동은 실행하지 않았습니다."
        ),
    }


@app.post("/vision/align")
def vision_align(request: VisionAlignRequest):
    """
    ArUco alignment operation modes.

    disabled:
        ArUco 정렬 단계를 BYPASS한다.

    observe_only:
        Marker/Tray ID 및 관측 결과만 확인한다.
        Stage correction readiness는 성공 조건이 아니며 Stage는 이동하지 않는다.

    closed_loop:
        기존 X/Z 폐루프 Stage 보정을 수행한다.
    """
    mode = ARUCO_ALIGNMENT_MODE

    if mode == "disabled":
        return {
            "success": True,
            "mode": "disabled",
            "bypassed": True,
            "observed": False,
            "stage_moved": False,
            "aligned": None,
            "verified": False,
            "message": "ArUco 정렬 단계를 BYPASS했습니다.",
        }

    if mode == "observe_only":
        return _observe_only_alignment(
            request.expected_tray_id
        )

    if mode != "closed_loop":
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "INVALID_ALIGNMENT_MODE",
            "message": f"지원하지 않는 ArUco alignment mode: {mode}",
        }

    if VISION_MODE != "aruco":
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "ALIGNMENT_UNAVAILABLE",
            "message": "closed_loop 모드는 VISION_MODE=aruco가 필요합니다.",
        }

    if not aruco_camera_enabled:
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "ARUCO_CAMERA_DISABLED",
            "message": "ArUco 카메라가 비활성화되어 있습니다.",
        }

    if not hasattr(aruco_vision, "get_correction_loop_config"):
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "ALIGNMENT_UNAVAILABLE",
            "message": "실제 ArUco Vision 모드에서만 사용할 수 있습니다.",
        }

    config = aruco_vision.get_correction_loop_config()

    if not config.get("enabled", False):
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "CORRECTION_LOOP_DISABLED",
            "message": (
                "closed_loop X/Z 보정 안전 스위치가 비활성화 상태입니다. "
                "실장/캘리브레이션 완료 후 system.yaml에서 enabled=true로 설정하세요."
            ),
            "config": config,
        }

    tolerance = config.get("tolerance_mm", {})
    max_single = config.get("max_single_correction_mm", {})

    required_limits = [
        tolerance.get("x"),
        tolerance.get("z"),
        max_single.get("x"),
        max_single.get("z"),
    ]

    if any(
        value is None or float(value) <= 0
        for value in required_limits
    ):
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "ALIGNMENT_LIMITS_NOT_CONFIGURED",
            "message": (
                "X/Z 허용오차와 1회 최대 보정량을 "
                "먼저 실측값으로 설정해야 합니다."
            ),
            "config": config,
        }

    max_iterations = int(
        config.get("max_iterations", 2)
    )
    steps = []

    for iteration in range(max_iterations + 1):
        detection = aruco_vision.detect_tray_aruco(
            expected_tray_id=request.expected_tray_id
        )

        steps.append({
            "type": "VISION",
            "iteration": iteration,
            "result": detection,
        })

        if (
            not detection.get("success")
            or not detection.get("detected")
        ):
            return {
                "success": False,
                "mode": mode,
                "aligned": False,
                "error": detection.get(
                    "error_code",
                    "VISION_FAILED",
                ),
                "message": detection.get(
                    "message",
                    "ArUco 검출에 실패했습니다.",
                ),
                "steps": steps,
            }

        if not detection.get(
            "ready_for_stage_correction"
        ):
            return {
                "success": False,
                "mode": mode,
                "aligned": False,
                "error": "VISION_CORRECTION_BLOCKED",
                "message": detection.get(
                    "message",
                    "Vision 보정 조건이 충족되지 않았습니다.",
                ),
                "steps": steps,
            }

        delta = (
            detection.get("stage_correction_delta_mm")
            or {}
        )
        dx = float(delta.get("x", 0.0))
        dz = float(delta.get("z", 0.0))

        if (
            abs(dx) <= float(tolerance["x"])
            and abs(dz) <= float(tolerance["z"])
        ):
            return {
                "success": True,
                "mode": mode,
                "aligned": True,
                "verified": True,
                "iterations": iteration,
                "final_detection": detection,
                "steps": steps,
            }

        if iteration >= max_iterations:
            return {
                "success": False,
                "mode": mode,
                "aligned": False,
                "error": "ALIGNMENT_MAX_ITERATIONS",
                "message": (
                    "허용 횟수 내에 X/Z 정렬 오차가 수렴하지 않았습니다."
                ),
                "final_detection": detection,
                "steps": steps,
            }

        if (
            abs(dx) > float(max_single["x"])
            or abs(dz) > float(max_single["z"])
        ):
            return {
                "success": False,
                "mode": mode,
                "aligned": False,
                "error": "CORRECTION_TOO_LARGE",
                "message": (
                    "Vision 보정량이 설정된 1회 최대 이동량을 초과했습니다."
                ),
                "correction_mm": {
                    "x": dx,
                    "z": dz,
                },
                "steps": steps,
            }

        move = stage.move_relative(dx, dz)

        steps.append({
            "type": "STAGE_CORRECTION",
            "iteration": iteration + 1,
            "result": move,
        })

        if not move.get("success"):
            return {
                "success": False,
                "mode": mode,
                "aligned": False,
                "error": "STAGE_CORRECTION_FAILED",
                "message": move.get(
                    "message",
                    "Stage 보정 이동에 실패했습니다.",
                ),
                "steps": steps,
            }

        if not config.get(
            "reobserve_after_move",
            True,
        ):
            return {
                "success": True,
                "mode": mode,
                "aligned": None,
                "verified": False,
                "message": (
                    "보정 이동은 완료했지만 "
                    "재관측 검증은 비활성화되어 있습니다."
                ),
                "steps": steps,
            }

    return {
        "success": False,
        "mode": mode,
        "aligned": False,
        "error": "ALIGNMENT_INTERNAL_ERROR",
        "steps": steps,
    }


def material_flow_alignment_callback(
    tray_id: int,
):
    """
    MaterialFlowExecutor ArUco alignment gate.

    disabled:
        즉시 성공 처리하여 기본 Material Flow를 계속한다.

    observe_only:
        Marker/Tray 식별 성공만 요구한다.
        aligned=True 또는 Stage correction readiness는 요구하지 않는다.

    closed_loop:
        실제 Stage 보정 후 aligned=True까지 요구한다.
    """
    mode = ARUCO_ALIGNMENT_MODE

    if mode == "disabled":
        return {
            "success": True,
            "mode": "disabled",
            "bypassed": True,
            "observed": False,
            "stage_moved": False,
            "aligned": None,
            "alignment_gate_passed": True,
            "message": (
                "ArUco 정렬을 BYPASS하고 Material Flow를 계속합니다."
            ),
        }

    if mode == "observe_only":
        result = _observe_only_alignment(
            int(tray_id)
        )

        if not result.get("success", False):
            return result

        return {
            **result,
            "alignment_gate_passed": True,
            "message": (
                "ArUco Marker/Tray 식별 성공. "
                "observe_only 모드이므로 Stage 보정 없이 "
                "Material Flow를 계속합니다."
            ),
        }

    if mode != "closed_loop":
        return {
            "success": False,
            "mode": mode,
            "aligned": False,
            "error": "INVALID_ALIGNMENT_MODE",
            "message": f"지원하지 않는 ArUco alignment mode: {mode}",
        }

    result = vision_align(
        VisionAlignRequest(
            expected_tray_id=int(tray_id)
        )
    )

    if not result.get("success", False):
        return result

    if result.get("aligned") is not True:
        return {
            **result,
            "success": False,
            "aligned": False,
            "error": "ALIGNMENT_NOT_VERIFIED",
            "message": (
                "closed_loop Material Flow에서는 Stage 보정 후 "
                "ArUco 재관측으로 정렬 완료가 확인되어야 합니다."
            ),
        }

    return {
        **result,
        "alignment_gate_passed": True,
    }


# ============================================================
# Stage API
# ============================================================

@app.get("/stage/status")
def stage_status():

    return (
        stage.get_status()
    )


@app.post("/stage/home")
def stage_home():

    return stage.home()


@app.post(
    "/stage/move-to-tray/{tray_id}"
)
def stage_move_to_tray(
    tray_id: int
):

    return (
        stage.move_to_tray(
            tray_id
        )
    )


@app.post("/stage/move-to-handoff")
def stage_move_to_handoff():

    return stage.move_to_handoff()


@app.post("/stage/pause")
def stage_pause():

    return stage.pause()


@app.post("/stage/resume")
def stage_resume():

    return stage.resume()


@app.post("/stage/stop")
def stage_stop():

    return stage.stop()


@app.post(
    "/stage/emergency-stop"
)
def stage_emergency_stop():

    return (
        stage.emergency_stop()
    )


@app.post("/stage/reset")
def stage_reset():

    return stage.reset_error()


# ============================================================
# 재배치 계획
# ============================================================

class RelocationPlanRequest(
    BaseModel
):
    current_slots: list[str]
    used_trays: list[str]
    target_order: list[str]


@app.post("/relocation/plan")
def relocation_plan(
    request:
        RelocationPlanRequest
):

    try:

        result = (
            build_relocation_plan(
                current_slots=
                    request.current_slots,
                used_trays=
                    request.used_trays,
                target_order=
                    request.target_order,
            )
        )

        return {
            "success": True,
            "data": result,
        }

    except ValueError as error:

        return {
            "success": False,
            "message": str(error),
        }


# ============================================================
# Material Flow API
# Stage 공급 / Handoff / 회수
# ============================================================

class MaterialFlowStartRequest(
    BaseModel
):
    items: list[dict]


@app.post("/material-flow/start")
def material_flow_start(
    request: MaterialFlowStartRequest
):

    try:
        data = material_flow.start(
            request.items
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post("/material-flow/execute-supply")
def material_flow_execute_supply():

    status = material_flow.get_status()

    if status.get("supply_state") == "ABORTED":
        return {
            "success": False,
            "message": "Material Flow가 ABORTED 상태이므로 공급을 실행할 수 없습니다.",
            "material_flow": status,
        }

    if material_flow_executor is None:
        return {
            "success": False,
            "message":
                "실제 MaterialFlowExecutor가 활성화되어 있지 않습니다.",
        }

    try:
        result = material_flow_executor.supply_current_tray()

        if not result.get("success"):
            result["material_flow"] = material_flow.abort()

        return result

    except Exception as error:
        material_flow.abort()
        return {
            "success": False,
            "message": f"Tray 공급 실행 중 오류: {error}",
        }


@app.post(
    "/material-flow/execute-return/{tray_id}"
)
def material_flow_execute_return(
    tray_id: int,
):

    status = material_flow.get_status()

    if status.get("return_state") == "ABORTED":
        return {
            "success": False,
            "message": "Material Flow가 ABORTED 상태이므로 반납을 실행할 수 없습니다.",
            "material_flow": status,
        }

    if material_flow_executor is None:
        return {
            "success": False,
            "message":
                "실제 MaterialFlowExecutor가 활성화되어 있지 않습니다.",
        }

    try:
        result = material_flow_executor.return_current_tray(
            tray_id
        )

        if not result.get("success"):
            result["material_flow"] = material_flow.abort()

        return result

    except Exception as error:
        material_flow.abort()
        return {
            "success": False,
            "message": f"Tray 반납 실행 중 오류: {error}",
        }


@app.post("/material-flow/abort")
def material_flow_abort():
    """
    사용자가 현재 자동 작업을 명시적으로 종료할 때 사용한다.

    Stage STOP과 별도로 Material Flow 상태를 ABORTED로 고정해
    남아 있는 공급/반납 Queue가 자동 재개되지 않도록 한다.
    """
    return {
        "success": True,
        "data": material_flow.abort(),
    }


@app.get("/material-flow/status")
def material_flow_status():

    return {
        "success": True,
        "data":
            material_flow.get_status(),
    }


@app.post(
    "/material-flow/supply/tray-arrived"
)
def material_flow_supply_tray_arrived():

    return {
        "success": True,
        "data":
            material_flow.supply_tray_arrived(),
    }


@app.post(
    "/material-flow/supply/alignment-complete"
)
def material_flow_supply_alignment_complete():

    return {
        "success": True,
        "data":
            material_flow.supply_alignment_complete(),
    }


@app.post(
    "/material-flow/supply/extraction-complete"
)
def material_flow_supply_extraction_complete():

    return {
        "success": True,
        "data":
            material_flow.supply_extraction_complete(),
    }


@app.post(
    "/material-flow/supply/handoff-complete"
)
def material_flow_supply_handoff_complete():

    try:
        data = (
            material_flow
            .supply_handoff_complete()
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return-ready/{tray_id}"
)
def material_flow_return_ready(
    tray_id: int
):

    try:
        data = material_flow.enqueue_return(
            tray_id
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return/identified/{tray_id}"
)
def material_flow_return_identified(
    tray_id: int
):

    try:
        data = (
            material_flow
            .return_tray_identified(
                tray_id
            )
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return/pick-complete"
)
def material_flow_return_pick_complete():

    try:
        data = (
            material_flow
            .return_pick_complete()
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return/slot-arrived"
)
def material_flow_return_slot_arrived():

    try:
        data = (
            material_flow
            .return_slot_arrived()
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return/insert-complete"
)
def material_flow_return_insert_complete():

    try:
        data = (
            material_flow
            .return_insert_complete()
        )

        return {
            "success": True,
            "data": data,
        }

    except ValueError as error:
        return {
            "success": False,
            "message": str(error),
        }


@app.post(
    "/material-flow/return/handoff-arrived"
)
def material_flow_return_handoff_arrived():

    return {
        "success": True,
        "data":
            material_flow.return_handoff_arrived(),
    }


# ============================================================
# Workflow API
# ============================================================

class WorkflowStartRequest(
    BaseModel
):
    items: list[dict]


@app.post("/workflow/start")
def workflow_start(
    request:
        WorkflowStartRequest
):

    return {
        "success": True,
        "data":
            workflow.start_work(
                request.items
            ),
    }


@app.get("/workflow/status")
def workflow_status():

    return {
        "success": True,
        "data":
            workflow.get_status(),
    }


@app.post(
    "/workflow/tray-arrived"
)
def workflow_tray_arrived():

    return {
        "success": True,
        "data":
            workflow.tray_arrived(),
    }


@app.post(
    "/workflow/picking-complete"
)
def workflow_picking_complete():

    return {
        "success": True,
        "data":
            workflow.picking_complete(),
    }


@app.post(
    "/workflow/start-vision"
)
def workflow_start_vision():

    return {
        "success": True,
        "data":
            workflow.start_vision_check(),
    }


@app.post(
    "/workflow/vision-passed"
)
def workflow_vision_passed():

    return {
        "success": True,
        "data":
            workflow.vision_passed(),
    }


@app.post(
    "/workflow/next-item"
)
def workflow_next_item():

    return {
        "success": True,
        "data":
            workflow.next_item(),
    }


@app.post(
    "/workflow/final-verification-passed"
)
def workflow_final_verification_passed():

    return {
        "success": True,
        "data":
            workflow.final_verification_passed(),
    }


@app.post(
    "/workflow/tray-return-complete"
)
def workflow_tray_return_complete():

    return {
        "success": True,
        "data":
            workflow.tray_return_complete(),
    }


@app.post(
    "/workflow/relocation-complete"
)
def workflow_relocation_complete():

    return {
        "success": True,
        "data":
            workflow.relocation_complete(),
    }


@app.post(
    "/workflow/inventory-complete"
)
def workflow_inventory_complete():

    return {
        "success": True,
        "data":
            workflow.inventory_complete(),
    }


@app.post(
    "/workflow/history-complete"
)
def workflow_history_complete():

    return {
        "success": True,
        "data":
            workflow.history_complete(),
    }
