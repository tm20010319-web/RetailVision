"""Restricted, read-only Event 804 spatial identity diagnostic."""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.camera_track_identity import SameCameraRecognitionBinder, TrackSnapshot
from agent.face_spatial_evidence import FaceSpatialEvidence
from agent.offline_identity_association import (
    OfflineSpatialGeometry,
    OfflineSpatialGeometryValidator,
    map_face_bbox_to_recording_frame,
)


ROOT = Path("reports/p6s_trusted_publication_20260820/event_804")
VIDEO = ROOT / "AI_20260820122257-20260820122501.mp4"
PROBE = ROOT / "behavior_probe_flush.json"
EVENT_TIME = datetime.fromisoformat("2026-08-20T04:23:19")
RUN_ID = "b28-real-804-flush-run"


def _frames(path: Path, *, time_base_num: int, time_base_den: int) -> list[tuple[float, np.ndarray]]:
    if time_base_num <= 0 or time_base_den <= 0:
        raise ValueError("invalid_recording_time_base")
    result: list[tuple[float, np.ndarray]] = []
    capture = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not capture.isOpened():
        raise RuntimeError("recording_open_failed")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            raw_pts = float(capture.get(cv2.CAP_PROP_PTS))
            if np.isfinite(raw_pts):
                # OpenCV exposes the stream PTS in time-base ticks here, not seconds.
                pts_seconds = raw_pts * time_base_num / time_base_den
                result.append((pts_seconds, frame))
    finally:
        capture.release()
    return result


def _track_rows(data: dict) -> list[TrackSnapshot]:
    rows = []
    for value in data["person_tracks"]:
        rows.append(TrackSnapshot(
            store_id="store-1",
            camera_id="cam02",
            runtime_id=RUN_ID,
            track_id=int(value["track_id"]),
            track_token=value["track_token"],
            observed_at=datetime.fromisoformat(value["observed_at"]),
            bbox=tuple(value["bbox"]),
            confidence=float(value["confidence"]),
            role=value["role"],
            stable_frames=3,
            is_frame_clipped=bool(value["bbox"][0] <= 0 or value["bbox"][1] <= 0),
            coordinate_space="normalized_full_frame",
            frame_size=(800, 448),
        ))
    return rows


def _rect(image: np.ndarray, bbox: tuple[float, float, float, float], color, label: str) -> None:
    h, w = image.shape[:2]
    x1, y1, x2, y2 = (int(round(value)) for value in (bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h))
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.putText(image, label, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)


def main() -> int:
    data = json.loads(PROBE.read_text(encoding="utf-8"))
    tracks = _track_rows(data)
    face = FaceSpatialEvidence.from_safe_dict({
        "coordinate_space": "normalized_full_frame",
        "normalized_bbox": [0.38125, 0.34558823529411764, 0.48125, 0.5220588235294118],
        "raw_bbox": [732.0, 376.0, 924.0, 568.0],
        "source_frame_size": [1920, 1088],
        "quality_status": "parsed",
        "event_time": EVENT_TIME.isoformat(),
    })
    assert face is not None
    geometry = OfflineSpatialGeometry(
        decoded_source_frame_size=(1920, 1088),
        analysis_frame_size=(800, 448),
        full_frame_resize_verified=True,
        cropped=False,
        mirrored=False,
        rotated=False,
        padded=False,
        transform_type="full_frame_resize",
        scale_x=800 / 1920,
        scale_y=448 / 1088,
        policy_id="p6s_face_recording_geometry",
        policy_version="p6s_face_recording_geometry_v1",
        validation_report_ref="P6S_FACE_RECORDING_SPATIAL_GEOMETRY_VALIDATION.md",
        device_id="cam02",
    )
    compatibility = OfflineSpatialGeometryValidator().validate(face=face, geometry=geometry)
    mapped = map_face_bbox_to_recording_frame(face, geometry)
    assert mapped is not None
    candidates = [item for item in tracks if abs((item.observed_at - EVENT_TIME).total_seconds()) <= 0.5]
    primary = min(candidates, key=lambda item: (abs((item.observed_at - EVENT_TIME).total_seconds()), -item.stable_frames, -item.confidence, item.observed_at))
    binder = SameCameraRecognitionBinder()
    decision = binder.evaluate(
        store_id="store-1", camera_id="cam02", runtime_id=RUN_ID,
        effective_occurred_at=EVENT_TIME, reference_time=EVENT_TIME,
        tracks=tuple(candidates), face=mapped,
    )
    diagnostic_by_token = {item["track_token"]: item for item in decision.spatial_diagnostics}
    time_base_num = int(data["behavior_summary"]["time_base_num"])
    time_base_den = int(data["behavior_summary"]["time_base_den"])
    frames = _frames(VIDEO, time_base_num=time_base_num, time_base_den=time_base_den)
    primary_pts = (primary.observed_at - datetime.fromisoformat("2026-08-20T04:22:57")).total_seconds()
    primary_frame_pts, primary_frame = min(frames, key=lambda item: abs(item[0] - primary_pts))
    debug = primary_frame.copy()
    _rect(debug, mapped.bbox, (0, 0, 255), "FACE mapped")
    _rect(debug, primary.bbox, (255, 0, 0), f"PRIMARY {primary.track_token}")
    diag = diagnostic_by_token[primary.track_token]
    lines = [
        "Event 804 | RESTRICTED DIAGNOSTIC | IDENTITY UNMATCHED",
        f"Recognition PTS: {EVENT_TIME.isoformat()} | Primary PTS: {primary_pts:.6f} | delta: {abs((primary.observed_at - EVENT_TIME).total_seconds()) * 1000:.3f} ms",
        "Face source: 1920x1088 | Recording: 800x448 | AI: 1280x720 (normalized bbox retained)",
        f"Face bbox: {list(mapped.bbox)} | Person: {primary.track_token}",
        f"Containment: {diag['intersection_over_face_area']:.4f} | Required: {diag['containment_threshold']:.4f} | IoU: {diag['intersection_over_face_area']:.4f}",
    ]
    for index, line in enumerate(lines):
        cv2.putText(debug, line, (8, 18 + index * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(ROOT / "event_804_primary_geometry_debug.jpg"), debug)

    support = sorted(candidates, key=lambda item: item.observed_at)
    center = min(range(len(support)), key=lambda index: abs((support[index].observed_at - primary.observed_at).total_seconds()))
    selected = support[max(0, center - 2):center + 3]
    panels = []
    for item in selected:
        pts = (item.observed_at - datetime.fromisoformat("2026-08-20T04:22:57")).total_seconds()
        _, image = min(frames, key=lambda value: abs(value[0] - pts))
        panel = image.copy()
        _rect(panel, mapped.bbox, (0, 0, 255), "FACE")
        _rect(panel, item.bbox, (255, 0, 0), "PERSON")
        item_diag = diagnostic_by_token.get(item.track_token, {})
        cv2.putText(panel, f"PTS {pts:.3f}s delta {abs((item.observed_at - EVENT_TIME).total_seconds())*1000:.1f}ms", (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(panel, f"containment {item_diag.get('intersection_over_face_area', 0):.4f} track {item.track_token.rsplit(':', 1)[-1]}", (8, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        panels.append(panel)
    if panels:
        cv2.imwrite(str(ROOT / "event_804_temporal_geometry_support.jpg"), np.hstack(panels))

    behavior_tracks = sorted({item["person_track_token"] for item in data["raw_stage9_events"]})
    report = {
        "event": "804",
        "source_event_id": "804:2026-08-20T04:23:19",
        "canonical_event_id": "ae87a0d9d50eaa308ae79f244c41d7973e3cd4306a143b62034884f0949ab1ef",
        "recognition_occurred_at": EVENT_TIME.isoformat(),
        "recording_started_at_utc": "2026-08-20T04:22:57",
        "recording_ended_at_utc": "2026-08-20T04:25:01",
        "expected_source_pts": 22.0,
        "recording_time_base": {"num": time_base_num, "den": time_base_den},
        "primary": {
            "track_token": primary.track_token,
            "source_pts": primary_pts,
            "observed_at": primary.observed_at.isoformat(),
            "absolute_time_delta_ms": abs((primary.observed_at - EVENT_TIME).total_seconds()) * 1000,
            "person_bbox_normalized": list(primary.bbox),
            "person_bbox_pixel_800x448": [primary.bbox[0]*800, primary.bbox[1]*448, primary.bbox[2]*800, primary.bbox[3]*448],
            "primary_selection": "minimum_absolute_temporal_delta",
        },
        "primary_candidates": [
            {"track_token": item.track_token, "source_pts": (item.observed_at - datetime.fromisoformat("2026-08-20T04:22:57")).total_seconds(), "delta_ms": abs((item.observed_at - EVENT_TIME).total_seconds())*1000, "bbox": list(item.bbox), "confidence": item.confidence, "role": item.role}
            for item in selected
        ],
        "face": {"source_frame_size": [1920, 1088], "raw_bbox_pixel": [732, 376, 924, 568], "normalized_bbox": list(face.bbox), "geometry_policy_id": geometry.policy_id, "geometry_policy_version": geometry.policy_version},
        "coordinate_spaces": {"recording_decoded": [800, 448], "face_background": [1920, 1088], "ai_inference": [1280, 720], "person_bbox": "normalized_full_frame derived from 1280x720 AI frame", "binder_comparison": "normalized_full_frame after verified Face mapping"},
        "geometry": {"compatibility": compatibility.status, "transform_type": geometry.transform_type, "scale_x": geometry.scale_x, "scale_y": geometry.scale_y, "offset_x": geometry.offset_x, "offset_y": geometry.offset_y, "crop": "none", "padding": "none", "mirror": False, "rotation": 0, "mapped_face_bbox_normalized": list(mapped.bbox), "mapped_face_bbox_pixel_800x448": [mapped.bbox[0]*800, mapped.bbox[1]*448, mapped.bbox[2]*800, mapped.bbox[3]*448]},
        "primary_geometry": diag,
        "behavior_track_tokens": behavior_tracks,
        "same_track": behavior_tracks == [primary.track_token],
        "diagnosis": "valid_geometry_non_match",
        "identity_result": "unmatched",
        "thresholds_unchanged": True,
        "debug_images": ["event_804_primary_geometry_debug.jpg", "event_804_temporal_geometry_support.jpg"],
    }
    (ROOT / "event_804_spatial_identity_diagnostic.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"primary": primary.track_token, "primary_pts": primary_pts, "delta_ms": report["primary"]["absolute_time_delta_ms"], "containment": diag["intersection_over_face_area"], "threshold": diag["containment_threshold"], "diagnosis": report["diagnosis"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
