"""Validate the single-class staff-vest YOLO dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from PIL import Image


IMAGE_EXTENSIONS = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}
SPLITS = ("train", "val", "test")


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    split: str
    width: int
    height: int
    sha256: str
    perceptual_hash: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset_positional",
        nargs="?",
        type=Path,
    )
    parser.add_argument("--dataset", dest="dataset_flag", type=Path)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--min-box-pixels", type=float, default=8.0)
    parser.add_argument("--similarity-distance", type=int, default=4)
    parser.add_argument("--fail-on-similar", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.dataset_positional is not None and args.dataset_flag is not None:
        parser.error("provide the dataset either positionally or with --dataset, not both")
    args.dataset = (
        args.dataset_flag
        or args.dataset_positional
        or Path("data/staff_vest_dataset")
    )
    return args


def image_hash(image: Image.Image) -> int:
    resized = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = list(resized.get_flattened_data())
    value = 0
    for y in range(8):
        start = y * 9
        for x in range(8):
            value = (value << 1) | int(pixels[start + x] > pixels[start + x + 1])
    return value


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 4)


def distribution(values: list[float]) -> dict[str, float | None]:
    return {
        "min": percentile(values, 0),
        "p25": percentile(values, 0.25),
        "median": percentile(values, 0.5),
        "p75": percentile(values, 0.75),
        "max": percentile(values, 1),
    }


def resolve_dataset(dataset_argument: Path) -> tuple[Path, Path, dict[str, Any]]:
    yaml_path = dataset_argument if dataset_argument.suffix.lower() in {".yaml", ".yml"} else dataset_argument / "data.yaml"
    if not yaml_path.is_file():
        raise ValueError(f"data.yaml does not exist: {yaml_path}")
    try:
        payload = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(f"cannot read data.yaml: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("data.yaml must contain a mapping")
    configured_root = Path(str(payload.get("path", yaml_path.parent)))
    root = configured_root if configured_root.is_absolute() else yaml_path.parent / configured_root
    return root.resolve(), yaml_path.resolve(), payload


def expected_class_names(payload: dict[str, Any]) -> bool:
    names = payload.get("names")
    if isinstance(names, list):
        return names == ["staff_vest"]
    if isinstance(names, dict):
        normalized = {str(key): value for key, value in names.items()}
        return normalized == {"0": "staff_vest"}
    return False


def label_path_for(root: Path, split: str, image_root: Path, image: Path) -> Path:
    relative = image.relative_to(image_root).with_suffix(".txt")
    return root / "labels" / split / relative


def load_image(path: Path, split: str) -> ImageRecord:
    content = path.read_bytes()
    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        if width < 1 or height < 1:
            raise ValueError("image has invalid dimensions")
        return ImageRecord(
            path=path,
            split=split,
            width=width,
            height=height,
            sha256=hashlib.sha256(content).hexdigest(),
            perceptual_hash=image_hash(image),
        )


def validate_label(
    label_path: Path,
    image: ImageRecord,
    *,
    imgsz: int,
    min_box_pixels: float,
) -> tuple[list[str], list[tuple[float, float, float]]]:
    errors: list[str] = []
    boxes: list[tuple[float, float, float]] = []
    try:
        lines = label_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return [f"cannot read label {label_path}: {error}"], boxes
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        location = f"{label_path}:{line_number}"
        if len(parts) != 5:
            errors.append(f"{location}: expected five YOLO fields")
            continue
        try:
            class_value = float(parts[0])
            x_center, y_center, width, height = (float(value) for value in parts[1:])
        except ValueError:
            errors.append(f"{location}: fields must be numeric")
            continue
        values = (class_value, x_center, y_center, width, height)
        if not all(math.isfinite(value) for value in values):
            errors.append(f"{location}: fields must be finite")
            continue
        if class_value != 0:
            errors.append(f"{location}: class id must be 0 (staff_vest)")
        if not 0 <= x_center <= 1 or not 0 <= y_center <= 1:
            errors.append(f"{location}: box center must be normalized to [0, 1]")
        if not 0 < width <= 1 or not 0 < height <= 1:
            errors.append(f"{location}: box width and height must be in (0, 1]")
        if (
            x_center - width / 2 < -1e-6
            or x_center + width / 2 > 1 + 1e-6
            or y_center - height / 2 < -1e-6
            or y_center + height / 2 > 1 + 1e-6
        ):
            errors.append(f"{location}: box extends outside the normalized image")
        if errors and errors[-1].startswith(location):
            continue
        resize_scale = min(imgsz / image.width, imgsz / image.height)
        scaled_width = width * image.width * resize_scale
        scaled_height = height * image.height * resize_scale
        if scaled_width < min_box_pixels or scaled_height < min_box_pixels:
            errors.append(
                f"{location}: target is too small after resize "
                f"({scaled_width:.2f}x{scaled_height:.2f}px < {min_box_pixels:g}px)"
            )
        boxes.append((scaled_width, scaled_height, width * height))
    return errors, boxes


def validate_manifest(root: Path, records: list[ImageRecord]) -> tuple[list[str], int]:
    manifest_path = root / "split_manifest.json"
    if not manifest_path.exists():
        return [], 0
    errors: list[str] = []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"cannot read split manifest: {error}"], 0
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return ["split_manifest.json must contain an entries list"], 0
    batches: dict[str, set[str]] = defaultdict(set)
    manifest_images: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"split manifest entry {index} must be an object")
            continue
        relative = str(entry.get("dataset_image", "")).replace("\\", "/")
        split = str(entry.get("split", ""))
        batch_id = str(entry.get("batch_id", ""))
        if not relative or split not in SPLITS or not batch_id:
            errors.append(f"split manifest entry {index} is missing image, split, or batch_id")
            continue
        if relative in manifest_images:
            errors.append(f"split manifest repeats image: {relative}")
        manifest_images.add(relative)
        batches[batch_id].add(split)
        if not relative.startswith(f"images/{split}/"):
            errors.append(
                f"split manifest path disagrees with split: {relative} -> {split}"
            )
        if not (root / relative).is_file():
            errors.append(f"split manifest image does not exist: {relative}")
    leaks = 0
    for batch_id, splits in sorted(batches.items()):
        if len(splits) > 1:
            leaks += 1
            errors.append(
                f"capture batch crosses dataset splits: {batch_id} -> {sorted(splits)}"
            )
    actual_images = {
        str(record.path.relative_to(root)).replace("\\", "/") for record in records
    }
    missing_entries = sorted(actual_images - manifest_images)
    if missing_entries:
        errors.append(f"split manifest is missing {len(missing_entries)} dataset images")
    return errors, leaks


def validate_dataset(
    dataset_argument: Path,
    *,
    imgsz: int,
    min_box_pixels: float,
    similarity_distance: int,
    fail_on_similar: bool,
) -> dict[str, Any]:
    if imgsz < 32:
        raise ValueError("--imgsz must be at least 32")
    if min_box_pixels <= 0:
        raise ValueError("--min-box-pixels must be greater than zero")
    if not 0 <= similarity_distance <= 64:
        raise ValueError("--similarity-distance must be between 0 and 64")
    root, yaml_path, payload = resolve_dataset(dataset_argument)
    errors: list[str] = []
    warnings: list[str] = []
    if payload.get("nc") not in (None, 1):
        errors.append("data.yaml nc must be 1")
    if not expected_class_names(payload):
        errors.append("data.yaml names must define only class 0: staff_vest")

    records: list[ImageRecord] = []
    split_counts: dict[str, dict[str, int]] = {}
    missing_labels = 0
    orphan_labels = 0
    empty_labels = 0
    damaged_images = 0
    scaled_widths: list[float] = []
    scaled_heights: list[float] = []
    normalized_areas: list[float] = []

    for split in SPLITS:
        configured = payload.get(split)
        if not isinstance(configured, str) or not configured.strip():
            errors.append(f"data.yaml must define {split}")
            split_counts[split] = {"images": 0, "labels": 0, "empty_labels": 0, "boxes": 0}
            continue
        image_root = Path(configured)
        image_root = image_root if image_root.is_absolute() else root / image_root
        label_root = root / "labels" / split
        images = sorted(
            path
            for path in image_root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ) if image_root.is_dir() else []
        labels = sorted(label_root.rglob("*.txt")) if label_root.is_dir() else []
        expected_labels: set[Path] = set()
        split_empty = 0
        split_boxes = 0
        if not image_root.is_dir():
            errors.append(f"image split directory does not exist: {image_root}")
        for image_path in images:
            try:
                record = load_image(image_path, split)
                records.append(record)
            except (OSError, ValueError, Image.UnidentifiedImageError) as error:
                damaged_images += 1
                errors.append(f"damaged image {image_path}: {error}")
                continue
            label_path = label_path_for(root, split, image_root, image_path)
            expected_labels.add(label_path.resolve())
            if not label_path.is_file():
                missing_labels += 1
                errors.append(f"missing label for image: {image_path}")
                continue
            label_errors, boxes = validate_label(
                label_path,
                record,
                imgsz=imgsz,
                min_box_pixels=min_box_pixels,
            )
            errors.extend(label_errors)
            try:
                is_empty = not label_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                is_empty = False
            if is_empty:
                empty_labels += 1
                split_empty += 1
            split_boxes += len(boxes)
            for width, height, area in boxes:
                scaled_widths.append(width)
                scaled_heights.append(height)
                normalized_areas.append(area)
        for label_path in labels:
            if label_path.resolve() not in expected_labels:
                orphan_labels += 1
                errors.append(f"label has no matching image: {label_path}")
        split_counts[split] = {
            "images": len(images),
            "labels": len(labels),
            "empty_labels": split_empty,
            "boxes": split_boxes,
        }

    if not scaled_widths:
        errors.append("dataset contains no staff_vest bounding boxes")

    exact_groups: dict[str, list[ImageRecord]] = defaultdict(list)
    for record in records:
        exact_groups[record.sha256].append(record)
    duplicate_groups = 0
    cross_split_leaks = 0
    for group in exact_groups.values():
        if len(group) < 2:
            continue
        duplicate_groups += 1
        splits = {record.split for record in group}
        paths = [str(record.path) for record in group]
        if len(splits) > 1:
            cross_split_leaks += 1
            errors.append(f"duplicate content crosses splits: {paths}")
        else:
            errors.append(f"duplicate content within {group[0].split}: {paths}")

    similar_pairs: list[dict[str, Any]] = []
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            if left.split == right.split or left.sha256 == right.sha256:
                continue
            distance = (left.perceptual_hash ^ right.perceptual_hash).bit_count()
            if distance <= similarity_distance:
                similar_pairs.append(
                    {
                        "left": str(left.path.relative_to(root)).replace("\\", "/"),
                        "right": str(right.path.relative_to(root)).replace("\\", "/"),
                        "hamming_distance": distance,
                    }
                )
    if similar_pairs:
        message = f"found {len(similar_pairs)} perceptually similar cross-split pairs"
        if fail_on_similar:
            errors.append(message)
            cross_split_leaks += len(similar_pairs)
        else:
            warnings.append(message)

    manifest_errors, manifest_leaks = validate_manifest(root, records)
    errors.extend(manifest_errors)
    cross_split_leaks += manifest_leaks
    return {
        "dataset": str(root),
        "data_yaml": str(yaml_path),
        "valid": not errors,
        "summary": {
            "images": len(records),
            "labels": sum(value["labels"] for value in split_counts.values()),
            "empty_labels": empty_labels,
            "missing_labels": missing_labels,
            "orphan_labels": orphan_labels,
            "damaged_images": damaged_images,
            "boxes": len(scaled_widths),
            "duplicate_groups": duplicate_groups,
            "cross_split_leaks": cross_split_leaks,
            "similar_cross_split_pairs": len(similar_pairs),
        },
        "splits": split_counts,
        "bbox_distribution": {
            "scaled_width_px": distribution(scaled_widths),
            "scaled_height_px": distribution(scaled_heights),
            "normalized_area": distribution(normalized_areas),
        },
        "similar_pairs": similar_pairs[:100],
        "warnings": warnings,
        "errors": errors,
    }


def main() -> int:
    args = parse_args()
    try:
        report = validate_dataset(
            args.dataset,
            imgsz=args.imgsz,
            min_box_pixels=args.min_box_pixels,
            similarity_distance=args.similarity_distance,
            fail_on_similar=args.fail_on_similar,
        )
        output = json.dumps(report, ensure_ascii=True, indent=2)
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0 if report["valid"] else 1
    except (OSError, ValueError) as error:
        print(json.dumps({"valid": False, "errors": [str(error)]}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
