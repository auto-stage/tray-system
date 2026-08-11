from __future__ import annotations
import argparse, glob
from pathlib import Path
import cv2
from aruco_tray.calibration import ChessboardCalibrator


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--images',required=True,help='예: calibration_images/*.png')
    p.add_argument('--square-mm',type=float,default=25.0)
    p.add_argument('--cols',type=int,default=9)
    p.add_argument('--rows',type=int,default=6)
    p.add_argument('--output',default='config/camera_external.yaml')
    a=p.parse_args()
    cal=ChessboardCalibrator(a.cols,a.rows,a.square_mm)
    for path in sorted(glob.glob(a.images)):
        im=cv2.imread(path)
        if im is not None and cal.add_sample(im): print('[OK]',path)
        else: print('[SKIP]',path)
    rms=cal.calibrate_and_save(a.output,Path(a.output).stem,-1)
    print('[DONE]',a.output,'RMS=',rms)

if __name__=='__main__': main()
