from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

VALID_ALIGNMENT_MODES = frozenset({"disabled", "observe_only", "closed_loop"})


def normalize_alignment_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in VALID_ALIGNMENT_MODES:
        allowed = ", ".join(sorted(VALID_ALIGNMENT_MODES))
        raise ValueError(f"지원하지 않는 ArUco alignment mode: {value!r}. 허용값: {allowed}")
    return mode


def _legacy_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off", "none", "null"}:
        return False
    raise ValueError("correction_loop.enabled는 boolean 값이어야 합니다.")


def resolve_alignment_mode(correction_loop: Mapping[str, Any] | None, *, override: str | None = None) -> str:
    loop = dict(correction_loop or {})
    if override is not None and str(override).strip():
        return normalize_alignment_mode(override)
    explicit = loop.get("mode")
    if explicit is not None and str(explicit).strip():
        return normalize_alignment_mode(explicit)
    return "closed_loop" if _legacy_enabled(loop.get("enabled", False)) else "disabled"


def load_alignment_mode(system_config_path: str | Path, *, override: str | None = None) -> str:
    path = Path(system_config_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    integration = raw.get("integration", {})
    if not isinstance(integration, dict):
        integration = {}
    loop = integration.get("correction_loop", {})
    if not isinstance(loop, dict):
        loop = {}
    return resolve_alignment_mode(loop, override=override)
