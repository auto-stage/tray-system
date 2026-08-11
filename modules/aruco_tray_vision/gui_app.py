from __future__ import annotations

import sys
from pathlib import Path
import cv2
import numpy as np
import yaml

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QSpinBox, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from aruco_tray.calibration import ChessboardCalibrator
from aruco_tray.config import load_trays, load_yaml
from aruco_tray.controller import build_vision_decision
from aruco_tray.vision import ArucoVision

ROOT = Path(__file__).resolve().parent


class MainWindow(QMainWindow):
    STEPS = [
        "1. 카메라 프레임 수신",
        "2. ArUco 마커 검출",
        "3. 트레이 ID 조회",
        "4. 카메라 캘리브레이션/6DoF 확인",
        "5. 3D 파지점 계산",
        "6. Roll/Pitch/Yaw 허용범위 검사",
        "7. Camera→Stage 좌표변환 확인",
        "8. 실제 스테이지/그리퍼 통합 준비",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ArUco 6DoF 트레이 자세/파지점 테스트")
        self.resize(1400, 850)

        self.trays = load_trays(ROOT / "config" / "trays.yaml")
        self.system = load_yaml(ROOT / "config" / "system.yaml")
        marker_sizes = {k: v.marker_size_mm for k, v in self.trays.items()}
        self.profile_path = ROOT / "config" / "camera_laptop.yaml"
        self.vision = ArucoVision(marker_sizes, self.profile_path)
        self.calibrator = ChessboardCalibrator(9, 6, 25.0)

        self.cap = None
        self.last_frame = None
        self.last_obs = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_frame)

        self.build_ui()

    def build_ui(self):
        tabs = QTabWidget()
        tabs.addTab(self.build_operation_tab(), "운용 / 6DoF 테스트")
        tabs.addTab(self.build_calibration_tab(), "카메라 캘리브레이션")
        self.setCentralWidget(tabs)

    def build_operation_tab(self):
        root = QWidget(); layout = QHBoxLayout(root)
        left = QVBoxLayout(); right = QVBoxLayout()

        self.video = QLabel("카메라를 시작해 주세요.")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setMinimumSize(850, 620)
        self.video.setStyleSheet("background:#111;color:#ddd;border:1px solid #444;")
        left.addWidget(self.video)

        row = QHBoxLayout()
        row.addWidget(QLabel("Camera index"))
        self.cam_index = QSpinBox(); self.cam_index.setRange(0, 10); self.cam_index.setValue(0)
        row.addWidget(self.cam_index)
        self.btn_camera = QPushButton("카메라 시작"); self.btn_camera.clicked.connect(self.toggle_camera)
        row.addWidget(self.btn_camera)
        self.btn_seq = QPushButton("시퀀스 점검"); self.btn_seq.clicked.connect(self.run_sequence_check)
        row.addWidget(self.btn_seq)
        left.addLayout(row)

        prow = QHBoxLayout()
        prow.addWidget(QLabel("카메라 프로파일"))
        self.profile_edit = QLineEdit(str(self.profile_path)); prow.addWidget(self.profile_edit)
        bsel = QPushButton("선택"); bsel.clicked.connect(self.choose_profile); prow.addWidget(bsel)
        bre = QPushButton("다시 불러오기"); bre.clicked.connect(self.reload_profile); prow.addWidget(bre)
        left.addLayout(prow)
        layout.addLayout(left, 3)

        status = QGroupBox("실시간 상태"); grid = QGridLayout(status)
        self.labels = {}
        fields = [
            ("cal", "캘리브레이션"), ("id", "마커 / 트레이"), ("px", "중심 픽셀"),
            ("imgyaw", "영상 Yaw"), ("xyz", "Marker XYZ (camera)"),
            ("rpy", "Marker Roll/Pitch/Yaw"), ("grip", "Grip XYZ (camera)"),
            ("poseok", "자세 허용 판정"), ("stage", "Stage 좌표"),
        ]
        for r,(key,name) in enumerate(fields):
            grid.addWidget(QLabel(name), r, 0)
            lab=QLabel("-"); self.labels[key]=lab; grid.addWidget(lab, r, 1)
        right.addWidget(status)

        seqbox=QGroupBox("시퀀스 점검"); sv=QVBoxLayout(seqbox)
        self.seq=QListWidget()
        for s in self.STEPS: self.seq.addItem(QListWidgetItem("○ "+s))
        sv.addWidget(self.seq); right.addWidget(seqbox,1)
        self.log=QTextEdit(); self.log.setReadOnly(True); right.addWidget(self.log,1)
        layout.addLayout(right,2)
        return root

    def build_calibration_tab(self):
        root=QWidget(); v=QVBoxLayout(root)
        info=QLabel(
            "체커보드: 내부 코너 9×6, 한 칸 25 mm. 노트북 카메라와 외장 웹캠은 각각 따로 캘리브레이션합니다. "
            "다양한 위치/거리/기울기에서 최소 10장, 권장 15장 이상 수집하세요."
        ); info.setWordWrap(True); v.addWidget(info)
        row=QHBoxLayout(); row.addWidget(QLabel("저장 YAML"))
        self.cal_path=QLineEdit(str(ROOT/'config/camera_laptop.yaml')); row.addWidget(self.cal_path)
        b=QPushButton("저장 경로 선택"); b.clicked.connect(self.choose_cal_path); row.addWidget(b); v.addLayout(row)
        row2=QHBoxLayout()
        self.checker_label=QLabel("체커보드: 미검출"); row2.addWidget(self.checker_label)
        self.sample_label=QLabel("샘플: 0"); row2.addWidget(self.sample_label)
        ba=QPushButton("현재 샘플 추가"); ba.clicked.connect(self.add_cal_sample); row2.addWidget(ba)
        bc=QPushButton("계산/저장"); bc.clicked.connect(self.calibrate); row2.addWidget(bc)
        br=QPushButton("샘플 초기화"); br.clicked.connect(self.clear_samples); row2.addWidget(br)
        v.addLayout(row2); v.addStretch(1)
        return root

    def toggle_camera(self):
        if self.cap is None:
            cap=cv2.VideoCapture(self.cam_index.value())
            if not cap.isOpened():
                QMessageBox.critical(self,"카메라 오류",f"Camera index {self.cam_index.value()}를 열 수 없습니다.")
                return
            self.cap=cap; self.timer.start(30); self.btn_camera.setText("카메라 중지")
            self.log.append(f"카메라 시작 index={self.cam_index.value()}")
        else:
            self.close_camera()

    def close_camera(self):
        self.timer.stop()
        if self.cap is not None: self.cap.release()
        self.cap=None; self.btn_camera.setText("카메라 시작")

    def update_frame(self):
        if self.cap is None: return
        ok,frame=self.cap.read()
        if not ok: return
        self.last_frame=frame.copy()
        self.last_obs=self.vision.detect(frame)
        display=self.vision.draw(frame,self.last_obs)
        found,corners=self.calibrator.detect(frame)
        self.checker_label.setText("체커보드: 검출" if found else "체커보드: 미검출")
        if found and corners is not None:
            cv2.drawChessboardCorners(display,(9,6),corners,found)
        self.update_status()
        self.show_frame(display)

    def update_status(self):
        self.labels['cal'].setText("완료" if self.vision.calibrated else "미완료 (ID/2D Yaw만 가능)")
        if not self.last_obs:
            for k in ['id','px','imgyaw','xyz','rpy','grip','poseok','stage']: self.labels[k].setText("-")
            self.labels['id'].setText("미검출"); return
        obs=self.last_obs[0]; tray=self.trays.get(obs.marker_id)
        self.labels['id'].setText(f"ID {obs.marker_id} / {tray.tray_code if tray else '미등록'}")
        self.labels['px'].setText(f"({obs.center_u_px:.1f}, {obs.center_v_px:.1f})")
        self.labels['imgyaw'].setText(f"{obs.image_yaw_deg:+.2f}°")
        if obs.pose6d is None or tray is None:
            self.labels['xyz'].setText("캘리브레이션 필요")
            self.labels['rpy'].setText("캘리브레이션 필요")
            self.labels['grip'].setText("-"); self.labels['poseok'].setText("-"); self.labels['stage'].setText("미캘리브레이션")
            return
        p=obs.pose6d; x,y,z=p.translation_mm
        self.labels['xyz'].setText(f"X={x:+.1f}, Y={y:+.1f}, Z={z:+.1f} mm")
        self.labels['rpy'].setText(f"R={p.roll_deg:+.1f}°, P={p.pitch_deg:+.1f}°, Y={p.yaw_deg:+.1f}°")
        try:
            d=build_vision_decision(obs,self.trays,self.system['pose_limits'],self.camera_to_stage_matrix())
            gx,gy,gz=d.target_camera.position_mm
            self.labels['grip'].setText(f"X={gx:+.1f}, Y={gy:+.1f}, Z={gz:+.1f} mm")
            self.labels['poseok'].setText("POSE OK" if d.pose_check.ok else "OUT: "+" / ".join(d.pose_check.reasons))
            if d.target_stage_xyz_mm is None:
                self.labels['stage'].setText("Camera→Stage extrinsic 미캘리브레이션")
            else:
                sx,sy,sz=d.target_stage_xyz_mm; self.labels['stage'].setText(f"X={sx:+.1f}, Y={sy:+.1f}, Z={sz:+.1f} mm")
        except Exception as e:
            self.labels['grip'].setText(str(e))

    def camera_to_stage_matrix(self):
        cfg=self.system['integration']['camera_to_stage']
        if not cfg.get('calibrated',False) or cfg.get('matrix_4x4') is None: return None
        return np.asarray(cfg['matrix_4x4'],dtype=float).reshape(4,4)

    def run_sequence_check(self):
        for i,s in enumerate(self.STEPS): self.seq.item(i).setText("○ "+s)
        def mark(i,ok,msg):
            self.seq.item(i).setText(("✓ " if ok else "✕ ")+self.STEPS[i]); self.log.append(msg)
            return ok
        if self.last_frame is None:
            mark(0,False,"1) 카메라 프레임 없음"); return
        mark(0,True,"1) 카메라 프레임 정상")
        if not self.last_obs:
            mark(1,False,"2) ArUco 미검출"); return
        obs=self.last_obs[0]; mark(1,True,f"2) ArUco ID={obs.marker_id} 검출")
        tray=self.trays.get(obs.marker_id)
        if tray is None:
            mark(2,False,"3) 미등록 마커"); return
        mark(2,True,f"3) 트레이={tray.tray_code}")
        if obs.pose6d is None:
            mark(3,False,"4) 카메라 미캘리브레이션: 6DoF 시험은 여기서 정상 중단"); return
        mark(3,True,"4) 6DoF pose 유효")
        d=build_vision_decision(obs,self.trays,self.system['pose_limits'],self.camera_to_stage_matrix())
        mark(4,True,f"5) 3D grip target={d.target_camera.position_mm.round(2).tolist()} mm")
        if not d.pose_check.ok:
            mark(5,False,"6) 자세 허용범위 초과: "+" / ".join(d.pose_check.reasons)); return
        mark(5,True,"6) Roll/Pitch/Yaw 허용범위 이내")
        if d.target_stage_xyz_mm is None:
            mark(6,False,"7) Camera→Stage extrinsic 미캘리브레이션: 실제 스테이지 통합 전 정상 상태")
            self.seq.item(7).setText("○ "+self.STEPS[7]); return
        mark(6,True,"7) Stage 좌표변환 가능")
        mark(7,True,"8) 실제 SerialStage/Gripper 어댑터 연결 가능 상태")

    def show_frame(self,frame):
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB); h,w,ch=rgb.shape
        q=QImage(rgb.data,w,h,ch*w,QImage.Format.Format_RGB888).copy()
        pix=QPixmap.fromImage(q).scaled(self.video.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        self.video.setPixmap(pix)

    def choose_profile(self):
        p,_=QFileDialog.getOpenFileName(self,"프로파일 선택",str(ROOT/'config'),"YAML (*.yaml *.yml)")
        if p: self.profile_edit.setText(p); self.reload_profile()

    def reload_profile(self):
        self.vision.set_camera_profile(self.profile_edit.text()); self.log.append("프로파일 로드: "+self.profile_edit.text())

    def choose_cal_path(self):
        p,_=QFileDialog.getSaveFileName(self,"캘리브레이션 저장",str(ROOT/'config/camera_external.yaml'),"YAML (*.yaml)")
        if p: self.cal_path.setText(p)

    def add_cal_sample(self):
        if self.last_frame is None:
            QMessageBox.warning(self,"캘리브레이션","먼저 카메라를 시작해 주세요."); return
        if not self.calibrator.add_sample(self.last_frame):
            self.log.append("체커보드 코너 검출 실패"); return
        self.sample_label.setText(f"샘플: {self.calibrator.sample_count}")
        self.log.append(f"캘리브레이션 샘플 #{self.calibrator.sample_count}")

    def clear_samples(self):
        self.calibrator.clear(); self.sample_label.setText("샘플: 0"); self.log.append("캘리브레이션 샘플 초기화")

    def calibrate(self):
        try:
            rms=self.calibrator.calibrate_and_save(self.cal_path.text(),Path(self.cal_path.text()).stem,self.cam_index.value())
        except Exception as e:
            QMessageBox.warning(self,"캘리브레이션 실패",str(e)); return
        self.profile_edit.setText(self.cal_path.text()); self.reload_profile()
        QMessageBox.information(self,"완료",f"캘리브레이션 저장 완료\nRMS={rms:.5f}")

    def closeEvent(self,event):
        self.close_camera(); event.accept()


def run_gui():
    app=QApplication(sys.argv); w=MainWindow(); w.show(); sys.exit(app.exec())


if __name__ == '__main__':
    run_gui()
