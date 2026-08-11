import math
import numpy as np

from aruco_tray.geometry import compute_grip_target_camera, rotation_matrix_to_euler_zyx_deg
from aruco_tray.models import Pose6D, TrayDefinition


def rz(deg):
    a=math.radians(deg)
    return np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]],float)


def tray():
    return TrayDefinition(1,'A-01',50.0,np.array([80.0,30.0,10.0]))


def pose(R=np.eye(3)):
    r,p,y=rotation_matrix_to_euler_zyx_deg(R)
    return Pose6D(1,np.array([100.0,200.0,600.0]),R,r,p,y,0.0)


def test_identity_3d_grip_target():
    g=compute_grip_target_camera(pose(),tray())
    assert np.allclose(g.position_mm,[180.0,230.0,610.0])


def test_yaw_90_rotates_offset_in_3d():
    g=compute_grip_target_camera(pose(rz(90)),tray())
    assert np.allclose(g.position_mm,[70.0,280.0,610.0],atol=1e-9)


def test_euler_identity():
    r,p,y=rotation_matrix_to_euler_zyx_deg(np.eye(3))
    assert abs(r)<1e-12 and abs(p)<1e-12 and abs(y)<1e-12
