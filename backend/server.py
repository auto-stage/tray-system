from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
from adapters.stm32_stage_adapter import STM32StageAdapter
from adapters.mock_vision_adapter import MockVisionAdapter
from workflow.workflow_controller import WorkflowController
from parts_db import find_part

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import shutil
import os


# ============================================================
# 기본 경로
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

RACK_LAYOUT_PATH = DATA_DIR / "rack_layout.json"


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

# 부품 수량 검사용 Vision
# 현재 최신 브랜치의 기존 동작을 유지하기 위해 Mock을 그대로 사용한다.
count_vision = MockVisionAdapter()

# Tray ArUco 검출용 Vision
VISION_MODE = os.getenv(
    "VISION_MODE",
    "mock",
).strip().lower()

if VISION_MODE == "aruco":

    # 실제 ArUco 모드에서만 의존성을 불러온다.
    from adapters.aruco_vision_adapter import ArucoVisionAdapter

    camera_index_raw = os.getenv(
        "VISION_CAMERA_INDEX"
    )

    aruco_vision = ArucoVisionAdapter(
        camera_index=(
            int(camera_index_raw)
            if camera_index_raw is not None
            else None
        ),
        camera_profile=(
            os.getenv(
                "VISION_CAMERA_PROFILE"
            )
            or None
        ),
    )

    print(
        "[VISION] 실제 ArUco 모드"
    )

else:

    aruco_vision = MockVisionAdapter()

    print(
        "[VISION] MOCK 모드"
    )

workflow = WorkflowController()


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

    return (
        aruco_vision.get_camera_status()
    )


@app.get("/vision/stream")
def vision_stream(
    annotate: bool = True,
):

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
        return (
            aruco_vision.select_camera(
                profile_name=
                    request.profile_name,
                camera_index=
                    request.camera_index,
            )
        )
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


@app.post("/vision/count")
def vision_count(
    request:
        VisionCountRequest
):

    return (
        count_vision.detect_part_count(
            part_no=
                request.part_no,
            expected_quantity=
                request.expected_quantity,
        )
    )


@app.get("/vision/aruco")
def vision_aruco(
    expected_tray_id: int | None = None,
):

    return (
        aruco_vision.detect_tray_aruco(
            expected_tray_id=
                expected_tray_id
        )
    )


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