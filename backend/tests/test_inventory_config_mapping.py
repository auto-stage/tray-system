from __future__ import annotations

import json
from pathlib import Path
import sys

import yaml

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import inventory as inventory_service


def test_parts_yaml_is_authoritative_for_tray_mapping(monkeypatch, tmp_path):
    parts = yaml.safe_load(
        (BACKEND_DIR / "config" / "parts.yaml").read_text(encoding="utf-8")
    )
    t_bolt_tray = parts["parts"]["t_bolt"]["tray_id"]
    flange_nut_tray = parts["parts"]["flange_nut"]["tray_id"]
    parts["parts"]["t_bolt"]["tray_id"] = flange_nut_tray
    parts["parts"]["flange_nut"]["tray_id"] = t_bolt_tray

    parts_path = tmp_path / "parts.yaml"
    parts_path.write_text(
        yaml.safe_dump(parts, allow_unicode=True),
        encoding="utf-8",
    )

    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(
        json.dumps(
            {
                "B001": {
                    "tray": 1,
                    "name": "OLD T BOLT",
                    "spec": "OLD",
                    "stock": 24,
                },
                "N001": {
                    "tray": 4,
                    "name": "OLD FLANGE",
                    "spec": "OLD",
                    "stock": 20,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(inventory_service, "PARTS_CONFIG_PATH", parts_path)
    monkeypatch.setattr(inventory_service, "INVENTORY_PATH", inventory_path)

    result = inventory_service.get_all_inventory()

    assert result["B001"]["tray"] == flange_nut_tray
    assert result["B001"]["name"] == "T 볼트"
    assert result["B001"]["part_no"] == "B001"
    assert result["N001"]["tray"] == t_bolt_tray
    assert result["N001"]["name"] == "플랜지 너트"
