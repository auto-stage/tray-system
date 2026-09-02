from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PartInspectionAdapter(ABC):
    """Visual part-inspection interface.

    The real implementation may use OpenCV measurement, YOLO, or a hybrid.
    Real and Mock implementations return the same class/count/status contract.
    A detector may initially support only single-object validation; it must
    report that capability instead of presenting Mock counting as real.
    """

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def inspect(
        self,
        *,
        class_key: str,
        part_config: dict[str, Any],
        expected_count: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError
