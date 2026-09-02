from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any
import uuid


class YoloTrainingError(ValueError):
    pass


class YoloTrainingService:
    def __init__(self, *, root: str | Path, worker_path: str | Path) -> None:
        self.root = Path(root).resolve()
        self.base_models = self.root / "base_models"
        self.runs = self.root / "runs"
        self.models = self.root / "models"
        self.state = self.root / "state"
        self.worker_path = Path(worker_path).resolve()
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._log = None
        self._job_id: str | None = None

    def _ensure(self) -> None:
        for path in (self.base_models, self.runs, self.models, self.state):
            path.mkdir(parents=True, exist_ok=True)

    def available_base_models(self) -> list[dict[str, Any]]:
        self._ensure()
        paths = list(self.base_models.glob("*.pt"))
        configured = os.getenv("YOLO_BASE_WEIGHT", "").strip()
        if configured and Path(configured).expanduser().is_file():
            paths.append(Path(configured).expanduser().resolve())
        unique = {str(path.resolve()): path.resolve() for path in paths}
        return [{"id": path.stem, "name": path.name, "path": str(path), "size_bytes": path.stat().st_size} for path in sorted(unique.values())]

    def _base_path(self, model_id: str) -> Path:
        match = next((item for item in self.available_base_models() if item["id"] == model_id), None)
        if not match:
            raise YoloTrainingError("BASE_MODEL_NOT_READY: 공식 호환 detection weight를 승인 후 준비해야 합니다.")
        return Path(match["path"])

    def start(self, *, dataset_yaml: str | Path, base_model_id: str, epochs: int, image_size: int, batch: int) -> dict[str, Any]:
        dataset = Path(dataset_yaml).resolve()
        if not dataset.is_file():
            raise YoloTrainingError("Train/Validation 생성 후 학습할 수 있습니다.")
        if not 1 <= int(epochs) <= 1000 or not 128 <= int(image_size) <= 2048 or int(batch) < 1:
            raise YoloTrainingError("Training 설정 범위를 확인하세요.")
        base = self._base_path(base_model_id)
        self._ensure()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise YoloTrainingError("이미 학습이 진행 중입니다.")
            if self._log is not None:
                self._log.close()
            job_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
            job_path = self.state / f"{job_id}.job.json"
            status_path = self.state / f"{job_id}.status.json"
            log_path = self.state / f"{job_id}.log"
            job = {
                "job_id": job_id, "dataset_yaml": str(dataset), "base_model": str(base),
                "runs": str(self.runs), "status_path": str(status_path),
                "epochs": int(epochs), "image_size": int(image_size), "batch": int(batch), "device": "cpu",
            }
            job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
            self._log = log_path.open("ab")
            self._process = subprocess.Popen(
                [sys.executable, str(self.worker_path), "--job", str(job_path)],
                cwd=str(self.worker_path.parent), stdout=self._log, stderr=subprocess.STDOUT,
            )
            self._job_id = job_id
        return {"success": True, "status": "PREPARING", "job_id": job_id, "device": "cpu", "message": "CPU 학습을 시작했습니다. 오래 걸릴 수 있습니다."}

    def _latest_job(self) -> str | None:
        if self._job_id:
            return self._job_id
        paths = sorted(self.state.glob("*.job.json"), key=lambda path: path.stat().st_mtime) if self.state.is_dir() else []
        return paths[-1].name.removesuffix(".job.json") if paths else None

    def status(self) -> dict[str, Any]:
        self._ensure()
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            if self._process is not None and not running and self._log is not None:
                self._log.close(); self._log = None
            job_id = self._latest_job()
        if not job_id:
            return {"status": "IDLE", "running": False, "device": "cpu", "job_id": None}
        value: dict[str, Any] = {}
        status_path = self.state / f"{job_id}.status.json"
        if status_path.is_file():
            try:
                value = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        log_path = self.state / f"{job_id}.log"
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:] if log_path.is_file() else ""
        return {"status": "TRAINING" if running and not value else value.get("status", "FAILED"), "running": running, "device": "cpu", "job_id": job_id, "log_tail": tail, **value}

    def list_models(self) -> list[dict[str, Any]]:
        self._ensure()
        active = self.active_model_path()
        candidates = list(self.models.glob("*.pt")) + list(self.runs.glob("*/weights/best.pt"))
        result = []
        for path in sorted(candidates, key=lambda value: value.stat().st_mtime, reverse=True):
            model_id = path.stem if path.parent == self.models else path.parents[1].name
            result.append({
                "id": model_id, "name": path.name if path.parent == self.models else f"{model_id}/best.pt",
                "path": str(path.resolve()), "active": active == path.resolve(), "size_bytes": path.stat().st_size,
            })
        return result

    def import_model(self, source: str | Path, original_name: str, adapter) -> dict[str, Any]:
        source = Path(source).resolve()
        validated = adapter.validate_model(source)
        self._ensure()
        safe_stem = "".join(char for char in Path(original_name).stem if char.isalnum() or char in "_-")[:40] or "best"
        target = self.models / f"{safe_stem}_{uuid.uuid4().hex[:8]}.pt"
        shutil.copy2(source, target)
        return {"success": True, "model_id": target.stem, "name": target.name, "path": str(target), "class_mapping": validated["class_mapping"]}

    def activate(self, model_id: str, adapter) -> dict[str, Any]:
        selected = next((item for item in self.list_models() if item["id"] == model_id), None)
        if not selected:
            raise YoloTrainingError("등록된 모델을 찾을 수 없습니다.")
        result = adapter.load_model(selected["path"])
        state_path = self.state / "active_model.json"
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"model_id": model_id, "path": selected["path"]}, indent=2), encoding="utf-8")
        temporary.replace(state_path)
        return {"success": True, "model_id": model_id, **result}

    def active_model_path(self) -> Path | None:
        path = self.state / "active_model.json"
        if not path.is_file():
            return None
        try:
            model = Path(json.loads(path.read_text(encoding="utf-8"))["path"]).resolve()
            return model if model.is_file() else None
        except (OSError, json.JSONDecodeError, KeyError):
            return None
