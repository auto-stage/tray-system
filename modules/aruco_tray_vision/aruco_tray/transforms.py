from __future__ import annotations

import numpy as np


def transform_point_4x4(T: np.ndarray, point_xyz: np.ndarray) -> np.ndarray:
    T = np.asarray(T, dtype=float).reshape(4, 4)
    p = np.append(np.asarray(point_xyz, dtype=float).reshape(3), 1.0)
    out = T @ p
    if abs(out[3]) < 1e-12:
        raise ValueError("Invalid homogeneous transform")
    return out[:3] / out[3]


def identity_transform() -> np.ndarray:
    return np.eye(4, dtype=float)
