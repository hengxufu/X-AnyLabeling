#!/usr/bin/env python3
"""Batch-export X-AnyLabeling polygon JSON files to grayscale PNG masks.

This is a small CLI wrapper around the repository's existing
``LabelConverter.custom_to_mask`` implementation.  It additionally writes a
valid all-zero PNG for empty annotations, which the GUI exporter currently
skips.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export X-AnyLabeling polygon annotations as binary grayscale "
            "PNG masks (foreground=255, background=0)."
        )
    )
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--images",
        type=Path,
        help="Optional image root used to report images with no JSON label.",
    )
    parser.add_argument(
        "--foreground-label",
        default="spacecraft",
        help="Polygon label to render as foreground (default: spacecraft).",
    )
    parser.add_argument(
        "--missing-report",
        type=Path,
        help="Optional text report for missing labels/images and export failures.",
    )
    parser.add_argument(
        "--empty-report",
        type=Path,
        help="Optional text report listing JSON files with no shapes.",
    )
    return parser.parse_args()


def image_stem(data: dict, json_file: Path) -> str:
    image_path = str(data.get("imagePath") or "").replace("\\", "/")
    return Path(image_path).stem if image_path else json_file.stem


def write_lines(path: Path | None, lines: list[str]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM keeps reports readable in Windows PowerShell 5.1/Get-Content.
    path.write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8-sig"
    )


def clamp_point(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    x = min(max(int(round(x)), 0), width - 1)
    y = min(max(int(round(y)), 0), height - 1)
    return x, y


def shape_to_contour(
    shape: dict, width: int, height: int, foreground_label: str
) -> np.ndarray | None:
    if str(shape.get("label") or "") != foreground_label:
        return None

    shape_type = str(shape.get("shape_type") or "")
    points = shape.get("points") or []

    if shape_type == "polygon":
        if len(points) < 3:
            return None
        contour = [clamp_point(x, y, width, height) for x, y in points]
        return np.array(contour, dtype=np.int32)

    if shape_type == "rectangle":
        if len(points) == 2:
            (x0, y0), (x1, y1) = points
            left, right = sorted((x0, x1))
            top, bottom = sorted((y0, y1))
            contour = [
                clamp_point(left, top, width, height),
                clamp_point(right, top, width, height),
                clamp_point(right, bottom, width, height),
                clamp_point(left, bottom, width, height),
            ]
            return np.array(contour, dtype=np.int32)
        if len(points) >= 4:
            contour = [
                clamp_point(x, y, width, height) for x, y in points[:4]
            ]
            return np.array(contour, dtype=np.int32)
    return None


def write_mask_from_shapes(
    output_file: Path, data: dict, foreground_label: str
) -> tuple[bool, Counter[str], Counter[str]]:
    width = int(data["imageWidth"])
    height = int(data["imageHeight"])
    mask = np.zeros((height, width), dtype=np.uint8)

    contours: list[np.ndarray] = []
    ignored_shape_types: Counter[str] = Counter()
    ignored_labels: Counter[str] = Counter()

    for shape in data.get("shapes") or []:
        shape_type = str(shape.get("shape_type") or "")
        label = str(shape.get("label") or "")

        if label != foreground_label:
            ignored_labels[label or "<missing>"] += 1
            continue

        contour = shape_to_contour(shape, width, height, foreground_label)
        if contour is None:
            ignored_shape_types[shape_type or "<missing>"] += 1
            continue
        contours.append(contour)

    if contours:
        contours.sort(key=cv2.contourArea, reverse=True)
        for contour in contours:
            cv2.fillPoly(mask, [contour], 255)

    ok, png = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError("OpenCV failed to encode PNG mask")
    output_file.write_bytes(png.tobytes())
    return bool(contours), ignored_shape_types, ignored_labels


def main() -> int:
    args = parse_args()
    labels_root = args.labels.resolve()
    output_root = args.output.resolve()

    if not labels_root.is_dir():
        raise SystemExit(f"Label directory does not exist: {labels_root}")
    if args.images is not None and not args.images.is_dir():
        raise SystemExit(f"Image directory does not exist: {args.images}")

    output_root.mkdir(parents=True, exist_ok=True)
    label_files = sorted(labels_root.rglob("*.json"))
    mapping = {
        "type": "grayscale",
        "colors": {args.foreground_label: 255},
    }

    exported = 0
    foreground_masks = 0
    empty_annotations: list[str] = []
    failures: list[str] = []
    label_stems: set[str] = set()
    duplicate_output_stems: list[str] = []
    output_stems: set[str] = set()
    ignored_shape_types: Counter[str] = Counter()
    ignored_labels: Counter[str] = Counter()
    blank_png_cache: dict[tuple[int, int], bytes] = {}

    for json_file in label_files:
        try:
            data = json.loads(json_file.read_text(encoding="utf-8-sig"))
            width = int(data["imageWidth"])
            height = int(data["imageHeight"])
            if width <= 0 or height <= 0:
                raise ValueError(f"invalid image size {width}x{height}")

            stem = image_stem(data, json_file)
            stem_key = stem.casefold()
            label_stems.add(stem_key)
            if stem_key in output_stems:
                duplicate_output_stems.append(f"{stem}\t{json_file}")
                continue
            output_stems.add(stem_key)

            shapes = data.get("shapes") or []
            if not shapes:
                empty_annotations.append(f"{stem}\t{json_file}")

            output_file = output_root / f"{stem}.png"
            has_foreground, shape_type_counts, label_counts = write_mask_from_shapes(
                output_file=output_file,
                data=data,
                foreground_label=args.foreground_label,
            )
            ignored_shape_types.update(shape_type_counts)
            ignored_labels.update(label_counts)

            if has_foreground:
                foreground_masks += 1
                mask = cv2.imdecode(
                    np.fromfile(output_file, dtype=np.uint8),
                    cv2.IMREAD_GRAYSCALE,
                )
                if mask is None:
                    raise RuntimeError("written PNG cannot be decoded")
                if mask.shape != (height, width):
                    raise RuntimeError(
                        f"mask size {mask.shape[1]}x{mask.shape[0]} "
                        f"!= {width}x{height}"
                    )
                values = set(int(v) for v in np.unique(mask))
                if not values.issubset({0, 255}):
                    raise RuntimeError(
                        f"unexpected grayscale values: {sorted(values)}"
                    )
            exported += 1
        except Exception as exc:  # Continue so one bad JSON does not stop the batch.
            failures.append(f"{json_file}\t{type(exc).__name__}: {exc}")

    missing_label_images: list[str] = []
    labels_without_images: list[str] = []
    if args.images is not None:
        image_files = sorted(
            path
            for path in args.images.rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_EXTENSIONS
        )
        image_stems = {path.stem.casefold() for path in image_files}
        missing_label_images = [
            str(path) for path in image_files if path.stem.casefold() not in label_stems
        ]
        labels_without_images = sorted(label_stems - image_stems)

    report: list[str] = []
    report.append(f"[images_without_json] count={len(missing_label_images)}")
    report.extend(missing_label_images)
    report.append("")
    report.append(f"[json_without_image] count={len(labels_without_images)}")
    report.extend(labels_without_images)
    report.append("")
    report.append(f"[duplicate_output_stems] count={len(duplicate_output_stems)}")
    report.extend(duplicate_output_stems)
    report.append("")
    report.append(f"[export_failures] count={len(failures)}")
    report.extend(failures)
    write_lines(args.missing_report, report)
    write_lines(args.empty_report, empty_annotations)

    summary = {
        "labels": str(labels_root),
        "output": str(output_root),
        "json_files": len(label_files),
        "exported_png": exported,
        "masks_with_foreground": foreground_masks,
        "empty_annotations": len(empty_annotations),
        "images_without_json": len(missing_label_images),
        "json_without_image": len(labels_without_images),
        "duplicate_output_stems": len(duplicate_output_stems),
        "export_failures": len(failures),
        "ignored_shape_types": dict(ignored_shape_types),
        "ignored_labels": dict(ignored_labels),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures or duplicate_output_stems else 0


if __name__ == "__main__":
    raise SystemExit(main())
