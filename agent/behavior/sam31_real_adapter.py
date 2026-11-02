"""Verified SAM3.1 video-predictor adapter for DPI-01A/01C.

The installed multiplex tracking implementation has two prompt paths: SAM3
box prompts and SAM2 point refinement.  It rejects a request containing both,
so this adapter performs the documented two-call sequence in one session and
on one frame.  It never sends a text prompt.
"""

from __future__ import annotations

import gc
import hashlib
import importlib
import inspect
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional, Sequence

import cv2
import numpy as np

from agent.behavior.product_first_frame_initialization import (
    BBoxPixels,
    DenseProductInitializationError,
    InstancePromptResult,
    InitializationFailureCode,
    PointPixels,
)


class Sam31InstancePromptAdapter:
    """Adapt an already-created real predictor/session to the frozen Protocol."""

    def __init__(
        self,
        *,
        predictor: object,
        session_id: str,
        frame_size: tuple[int, int] = (1280, 720),
        output_prob_thresh: float = 0.5,
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        if frame_size != (1280, 720):
            raise ValueError("SAM3.1 Stage7C adapter requires 1280x720")
        if not hasattr(predictor, "handle_request"):
            raise TypeError("predictor must expose handle_request")
        self.predictor = predictor
        self.session_id = session_id
        self.frame_size = frame_size
        self.output_prob_thresh = float(output_prob_thresh)
        self.calls: list[dict[str, object]] = []
        self.closed = False
        self._used_obj_ids: set[int] = set()
        self._restore_specs: list[dict[str, object]] = []

    @property
    def session_reference(self) -> str:
        digest = hashlib.sha256(self.session_id.encode("utf-8")).hexdigest()[:16]
        return f"sam-session:{digest}"

    def add_instance_prompt(
        self,
        *,
        frame: np.ndarray,
        frame_index: int,
        bbox: BBoxPixels,
        points: tuple[PointPixels, ...],
        point_labels: tuple[int, ...],
        obj_id: int,
    ) -> InstancePromptResult:
        if self.closed:
            raise RuntimeError("SAM3.1 adapter session is closed")
        width, height = self.frame_size
        if frame.shape[:2] != (height, width):
            return self._failed_result(obj_id, "frame_shape", tuple(frame.shape))
        if frame_index != 0:
            return self._failed_result(obj_id, "frame_index", frame_index)
        if obj_id in self._used_obj_ids:
            return self._failed_result(obj_id, "duplicate_requested_obj_id", obj_id)
        if len(points) != len(point_labels) or not points:
            return self._failed_result(obj_id, "point_label_length", len(points))
        if any(label not in (0, 1) for label in point_labels):
            return self._failed_result(obj_id, "invalid_point_label", point_labels)
        x1, y1, x2, y2 = (int(value) for value in bbox)
        if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
            return self._failed_result(obj_id, "bbox_out_of_bounds", bbox)
        if any(not (0 <= x < width and 0 <= y < height) for x, y in points):
            return self._failed_result(obj_id, "point_out_of_bounds", points)

        self._used_obj_ids.add(obj_id)
        bbox_request = {
            "type": "add_prompt",
            "session_id": self.session_id,
            "frame_index": int(frame_index),
            "bounding_boxes": [[
                x1 / width,
                y1 / height,
                (x2 - x1) / width,
                (y2 - y1) / height,
            ]],
            "bounding_box_labels": [1],
            "output_prob_thresh": self.output_prob_thresh,
        }
        self.calls.append({
            "stage": "bbox",
            "frame_index": frame_index,
            "requested_obj_id": obj_id,
            "bbox": list(bbox),
            "points_in_request": False,
        })
        try:
            bbox_response = self.predictor.handle_request(bbox_request)
            bbox_outputs = (bbox_response or {}).get("outputs", {})
            bbox_ids = _array_1d(bbox_outputs.get("out_obj_ids", []))
            if len(bbox_ids) != 1:
                return self._failed_result(
                    obj_id,
                    "bbox_candidate_count",
                    {"raw_output_shape": _output_shapes(bbox_outputs), "candidate_count": len(bbox_ids)},
                    code=InitializationFailureCode.CANDIDATE_AMBIGUOUS,
                )
            resolved_obj_id = int(bbox_ids[0])
            # The SAM3.1 multiplex bbox path currently emits backend object 0
            # for every new box.  Keep the backend id for the first object, but
            # use the caller's unique id for point refinement after a collision;
            # the point response must explicitly contain that id or we fail
            # closed.  Backends that already return unique bbox ids retain the
            # historical behavior.
            backend_obj_id_reused = (
                resolved_obj_id in self._used_obj_ids and resolved_obj_id != obj_id
            )
            refinement_obj_id = obj_id if backend_obj_id_reused else resolved_obj_id
            if refinement_obj_id in self._used_obj_ids and refinement_obj_id != obj_id:
                return self._failed_result(
                    resolved_obj_id,
                    "resolved_obj_id_reused",
                    {"resolved_obj_id": resolved_obj_id, "requested_obj_id": obj_id},
                )
            self._used_obj_ids.add(refinement_obj_id)

            points_request = {
                "type": "add_prompt",
                "session_id": self.session_id,
                "frame_index": int(frame_index),
                "points": [[x / width, y / height] for x, y in points],
                "point_labels": list(point_labels),
                "obj_id": refinement_obj_id,
                "clear_old_points": True,
                "rel_coordinates": True,
                "output_prob_thresh": self.output_prob_thresh,
            }
            self.calls.append({
                "stage": "points",
                "frame_index": frame_index,
                "requested_obj_id": obj_id,
                "resolved_obj_id": resolved_obj_id,
                "refinement_obj_id": refinement_obj_id,
                "backend_obj_id_reused": backend_obj_id_reused,
                "points": [list(point) for point in points],
                "point_labels": list(point_labels),
                "points_in_request": True,
            })
            point_response = self.predictor.handle_request(points_request)
            point_outputs = (point_response or {}).get("outputs", {})
            ids = _array_1d(point_outputs.get("out_obj_ids", []))
            masks = _array_masks(point_outputs.get("out_binary_masks"))
            matched = [index for index, value in enumerate(ids) if int(value) == refinement_obj_id]
            if len(matched) != 1 or len(masks) <= matched[0]:
                return self._failed_result(
                    refinement_obj_id,
                    "point_candidate_mapping",
                    {"raw_output_shape": _output_shapes(point_outputs), "candidate_count": len(ids), "matched_count": len(matched)},
                    code=InitializationFailureCode.CANDIDATE_AMBIGUOUS,
                )
            mask = masks[matched[0]].astype(bool)
            if mask.ndim != 2 or mask.shape != (height, width) or not np.any(mask):
                return self._failed_result(
                    resolved_obj_id,
                    "mask_shape_or_empty",
                    {"raw_output_shape": _output_shapes(point_outputs), "mask_shape": list(mask.shape), "mask_area": int(np.count_nonzero(mask))},
                    code=InitializationFailureCode.MASK_EMPTY,
                )
            confidence = _confidence_for_id(point_outputs.get("out_probs"), ids, matched[0])
            # The multiplex bbox path resets the SAM3 state before every box
            # prompt.  Restore all previously accepted point objects in this
            # same Session before the caller starts propagation.  This keeps
            # the real backend's object table complete without changing the
            # frozen bbox -> point prompt contract.
            try:
                self._restore_previous_prompts()
            except Exception as error:
                return self._failed_result(
                    refinement_obj_id,
                    "session_object_restore_failed",
                    {"error_type": type(error).__name__, "error": str(error)},
                    code=InitializationFailureCode.CANDIDATE_AMBIGUOUS,
                )
            self._restore_specs.append({
                "obj_id": refinement_obj_id,
                "points_request": dict(points_request),
            })
            return InstancePromptResult(
                candidate_id=refinement_obj_id,
                masks=(mask,),
                session_id=self.session_reference,
                confidence=confidence,
                diagnostics={
                    "adapter": "Sam31InstancePromptAdapter",
                    "sam_version": _sam_version(),
                    "session_id_ref": self.session_reference,
                    "requested_obj_id": obj_id,
                    "resolved_obj_id": resolved_obj_id,
                    "refinement_obj_id": refinement_obj_id,
                    "backend_obj_id_reused": backend_obj_id_reused,
                    "raw_output_shape": _output_shapes(point_outputs),
                    "candidate_count": len(ids),
                    "mask_shape": list(mask.shape),
                    "mask_area": int(np.count_nonzero(mask)),
                    "call_sequence": ["bbox", "points"],
                },
            )
        except Exception as error:
            return self._failed_result(
                obj_id,
                "predictor_exception",
                {"error_type": type(error).__name__, "error": str(error)},
            )

    def _restore_previous_prompts(self) -> None:
        """Re-add prior point objects after a backend bbox reset."""

        for spec in self._restore_specs:
            request = dict(spec["points_request"])
            expected_obj_id = int(spec["obj_id"])
            response = self.predictor.handle_request(request)
            outputs = (response or {}).get("outputs", {})
            ids = _array_1d(outputs.get("out_obj_ids", []))
            masks = _array_masks(outputs.get("out_binary_masks"))
            matches = [index for index, value in enumerate(ids) if int(value) == expected_obj_id]
            self.calls.append({
                "stage": "restore_points",
                "frame_index": int(request["frame_index"]),
                "requested_obj_id": expected_obj_id,
                "obj_id": expected_obj_id,
                "points": request.get("points", []),
                "point_labels": request.get("point_labels", []),
                "points_in_request": True,
                "restored_candidate_count": len(ids),
                "restored_match_count": len(matches),
            })
            if len(matches) != 1 or len(masks) <= matches[0]:
                raise RuntimeError(
                    f"restored point response did not contain unique obj_id={expected_obj_id}"
                )

    def _failed_result(
        self,
        candidate_id: int,
        reason: str,
        detail: object,
        *,
        code: str = InitializationFailureCode.CANDIDATE_AMBIGUOUS,
    ) -> InstancePromptResult:
        return InstancePromptResult(
            candidate_id=int(candidate_id),
            masks=(),
            session_id=self.session_reference,
            diagnostics={
                "adapter": "Sam31InstancePromptAdapter",
                "sam_version": _sam_version(),
                "session_id_ref": self.session_reference,
                "adapter_status": "REJECTED",
                "failure_code": code,
                "reason": reason,
                "detail": detail,
            },
        )

    def close(self) -> None:
        if self.closed:
            return
        self.predictor.handle_request({
            "type": "close_session",
            "session_id": self.session_id,
        })
        self.closed = True


class ExternalSam31InstancePromptAdapter(Sam31InstancePromptAdapter):
    """Own a real external predictor and one temporary one-frame session."""

    def __init__(self, *, predictor: object, session_id: str, temp_dir: tempfile.TemporaryDirectory[str], **kwargs: object) -> None:
        super().__init__(predictor=predictor, session_id=session_id, **kwargs)
        self._temp_dir = temp_dir

    def close(self) -> None:
        if self.closed:
            return
        try:
            super().close()
        finally:
            self._temp_dir.cleanup()
            del self.predictor
            gc.collect()


def create_external_sam31_adapter(
    *,
    frame: np.ndarray,
    runtime_root: str | Path = r"D:\GJJ\work\sam3",
    checkpoint_path: Optional[str | Path] = None,
    output_prob_thresh: float = 0.5,
) -> ExternalSam31InstancePromptAdapter:
    """Build the repository's real SAM3.1 predictor and start one-frame session."""

    root = Path(runtime_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    base = importlib.import_module("sam31_stage5_1_1_demo_latency_finalization")
    base.configure_torch()
    predictor = _build_external_predictor(base, checkpoint_path)
    base.tune_runtime(predictor)
    temp_dir = tempfile.TemporaryDirectory(prefix="dpi01c-sam3-")
    frame_path = Path(temp_dir.name) / "00000.jpg"
    if not cv2.imwrite(str(frame_path), frame):
        temp_dir.cleanup()
        raise RuntimeError("failed to materialize one-frame SAM3 session")
    response = predictor.handle_request({
        "type": "start_session",
        "resource_path": temp_dir.name,
        "offload_video_to_cpu": True,
    })
    session_id = (response or {}).get("session_id")
    if not session_id:
        temp_dir.cleanup()
        del predictor
        gc.collect()
        raise RuntimeError(f"SAM3 start_session returned no session_id: {response!r}")
    return ExternalSam31InstancePromptAdapter(
        predictor=predictor,
        session_id=str(session_id),
        temp_dir=temp_dir,
        output_prob_thresh=output_prob_thresh,
    )


def create_external_sam31_video_session(
    *,
    frame_dir: str | Path,
    runtime_root: str | Path = r"D:\GJJ\work\sam3",
    checkpoint_path: Optional[str | Path] = None,
) -> tuple[object, str]:
    """Build the real predictor and start a session over a frame directory.

    The caller owns ``frame_dir`` and must close the returned session.  This is
    intentionally separate from the one-frame initialization adapter so a
    propagation run cannot accidentally use its temporary single-frame source.
    """

    root = Path(runtime_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    base = importlib.import_module("sam31_stage5_1_1_demo_latency_finalization")
    base.configure_torch()
    predictor = _build_external_predictor(base, checkpoint_path)
    base.tune_runtime(predictor)
    response = predictor.handle_request({
        "type": "start_session",
        "resource_path": str(Path(frame_dir).resolve()),
        "offload_video_to_cpu": True,
    })
    session_id = (response or {}).get("session_id")
    if not session_id:
        del predictor
        gc.collect()
        raise RuntimeError(f"SAM3 start_session returned no session_id: {response!r}")
    return predictor, str(session_id)


def _build_external_predictor(base: object, checkpoint_path: Optional[str | Path]) -> object:
    """Build the frozen Stage5 predictor while honoring an explicit checkpoint.

    The checked-in Stage5 wrapper currently hard-codes its model-builder
    arguments and exposes ``build_predictor()`` without a checkpoint keyword.
    Use that wrapper when no override is requested; with an explicit local
    weight, call the same underlying SAM3.1 builder directly so the selected
    checkpoint is actually loaded.
    """

    build_predictor = getattr(base, "build_predictor")
    if checkpoint_path is None:
        return build_predictor()

    parameters = inspect.signature(build_predictor).parameters
    if "checkpoint_path" in parameters:
        return build_predictor(checkpoint_path=str(checkpoint_path))

    builder_module = importlib.import_module("sam3.model_builder")
    builder = getattr(builder_module, "build_sam3_predictor")
    return builder(
        checkpoint_path=str(checkpoint_path),
        version="sam3.1",
        max_num_objects=int(getattr(base, "MAX_NUM_OBJECTS", 16)),
        multiplex_count=int(getattr(base, "MULTIPLEX_COUNT", 16)),
        use_fa3=bool(getattr(base, "USE_FA3", False)),
        use_rope_real=bool(getattr(base, "USE_ROPE_REAL", False)),
        compile=bool(getattr(base, "ENABLE_COMPILE", False)),
        warm_up=False,
        async_loading_frames=False,
    )


def _array_1d(value: object) -> np.ndarray:
    if value is None:
        return np.asarray([], dtype=np.int64)
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    return np.asarray(value).reshape(-1)


def _array_masks(value: object) -> np.ndarray:
    if value is None:
        return np.asarray([])
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    array = np.asarray(value)
    while array.ndim > 3:
        array = array[:, 0]
    return array


def _confidence_for_id(value: object, ids: np.ndarray, index: int) -> float:
    if value is None:
        return 1.0
    if hasattr(value, "detach"):
        value = value.detach().cpu().numpy()
    values = np.asarray(value).reshape(-1)
    if index >= len(values):
        return 1.0
    try:
        return max(0.0, min(1.0, float(values[index])))
    except (TypeError, ValueError):
        return 1.0


def _output_shapes(outputs: object) -> dict[str, object]:
    if not isinstance(outputs, dict):
        return {"outputs_type": type(outputs).__name__}
    return {
        str(key): list(np.asarray(value.detach().cpu().numpy() if hasattr(value, "detach") else value).shape)
        for key, value in outputs.items()
        if value is not None
    }


def _sam_version() -> str:
    try:
        return str(importlib.import_module("sam3").__version__)
    except Exception:
        return "unknown"
