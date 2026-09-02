# YOLO Part Inspection MVP

The Work Order / Inspection Camera is the single C920 capture source for OCR,
dataset capture, OpenCV fallback, and YOLO inference. YOLO code never opens a
second `cv2.VideoCapture`.

## UI workflow

1. Open **Camera Settings → Work Order / Inspection Camera → YOLO**.
2. In **이미지 촬영**, select a class and capture several naturally rotated,
   shifted, and illuminated examples. Use at least two capture groups.
3. In **Bounding Box 라벨링**, draw every object box. Move a box by dragging
   it and resize it with its lower-right handle. Save as `MANUAL`, or inspect an
   automatic prediction and save it as `REVIEWED`.
4. Mark a deliberately empty image as `BACKGROUND`; do not use that state for
   an image that still needs labeling.
5. In **검증 / Train·Val**, correct every reported image and create the split.
6. Export `part_yolo_dataset.zip` for external GPU training, or select an
   approved local base weight for slower CPU training.
7. Import the resulting `best.pt`, activate it, then use **Live Detection**.

## Colab training

Upload and extract `part_yolo_dataset.zip`. Its root contains `dataset.yaml`
and ready-to-use `images/{train,val}` and `labels/{train,val}` directories.
After installing a compatible Ultralytics version in Colab, train a detection
model with that YAML. Download the generated `best.pt` and import it from the
tray_system UI. Colab integration is intentionally file-based; there is no
cloud account or remote training API in the backend.

Minimal Colab cells (the model download happens in Colab when the user runs
the final cell):

```python
!pip install ultralytics==8.3.239
!unzip -q /content/part_yolo_dataset.zip -d /content/part_yolo_dataset

from ultralytics import YOLO
model = YOLO("yolo11n.pt")
model.train(
    data="/content/part_yolo_dataset/dataset.yaml",
    epochs=50,
    imgsz=640,
)
```

`yolo11n.pt` is the small detection default referenced by the installed
Ultralytics 8.3.239 package. It is not bundled or downloaded by tray_system.

## Seed and auto-label loop

Start with a small, manually reviewed seed set containing all six classes.
After the first model is active, capture more images with **Auto Label**.
Automatic boxes remain `AUTO_UNREVIEWED` and are excluded from validation and
training until a person adjusts/deletes/adds boxes and saves `REVIEWED`.

## Runtime storage

All images, labels, generated datasets, ZIP exports, runs, and weights live
under `data/part_yolo/` and are ignored by Git. Existing OpenCV references in
`captures/part_inspection/` and `backend/data/part_classifier_profile.json`
remain independent and unchanged.
