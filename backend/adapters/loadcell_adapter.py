from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import time


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

    def read_average(
        self,
        *,
        samples: int = 5,
        interval_sec: float = 0.05,
    ) -> dict[str, Any]:
        """여러 번 측정하여 평균 net weight를 반환한다."""

        samples = max(int(samples), 1)
        values: list[float] = []
        raws: list[int] = []

        for index in range(samples):
            result = self.read_weight()

            if not result.get("success"):
                return {
                    "success": False,
                    "message": result.get(
                        "message",
                        "Load Cell 측정 실패",
                    ),
                    "sample_index": index,
                }

            weight = result.get(
                "net_weight_g",
                result.get("weight_g"),
            )

            if weight is None:
                return {
                    "success": False,
                    "message": "Load Cell 응답에 weight 값이 없습니다.",
                }

            values.append(float(weight))

            if result.get("raw") is not None:
                raws.append(int(result["raw"]))

            if (
                index < samples - 1
                and interval_sec > 0.0
            ):
                time.sleep(interval_sec)

        average_g = sum(values) / len(values)
        spread_g = max(values) - min(values)

        return {
            "success": True,
            "samples": len(values),
            "average_weight_g": average_g,
            "min_weight_g": min(values),
            "max_weight_g": max(values),
            "spread_g": spread_g,
            "raw_average": (
                sum(raws) / len(raws)
                if raws
                else None
            ),
        }

    def tray_present(
        self,
        *,
        threshold_g: float,
        samples: int = 5,
    ) -> dict[str, Any]:
        """캐리지 Load Cell에 Tray 하중이 존재하는지 확인한다."""

        result = self.read_average(
            samples=samples,
        )

        if not result.get("success"):
            return result

        weight_g = float(
            result["average_weight_g"]
        )

        detected = (
            weight_g >= float(threshold_g)
        )

        return {
            **result,
            "tray_present": detected,
            "threshold_g": float(threshold_g),
        }

    def tray_released(
        self,
        *,
        threshold_g: float,
        samples: int = 5,
    ) -> dict[str, Any]:
        """Tray 전달/해제 후 캐리지에서 하중이 사라졌는지 확인한다."""

        result = self.read_average(
            samples=samples,
        )

        if not result.get("success"):
            return result

        weight_g = float(
            result["average_weight_g"]
        )

        released = (
            weight_g < float(threshold_g)
        )

        return {
            **result,
            "tray_released": released,
            "threshold_g": float(threshold_g),
        }

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
