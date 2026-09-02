from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import traceback


def write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def serializable_metrics(values) -> dict[str, float]:
    result = {}
    if isinstance(values, dict):
        for key, value in values.items():
            try: result[str(key)] = float(value)
            except (TypeError, ValueError): pass
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    job = json.loads(Path(args.job).read_text(encoding="utf-8"))
    status_path = Path(job["status_path"])
    started = time.monotonic()
    state = {"status": "PREPARING", "job_id": job["job_id"], "epoch": 0, "epochs": job["epochs"], "device": "cpu", "started_at": datetime.now(timezone.utc).isoformat()}
    write(status_path, state)
    try:
        from ultralytics import YOLO
        model = YOLO(job["base_model"])

        def epoch_end(trainer):
            state.update({"status": "TRAINING", "epoch": int(getattr(trainer, "epoch", 0)) + 1, "elapsed_seconds": round(time.monotonic() - started, 1), "metrics": serializable_metrics(getattr(trainer, "metrics", {}))})
            write(status_path, state)

        model.add_callback("on_train_epoch_end", epoch_end)
        result = model.train(data=job["dataset_yaml"], epochs=job["epochs"], imgsz=job["image_size"], batch=job["batch"], device="cpu", workers=0, project=job["runs"], name=job["job_id"], exist_ok=True)
        save_dir = Path(getattr(result, "save_dir", Path(job["runs"]) / job["job_id"]))
        best = save_dir / "weights" / "best.pt"
        state.update({"status": "COMPLETED", "epoch": job["epochs"], "elapsed_seconds": round(time.monotonic() - started, 1), "best_model": str(best) if best.is_file() else None, "metrics": serializable_metrics(getattr(result, "results_dict", {}))})
        write(status_path, state)
        return 0
    except Exception as error:
        state.update({"status": "FAILED", "elapsed_seconds": round(time.monotonic() - started, 1), "error": f"{type(error).__name__}: {error}"})
        write(status_path, state); traceback.print_exc(); return 1


if __name__ == "__main__":
    raise SystemExit(main())
