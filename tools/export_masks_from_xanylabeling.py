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

from anylabeling.views.labeling.label_converter import LabelConverter  # noqa: E402


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
    converter = LabelConverter()
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

            usable_polygon_count = 0
            for shape in shapes:
                shape_type = str(shape.get("shape_type") or "")
                label = str(shape.get("label") or "")
                points = shape.get("points") or []
                if shape_type != "polygon":
                    ignored_shape_types[shape_type or "<missing>"] += 1
                elif label != args.foreground_label:
                    ignored_labels[label or "<missing>"] += 1
                elif len(points) >= 3:
                    usable_polygon_count += 1

            output_file = output_root / f"{stem}.png"
            if usable_polygon_count:
                converter.custom_to_mask(json_file, output_file, mapping)
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
            else:
                cache_key = (width, height)
                encoded = blank_png_cache.get(cache_key)
                if encoded is None:
                    blank = np.zeros((height, width), dtype=np.uint8)
                    ok, png = cv2.imencode(".png", blank)
                    if not ok:
                        raise RuntimeError("OpenCV failed to encode blank PNG")
                    encoded = png.tobytes()
                    blank_png_cache[cache_key] = encoded
                output_file.write_bytes(encoded)
                if output_file.stat().st_size != len(encoded):
                    raise RuntimeError("written blank PNG has an unexpected size")
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
