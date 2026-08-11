from __future__ import annotations

from pathlib import Path
import numpy as np
import yaml

from .models import TrayDefinition


def load_yaml(path: str | Path) -> dict:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be mapping: {path}")
    return data


def load_trays(path: str | Path) -> dict[int, TrayDefinition]:
    data = load_yaml(path)
    out: dict[int, TrayDefinition] = {}
    for raw_id, raw in data["trays"].items():
        marker_id = int(raw_id)
        off = raw["marker_to_grip_mm"]
        out[marker_id] = TrayDefinition(
            marker_id=marker_id,
            tray_code=str(raw["tray_code"]),
            marker_size_mm=float(raw["marker_size_mm"]),
            grip_offset_marker_mm=np.array(
                [float(off["x"]), float(off["y"]), float(off.get("z", 0.0))],
                dtype=float,
            ),
        )
    return out
