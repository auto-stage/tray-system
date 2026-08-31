from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LoadCellAdapter(ABC):
    """Load-cell interface used by the inspection service.

    Real hardware implementations only need to preserve this contract.
    All returned weights use grams.
    """

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def tare(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def read_weight(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def estimate_count(
        self,
        *,
        part_config: dict[str, Any],
        expected_quantity: int | None = None,
    ) -> dict[str, Any]:
        """Estimate piece count from load-cell data.

        expected_quantity exists only so a Mock adapter can create a
        deterministic development measurement. A real adapter must calculate
        the count from its measured weight and part calibration data.
        """
        raise NotImplementedError
