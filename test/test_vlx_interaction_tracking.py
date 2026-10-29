import cv2
import numpy as np
import pytest
from scripts.probe_vlx_seek import iou
from scripts.vlx_interaction_tracking import LocalFlowTrack
from scripts.vlx_interaction_tracking import InteractionTracks


def test_local_source_pixels_follow_motion_rotation_and_reject_disappearance():
    rng = np.random.default_rng(13)
    initial = np.full((720, 1280), 40, np.uint8)
    initial[300:380, 600:700] = rng.integers(40, 240, (80, 100), dtype=np.uint8)
    track = LocalFlowTrack({"class_id": 0, "bbox_xyxy": [600, 300, 700, 380]}, initial, 0)
    previous = initial
    for i in range(1, 16):
        matrix = cv2.getRotationMatrix2D((650, 340), i*2, 1)
        matrix[:, 2] += (i*5, -i*3)
        frame = cv2.warpAffine(initial, matrix, (1280, 720), borderValue=40)
        expected = cv2.transform(np.float32([[[600, 300], [700, 300], [700, 380], [600, 380]]]), matrix)[0]
        track.update(previous, frame)
        assert track.alive
        assert iou(track.output()["bbox_xyxy"], [*expected.min(axis=0), *expected.max(axis=0)]) > .9
        previous = frame
    track.update(previous, np.full_like(initial, 40))
    assert not track.alive


@pytest.mark.skipif(not hasattr(cv2, "TrackerCSRT_create"), reason="isolated contrib runtime required")
def test_csrt_recovery_cannot_keep_a_disappeared_object_or_renew_confirmation():
    initial = np.full((300, 500, 3), 35, np.uint8)
    cv2.circle(initial, (230, 140), 35, (0, 210, 230), -1)
    cv2.rectangle(initial, (200, 135), (260, 145), (15, 15, 15), -1)
    tracks = InteractionTracks(ttl_seconds=1.6, use_csrt=True)
    tracks.advance(1, 0, cv2.cvtColor(initial, cv2.COLOR_BGR2GRAY), initial)
    tracks.apply((1, 0, "", initial), [{"class_id": 5, "bbox_xyxy": [195, 105, 265, 175]}])
    for index in range(1, 9):
        matrix = cv2.getRotationMatrix2D((230, 140), index*20, 1)
        frame = cv2.warpAffine(initial, matrix, (500, 300), borderValue=(35, 35, 35))
        tracks.advance(index+1, index*.04, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), frame)
        if index == 8:
            tracks.apply((index+1, index*.04, "", frame), [])
        assert tracks.outputs()
        assert tracks.outputs()[0]["confirmed_at"] == 0
    assert tracks.outputs()[0]["position_source"] == "csrt"
    missing = np.full_like(initial, 35)
    tracks.advance(10, .36, cv2.cvtColor(missing, cv2.COLOR_BGR2GRAY), missing)
    assert tracks.outputs() == []
