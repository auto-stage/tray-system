from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any


# ``python3 backend/tools/servo_angle_debug.py`` 형태로 직접 실행해도
# repository root의 ``backend`` 패키지를 import할 수 있게 한다.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "STM32 MG996R gripper servo angle debugger. "
            "Backend를 종료한 상태에서 사용하세요."
        )
    )
    parser.add_argument(
        "--port",
        default=None,
        help=(
            "STM32 serial port (예: /dev/ttyACM0). "
            "생략하면 STM32StageAdapter가 자동 탐색합니다."
        ),
    )
    parser.add_argument(
        "--angle",
        type=int,
        default=None,
        help="한 번만 이동시킬 servo 절대 각도(0~180).",
    )
    return parser.parse_args()


def _valid_angle(angle_deg: int) -> bool:
    return 0 <= angle_deg <= 180


def _send_angle(
    gripper: Any,
    angle_deg: int,
) -> bool:
    if not _valid_angle(angle_deg):
        print("[ERROR] 각도는 0~180 범위여야 합니다.")
        return False

    result = gripper.set_angle(angle_deg)

    if result.get("success"):
        print(f"[OK] Servo -> {angle_deg} deg")
        message = str(result.get("message", "")).strip()
        if message:
            print(f"     STM32: {message}")
        return True

    print(
        "[ERROR] "
        + str(result.get("message", "SERVO 명령 실패"))
    )

    received = result.get("received")
    if received:
        print(f"        received={received}")

    return False


def _interactive_loop(gripper: Any) -> None:
    print()
    print("Servo angle debug mode")
    print("- 0~180 사이 정수 각도를 입력하세요.")
    print("- 종료: q / quit / exit")
    print("- 실물 간섭을 확인하면서 작은 각도 변화로 조정하세요.")

    while True:
        try:
            raw = input("Servo angle (0-180, q=quit): ").strip()
        except EOFError:
            print()
            break

        if not raw:
            continue

        if raw.lower() in {"q", "quit", "exit"}:
            break

        try:
            angle_deg = int(raw)
        except ValueError:
            print("[ERROR] 정수 각도 또는 q를 입력하세요.")
            continue

        _send_angle(gripper, angle_deg)


def main() -> int:
    args = _parse_args()

    if args.angle is not None and not _valid_angle(args.angle):
        print("[ERROR] --angle은 0~180 범위여야 합니다.")
        return 2

    try:
        from backend.adapters.stm32_gripper_adapter import STM32GripperAdapter
        from backend.adapters.stm32_stage_adapter import BAUDRATE, STM32StageAdapter
    except ImportError as exc:
        print(f"[ERROR] Backend 의존성 import 실패: {exc}")
        print("        requirements.txt 설치 상태를 확인하세요.")
        return 1

    stage = STM32StageAdapter(
        port=args.port,
        baudrate=BAUDRATE,
        auto_connect=False,
    )

    try:
        print("[INFO] STM32 연결 중...")
        stage.connect()
        print(
            f"[OK] STM32 connected: {stage.port} "
            f"@ {stage.baudrate} baud"
        )

        gripper = STM32GripperAdapter(stage)

        if args.angle is not None:
            return 0 if _send_angle(gripper, args.angle) else 1

        _interactive_loop(gripper)
        return 0

    except KeyboardInterrupt:
        print("\n[INFO] 사용자 입력으로 종료합니다.")
        return 130
    except Exception as exc:
        print(f"[ERROR] STM32 연결/통신 실패: {exc}")
        return 1
    finally:
        # Servo debugger 종료 시 X/Z ENABLE 상태나 Stage 상태를 변경하지 않는다.
        stage.close(safe=False)


if __name__ == "__main__":
    raise SystemExit(main())
