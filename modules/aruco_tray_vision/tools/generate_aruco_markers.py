#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import yaml


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate configured ArUco marker PNG files."
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parents[1] / "config" / "trays.yaml"),
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory. Generated images are runtime/printing artifacts, not repository files.",
    )
    parser.add_argument("--pixels", type=int, default=1000)
    parser.add_argument(
        "--ids",
        type=int,
        nargs="*",
        default=None,
        help="Generate selected IDs. Default: all enabled configured IDs.",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    trays = data.get("trays", {})
    if not isinstance(trays, dict):
        raise SystemExit("trays.yaml의 trays 항목이 올바르지 않습니다.")

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    output = Path(args.output).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    requested = {int(value) for value in args.ids} if args.ids else None
    generated = 0

    for raw_id, cfg in sorted(trays.items(), key=lambda item: int(item[0])):
        marker_id = int(raw_id)

        if requested is not None and marker_id not in requested:
            continue
        if not bool(cfg.get("enabled", True)):
            continue
        if marker_id < 0 or marker_id >= 50:
            print(f"[SKIP] ID {marker_id}: DICT_4X4_50 범위를 벗어났습니다.")
            continue

        image = cv2.aruco.generateImageMarker(dictionary, marker_id, int(args.pixels))
        path = output / f"aruco_4x4_50_id_{marker_id}.png"

        if not cv2.imwrite(str(path), image):
            raise RuntimeError(f"저장 실패: {path}")

        print(f"[OK] ID {marker_id}: {path}")
        generated += 1

    print(f"Generated {generated} marker(s).")
    print(
        "주의: PNG 픽셀 크기는 실제 mm 크기를 보장하지 않습니다. "
        "인쇄 후 실제 마커 한 변 길이를 측정하여 trays.yaml marker_size_mm에 반영하세요."
    )


if __name__ == "__main__":
    main()
