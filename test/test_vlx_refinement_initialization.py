"""Exercise delayed fixed-instance initialization without loading real models."""
from contextlib import nullcontext
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from scripts.vlx_mask_refinement import track


@pytest.mark.parametrize('size',[0,1,True,2.5])
def test_invalid_chunk_size_rejected_before_model_loading(tmp_path,size):
    with pytest.raises(ValueError,match='chunk_size'):
        track(tmp_path,tmp_path/'unused.pt',tmp_path,'stuffed toy',[0,1,2],size)


def run_tracking(tmp_path, monkeypatch, seed_times, *, chunk_size=150):
    monkeypatch.setattr(sys, "path", sys.path.copy())
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    boxes = {
        "orange_cartoon": [.1, .1, .29, .29],
        "white_egg": [.4, .1, .59, .29],
        "pink_plush": [.7, .1, .89, .29],
    }
    masks = np.zeros((3, 100, 100), dtype=bool)
    for index, (x1, y1, x2, y2) in enumerate(boxes.values()):
        masks[index, int(y1*100):int(y2*100)+1, int(x1*100):int(x2*100)+1] = True
    frames = tmp_path / "frames"
    frames.mkdir()
    rows = []
    for index, (time, complete) in enumerate(seed_times):
        seeds = dict(boxes)
        if not complete:
            seeds.pop("white_egg")
        rows.append({"source_frame": index+1, "timestamp_seconds": time, "vlx_seeds": seeds})
        (frames / f"{index:06d}.jpg").write_bytes(b"fake model does not decode frames")
    (tmp_path / "input.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    model = SimpleNamespace(tracker=SimpleNamespace(model=SimpleNamespace()))
    state = {"cached_frame_outputs": {}}
    current_length = 0
    outputs = {"out_obj_ids": np.arange(3), "out_binary_masks": masks}

    def request(value):
        nonlocal current_length
        if value["type"] == "start_session":
            current_length = len(list(Path(value["resource_path"]).glob("*.jpg")))
            return {"session_id": "fixture"}
        if value["type"] == "add_prompt":
            return {"outputs": outputs}
        return {}

    def propagate(value):
        for index in range(value["start_frame_index"], current_length):
            yield {"frame_index": index, "outputs": outputs}

    predictor = SimpleNamespace(
        model=model, handle_request=request, handle_stream_request=propagate,
        _get_session=lambda sid: {"state": state},
    )
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        inference_mode=nullcontext, autocast=lambda **kw: nullcontext(), bfloat16="fake"))
    monkeypatch.setitem(sys.modules, "sam3.model_builder", SimpleNamespace(
        build_sam3_predictor=lambda **kw: predictor))
    track(tmp_path, tmp_path/"unused.pt", tmp_path, "stuffed toy", [0, 1, 2], chunk_size)
    return json.loads((tmp_path/"masks_0_1_2/geometry.json").read_text(encoding="utf-8"))


def test_first_frame_miss_initializes_at_later_observed_frame_without_backfill(tmp_path, monkeypatch):
    result = run_tracking(tmp_path, monkeypatch, [(0, False), (1.04, True), (2.08, True)])
    assert result["rows"][0] == {}
    assert set(result["rows"][1]) == {"orange_cartoon", "white_egg", "pink_plush"}
    initial = json.loads((tmp_path/"masks_0_1_2/initialization_000000.json").read_text(encoding="utf-8"))
    assert initial["source_frame"] == 2


def test_targets_first_seen_after_startup_bound_do_not_initialize(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="no unambiguous"):
        run_tracking(tmp_path, monkeypatch, [(0, False), (2.2, True)])


def test_initial_frame_remains_preferred_when_complete(tmp_path, monkeypatch):
    result = run_tracking(tmp_path, monkeypatch, [(0, True), (1.04, True)])
    assert set(result["rows"][0]) == {"orange_cartoon", "white_egg", "pink_plush"}


def test_following_window_still_requires_overlap_evidence(tmp_path, monkeypatch):
    # Window 0 covers indices 0..2; window 1 must initialize from index 2.
    # Empty propagated evidence models complete occlusion at that seam.
    from scripts import vlx_mask_refinement
    original = vlx_mask_refinement.mask_geometry
    calls = 0

    def missing_boundary(mask):
        nonlocal calls
        calls += 1
        # Three initialization masks + three masks per propagated frame.
        return None if 10 <= calls <= 12 else original(mask)

    monkeypatch.setattr(vlx_mask_refinement, "mask_geometry", missing_boundary)
    with pytest.raises(RuntimeError, match="window 2"):
        run_tracking(tmp_path, monkeypatch,
                     [(0, True), (.2, True), (.4, False), (.6, True), (.8, True)], chunk_size=3)
