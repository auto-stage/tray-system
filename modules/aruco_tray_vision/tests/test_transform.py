import numpy as np
from aruco_tray.transforms import transform_point_4x4


def test_transform_point():
    T=np.eye(4); T[:3,3]=[10,20,30]
    assert np.allclose(transform_point_4x4(T,[1,2,3]),[11,22,33])
