"""Interactive calibration for behavior polygons."""

from __future__ import annotations

import argparse
import json
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from agent.behavior_monitor import PolygonArea
from agent.behavior_vision import BehaviorCameraConfig, draw_behavior_area_overlay


Point = tuple[int, int]
NormalizedPoint = tuple[float, float]


@dataclass
class AreaSelection:
    name: str
    points: list[Point] = field(default_factory=list)

    def add(self, point: Point) -> None:
        if (
            len(point) != 2
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in point
            )
        ):
            raise ValueError("ROI point must contain non-negative pixel coordinates")
        self.points.append(point)

    def undo(self) -> Point | None:
        return self.points.pop() if self.points else None


def normalize_points(
    points: Sequence[Point],
    *,
    image_width: int,
    image_height: int,
) -> list[NormalizedPoint]:
    if (
        isinstance(image_width, bool)
        or isinstance(image_height, bool)
        or not isinstance(image_width, int)
        or not isinstance(image_height, int)
        or image_width <= 0
        or image_height <= 0
    ):
        raise ValueError("image dimensions must be positive integers")
    normalized = []
    for x, y in points:
        if not 0 <= x <= image_width or not 0 <= y <= image_height:
            raise ValueError("ROI point lies outside configured image dimensions")
        normalized.append((x / image_width, y / image_height))
    return normalized


def validate_polygon(points: Sequence[Point | NormalizedPoint]) -> None:
    if len(points) < 3:
        raise ValueError("polygon requires at least three points")
    if len(set(points)) != len(points):
        raise ValueError("polygon points must be unique")
    if any(
        len(point) != 2
        or not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            for value in point
        )
        for point in points
    ):
        raise ValueError("polygon points must contain finite coordinates")
    if _polygon_self_intersects(points):
        raise ValueError("polygon must not self-intersect")
    area = sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )
    if abs(area) < 1e-9:
        raise ValueError("polygon must have non-zero area")


def warn_if_image_size_mismatch(
    actual_size: tuple[int, int],
    configured_size: tuple[int, int],
) -> None:
    if actual_size != configured_size:
        warnings.warn(
            f"source image size {actual_size[0]}x{actual_size[1]} does not match "
            f"configured size {configured_size[0]}x{configured_size[1]}",
            RuntimeWarning,
            stacklevel=2,
        )


def save_area_config(
    *,
    output: str | Path,
    image_path: str,
    image_width: int,
    image_height: int,
    entry_points: Sequence[Point],
    greeting_points: Sequence[Point],
) -> dict:
    validate_polygon(entry_points)
    validate_polygon(greeting_points)
    normalized_entry = normalize_points(
        entry_points,
        image_width=image_width,
        image_height=image_height,
    )
    normalized_greeting = normalize_points(
        greeting_points,
        image_width=image_width,
        image_height=image_height,
    )
    source = np.array(normalized_entry, dtype=np.float32)
    if source.shape != (4, 2):
        raise ValueError("entry_trigger requires exactly four A/B/D/C points")
    destination = np.array(
        [(0.0, 0.0), (3.7, 0.0), (3.7, 1.0), (0.0, 1.0)],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(source, destination)
    if not np.all(np.isfinite(homography)) or abs(float(np.linalg.det(homography))) < 1e-12:
        raise ValueError("selected entry points cannot produce an invertible homography")
    payload = {
        "image": str(image_path),
        "calibration_status": "provisional",
        "calibration_image_size": [image_width, image_height],
        "entry_trigger": {
            "area_id": "front-entry-trigger",
            "points": [list(point) for point in normalized_entry],
        },
        "greeting_area": {
            "area_id": "front-greeting-area",
            "points": [list(point) for point in normalized_greeting],
        },
        "ground_homography": [float(value) for value in homography.reshape(-1)],
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def save_single_area_config(
    *,
    output: str | Path,
    image_path: str,
    image_width: int,
    image_height: int,
    area_id: str,
    area_type: str,
    points: Sequence[Point],
) -> dict:
    if not isinstance(area_id, str) or not area_id.strip():
        raise ValueError("area_id is required")
    if area_type not in {"interaction", "display"}:
        raise ValueError(
            "single-area calibration currently supports interaction or display"
        )
    validate_polygon(points)
    normalized = normalize_points(
        points,
        image_width=image_width,
        image_height=image_height,
    )
    payload = {
        "image": str(image_path),
        "calibration_image_size": [image_width, image_height],
        area_id: {
            "area_type": area_type,
            "pixel_points": [list(point) for point in points],
            "points": [list(point) for point in normalized],
        },
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def save_single_area_overlay(
    *,
    image: np.ndarray,
    output: str | Path,
    area_id: str,
    area_type: str,
    normalized_points: Sequence[NormalizedPoint],
) -> Path:
    area = PolygonArea(
        area_id=area_id,
        area_type=area_type,
        points=tuple((float(x), float(y)) for x, y in normalized_points),
    )
    camera = BehaviorCameraConfig(
        camera_id="calibration-preview",
        store_id="calibration-preview",
        source="calibration-preview",
        entry_trigger=None,
        greeting_area=None,
        interaction_areas=(area,) if area_type == "interaction" else (),
        display_areas=(area,) if area_type == "display" else (),
        ground_projector=None,
        calibration_image_size=(int(image.shape[1]), int(image.shape[0])),
    )
    rendered = draw_behavior_area_overlay(image, camera)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(target), rendered):
        raise RuntimeError(f"cannot write overlay: {target}")
    return target


def _polygon_self_intersects(
    points: Sequence[Point | NormalizedPoint],
) -> bool:
    count = len(points)
    for left_index in range(count):
        left = (points[left_index], points[(left_index + 1) % count])
        for right_index in range(left_index + 1, count):
            if right_index in {
                left_index,
                (left_index + 1) % count,
                (left_index - 1) % count,
            }:
                continue
            right = (points[right_index], points[(right_index + 1) % count])
            if _segments_intersect(*left, *right):
                return True
    return False


def _segments_intersect(start_a, end_a, start_b, end_b) -> bool:
    def orientation(start, end, point) -> float:
        return (
            (end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0])
        )

    values = (
        orientation(start_a, end_a, start_b),
        orientation(start_a, end_a, end_b),
        orientation(start_b, end_b, start_a),
        orientation(start_b, end_b, end_a),
    )
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True

    def on_segment(point, start, end) -> bool:
        return (
            min(start[0], end[0]) <= point[0] <= max(start[0], end[0])
            and min(start[1], end[1]) <= point[1] <= max(start[1], end[1])
        )

    return any(
        abs(value) < 1e-9 and on_segment(point, start, end)
        for value, point, start, end in (
            (values[0], start_b, start_a, end_a),
            (values[1], end_b, start_a, end_a),
            (values[2], start_a, start_b, end_b),
            (values[3], end_a, start_b, end_b),
        )
    )


def _collect_polygon(
    image: np.ndarray,
    name: str,
    color: tuple[int, int, int],
    *,
    image_width: int,
    image_height: int,
):
    selection = AreaSelection(name)
    window_name = f"Calibrate {name}"

    def on_mouse(event, x, y, _flags, _parameter):
        if event == cv2.EVENT_LBUTTONDOWN:
            selection.add((int(x), int(y)))
        elif event == cv2.EVENT_RBUTTONDOWN:
            selection.undo()

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window_name, on_mouse)
    while True:
        rendered = image.copy()
        if selection.points:
            points = np.array(selection.points, dtype=np.int32)
            cv2.polylines(rendered, [points], len(points) >= 3, color, 2)
            for index, point in enumerate(selection.points):
                cv2.circle(rendered, point, 5, color, -1)
                cv2.putText(
                    rendered,
                    (
                        f"{index + 1}: "
                        f"({point[0] / image_width:.6f},"
                        f"{point[1] / image_height:.6f})"
                    ),
                    (point[0] + 6, point[1] - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    color,
                    2,
                    cv2.LINE_AA,
                )
        cv2.putText(
            rendered,
            f"{name}: left click add, U/right click undo, Enter finish, Esc cancel",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            color,
            2,
            cv2.LINE_AA,
        )
        cv2.imshow(window_name, rendered)
        key = cv2.waitKey(30) & 0xFF
        if key in (ord("u"), ord("U"), 8):
            selection.undo()
        elif key in (10, 13):
            validate_polygon(selection.points)
            cv2.destroyWindow(window_name)
            return list(selection.points)
        elif key == 27:
            cv2.destroyWindow(window_name)
            raise KeyboardInterrupt("ROI calibration cancelled")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-width", required=True, type=int)
    parser.add_argument("--image-height", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--area-id")
    parser.add_argument("--area-type", choices=("interaction", "display"))
    parser.add_argument("--overlay-output")
    args = parser.parse_args(argv)

    image = cv2.imread(args.image)
    if image is None:
        parser.error(f"cannot read image: {args.image}")
    actual_size = (int(image.shape[1]), int(image.shape[0]))
    configured_size = (args.image_width, args.image_height)
    warn_if_image_size_mismatch(actual_size, configured_size)
    single_area_requested = any(
        value is not None
        for value in (args.area_id, args.area_type, args.overlay_output)
    )
    if single_area_requested:
        if not all(
            value is not None
            for value in (args.area_id, args.area_type, args.overlay_output)
        ):
            parser.error(
                "--area-id, --area-type, and --overlay-output are required together"
            )
        print(
            f"Select {args.area_id} {args.area_type} polygon in perimeter order."
        )
        points = _collect_polygon(
            image,
            args.area_id,
            (0, 190, 255),
            image_width=args.image_width,
            image_height=args.image_height,
        )
        payload = save_single_area_config(
            output=args.output,
            image_path=args.image,
            image_width=args.image_width,
            image_height=args.image_height,
            area_id=args.area_id,
            area_type=args.area_type,
            points=points,
        )
        save_single_area_overlay(
            image=image,
            output=args.overlay_output,
            area_id=args.area_id,
            area_type=args.area_type,
            normalized_points=payload[args.area_id]["points"],
        )
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        print(f"Overlay: {args.overlay_output}")
        return 0
    print("Select entry_trigger in A/B/D/C perimeter order.")
    entry_points = _collect_polygon(
        image,
        "entry_trigger",
        (0, 255, 0),
        image_width=args.image_width,
        image_height=args.image_height,
    )
    print("Select greeting_area perimeter. Do not include counters or shelves.")
    greeting_points = _collect_polygon(
        image,
        "greeting_area",
        (255, 140, 0),
        image_width=args.image_width,
        image_height=args.image_height,
    )
    payload = save_area_config(
        output=args.output,
        image_path=args.image,
        image_width=args.image_width,
        image_height=args.image_height,
        entry_points=entry_points,
        greeting_points=greeting_points,
    )
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
