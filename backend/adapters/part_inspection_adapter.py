from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class PartInspectionAdapter(ABC):
    """Visual part-inspection interface.

    The real implementation may use OpenCV measurement, YOLO, or a hybrid.
    Quantity is deliberately NOT the primary responsibility of this adapter;
    the load cell is the primary piece-count source.
    """

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def inspect(
        self,
        *,
        part_no: str,
        part_config: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError
