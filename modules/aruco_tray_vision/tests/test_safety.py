import numpy as np
from aruco_tray.models import Pose6D
from aruco_tray.safety import check_pose_limits


def make_pose(r,p,y):
    return Pose6D(1,np.zeros(3),np.eye(3),r,p,y,0.0)


def test_pose_ok():
    assert check_pose_limits(make_pose(2,3,5),8,8,15).ok


def test_pose_rejected():
    result=check_pose_limits(make_pose(10,3,5),8,8,15)
    assert not result.ok and 'Roll' in result.reasons[0]
