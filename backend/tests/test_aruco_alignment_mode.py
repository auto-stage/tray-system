from pathlib import Path

import pytest
import yaml

from backend.services.aruco_alignment_mode import load_alignment_mode, normalize_alignment_mode, resolve_alignment_mode


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
    assert resolve_alignment_mode({"mode": "observe_only", "enabled": True}) == "observe_only"


def test_environment_override_has_highest_priority():
    assert resolve_alignment_mode({"mode": "closed_loop", "enabled": True}, override="disabled") == "disabled"


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
    path.write_text(yaml.safe_dump({"integration": {"correction_loop": {"mode": "observe_only"}}}), encoding="utf-8")
    assert load_alignment_mode(path) == "observe_only"
