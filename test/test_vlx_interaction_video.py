"""Behavioral regression and file-pipeline integration (no GPU required)."""
import json
from pathlib import Path
import shutil

import cv2
import numpy as np
import pytest

from agent.vlx_interaction import (
    InteractionAnalyzer, InteractionConfig, ObjectEvidence, PersonEvidence, TARGETS,
)
from agent.vlx_person_tracking import AnonymousUsers, PersonDetection
from scripts.run_vlx_interaction_video import load_config, parse_timeline, run_analysis
from scripts.vlx_interaction_vision import parse_objects

KEY = TARGETS[0].key


def obj(x=0.40, key=KEY, continuous=True):
    return ObjectEvidence(key, (x, 0.4, x+0.08, 0.48), continuous)


def person(uid=1, x=None, wrists_visible=True):
    wrists = ((0.1, 0.2), (0.2, 0.2)) if x is None else ((x+0.04, 0.44), (0.1, 0.2))
    return PersonEvidence(uid, (0.05, 0.1, 0.85, 0.95), wrists if wrists_visible else (None, None))


def baseline(a, offset=0, key=KEY, x=0.4):
    for i in range(5):
        a.process(offset+i*0.2, [obj(x, key)], [person()])


def pickup(a, offset=0, uid=1, key=KEY, start=0.4):
    a.process(offset+1.0, [obj(start, key)], [person(uid, start)])
    for i, x in enumerate((start+0.025, start+0.05, start+0.075, start+0.1)):
        a.process(offset+1.2+i*0.2, [obj(x, key)], [person(uid, x)])


def release(a, offset=0, key=KEY, x=0.5, uid=1):
    for i in range(6):
        a.process(offset+2.0+i*0.2, [obj(x, key)], [person(uid)])


def test_complete_interaction_uses_media_time_and_confirmation_is_separate():
    a = InteractionAnalyzer()
    baseline(a); pickup(a); release(a)
    r = a.finish()
    assert r["total"]["pickup_count"] == r["total"]["putdown_count"] == 1
    session = r["sessions"][0]
    assert session["pickup_seconds"] == pytest.approx(1.2)
    assert session["pickup_confirmed_seconds"] == pytest.approx(1.6)
    assert session["putdown_seconds"] == pytest.approx(2.0)
    assert session["putdown_confirmed_seconds"] == pytest.approx(2.8)
    assert session["holding_seconds"] == pytest.approx(0.8)
    assert [e["event_type"] for e in r["events"]] == ["PICK_UP", "HOLDING", "PUT_DOWN"]
    assert r["total"]["total_holding_seconds"] == pytest.approx(0.8)
    assert r["by_user_object"][0]["user_label"] == "用户1"


def test_repeat_pickups_same_object_and_different_users_are_not_deduplicated():
    a = InteractionAnalyzer()
    baseline(a); pickup(a); release(a)
    # The new resting location is valid, without a configured table/ROI.
    for t in (3.2, 3.4, 3.6, 3.8):
        a.process(t, [obj(0.5)], [person(2)])
    pickup(a, offset=3.0, uid=2, start=0.5)
    release(a, offset=3.0, x=0.6, uid=2)
    r = a.finish()
    assert r["total"]["completed_count"] == 2
    assert len({s["session_id"] for s in r["sessions"]}) == 2
    assert [s["user_id"] for s in r["sessions"]] == [1, 2]


def test_held_stationary_is_not_putdown_and_eof_is_incomplete():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for i in range(20):
        a.process(2+i*0.2, [obj(0.5)], [person(1, 0.5)])
    r = a.finish()
    assert r["total"]["pickup_count"] == 1
    assert r["total"]["putdown_count"] == 0
    assert r["total"]["total_holding_seconds"] == 0
    assert r["sessions"][0]["end_reason"] == "end_of_video"
    assert r["sessions"][0]["holding_seconds"] is None
    assert a.finish() == r


def test_toss_catch_is_one_session_then_rest_with_hand_nearby_rearms():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    # In flight, briefly absent, then caught: no supported resting pose.
    for t, x, touching in ((2, .60, False), (2.2, .65, False),
                           (2.4, None, False), (2.6, .55, True),
                           (2.8, .5, True), (3, .5, True)):
        a.process(t, [] if x is None else [obj(x)],
                  [person(1, x if touching else None)])
    assert a.report()["total"]["pickup_count"] == 1
    assert a.report()["total"]["putdown_count"] == 0
    # The entire object returns to a previously observed resting pose;
    # leaving a hand against it must not merge the next lift into this one.
    for i in range(6):
        a.process(3.2+i*.2, [obj(.4)], [person(1, .4)])
    assert a.report()["total"]["putdown_count"] == 1
    pickup(a, offset=3.4)
    release(a, offset=3.4)
    report = a.finish()
    assert report["total"]["pickup_count"] == report["total"]["putdown_count"] == 2
    assert [s["session_id"] for s in report["sessions"]] == [f"{KEY}:1", f"{KEY}:2"]
    assert report["sessions"][0]["putdown_seconds"] == pytest.approx(3.2)


def test_brief_return_through_rest_pose_during_toss_does_not_close():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for t, x in ((2, .4), (2.2, .4), (2.4, .55), (2.6, .6), (2.8, .5)):
        a.process(t, [obj(x)], [person(1, x)])
    assert a.finish()["total"]["putdown_count"] == 0


def test_verified_stationary_correction_requires_a_known_resting_origin():
    for known in (False, True):
        a = InteractionAnalyzer()
        if known:
            baseline(a)
        corrected = ObjectEvidence(KEY, (.4, .42, .48, .48), False, 1.0)
        a.process(1.8, [corrected], [person(1, .4)])
        assert (a.states[KEY].baseline is not None) == known
        assert a.report()["events"] == []


def test_partial_extent_cannot_rearm_at_a_different_lower_edge():
    a = InteractionAnalyzer()
    baseline(a)
    a.process(1, [], [])
    for i in range(8):
        shifted = ObjectEvidence(KEY, (.4, .35, .48, .43), i != 0, .9 if i == 0 else None)
        a.process(1.2+i*.2, [shifted], [person(1, .4)])
    assert a.finish()["total"]["pickup_count"] == 0
    assert a.states[KEY].baseline is None


def test_stationary_partial_extent_during_contact_keeps_the_supported_return_reference():
    a = InteractionAnalyzer()
    for i in range(5):
        a.process(i*.2, [ObjectEvidence(KEY, (.4, .32, .48, .48))], [person()])
    a.process(1, [ObjectEvidence(KEY, (.406, .4, .48, .48))], [person(1, .4)])
    for i, x in enumerate((.425, .45, .475, .5)):
        a.process(1.2+i*.2, [obj(x)], [person(1, x)])
    for i in range(6):
        a.process(2+i*.2, [ObjectEvidence(KEY, (.414, .4, .488, .48))], [person(1, .414)])
    assert a.finish()["total"]["completed_count"] == 1


def test_real_bee_repeat_trace_keeps_tosses_inside_two_sessions():
    fixture = json.loads((Path(__file__).parent / "fixtures/vlx_bee_repeat_toss_trace.json").read_text(encoding="utf-8"))
    a = InteractionAnalyzer()
    for row in fixture["observations"]:
        a.process(row["timestamp_seconds"], [ObjectEvidence(**o) for o in row["objects"]],
                  [PersonEvidence(**p) for p in row["people"]])
    result = a.finish()
    assert result["total"]["pickup_count"] == result["total"]["putdown_count"] == 2
    assert result["total"]["incomplete_count"] == 0
    first, second = result["sessions"]
    assert first["session_id"] != second["session_id"]
    assert first["user_id"] == second["user_id"] == 1
    assert 24 <= first["pickup_seconds"] <= 24.4
    # No fabricated return timestamp inside the missing source frames.
    assert 39.76 <= first["putdown_seconds"] <= 40
    assert 42 <= second["pickup_seconds"] <= 42.4
    assert 49 <= second["putdown_seconds"] <= 50
    assert first["observation_gaps"] > 0
    assert [e["event_type"] for e in result["events"]] == ["PICK_UP", "HOLDING", "PUT_DOWN"]*2


def test_real_repeat_recording_counts_no_extra_sessions_under_other_target_labels():
    fixture = json.loads((Path(__file__).parent / "fixtures/vlx_bee_repeat_all_targets_trace.json").read_text(encoding="utf-8"))
    a = InteractionAnalyzer()
    for row in fixture["observations"]:
        a.process(row["timestamp_seconds"], [ObjectEvidence(**o) for o in row["objects"]],
                  [PersonEvidence(**p) for p in row["people"]])
    report = a.finish()
    assert report["total"]["pickup_count"] == report["total"]["putdown_count"] == 2
    assert report["total"]["incomplete_count"] == 0
    assert {s["object_key"] for s in report["sessions"]} == {"bee_plush"}
    assert [s["user_id"] for s in report["sessions"]] == [1, 1]
    assert report["sessions"][0]["putdown_seconds"] < report["sessions"][1]["pickup_seconds"]


@pytest.mark.parametrize("same_rest,same_actor,gap_end,expected", [
    (True, True, 4.0, 1), (False, True, 4.0, 0),
    (True, False, 4.0, 0), (True, True, 5.6, 0),
])
def test_short_source_gap_can_start_a_known_return_but_cannot_fabricate_release(same_rest, same_actor, gap_end, expected):
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for t in (2, 2.2, 2.4, 2.6, 2.8, 3.0):
        a.process(t, [], [person(1, .5)])
    for i in range(6):
        x = .4 if same_rest else .65
        a.process(gap_end+i*.2, [obj(x, continuous=i != 0)], [person(1 if same_actor else 2, x)])
        if i == 0:
            assert a.report()["total"]["putdown_count"] == 0
    assert a.finish()["total"]["putdown_count"] == expected


def test_touch_without_displacement_is_not_pickup():
    a = InteractionAnalyzer()
    baseline(a)
    for i in range(20):
        a.process(1+i*0.2, [obj(0.4+(i % 2)*0.002)], [person(1, 0.4)])
    assert a.finish()["events"] == []


def test_initially_held_object_cannot_invent_a_pickup():
    a = InteractionAnalyzer()
    for i in range(10):
        state = a.process(i*0.2, [obj(0.4)], [person(1, 0.4)])
        assert next(o for o in state["objects"] if o["object_key"] == KEY)["state"] == "unknown"
    for i in range(10):
        a.process(2+i*0.2, [obj(0.45+i*0.01)], [person(1, 0.45+i*0.01)])
    assert a.finish()["total"]["pickup_count"] == 0


def test_object_motion_without_wrist_co_motion_does_not_count():
    a = InteractionAnalyzer()
    baseline(a)
    for i in range(10):
        a.process(1+i*0.2, [obj(0.42+i*0.003)], [person(1, 0.4)])
    assert a.finish()["total"]["pickup_count"] == 0


@pytest.mark.parametrize("people", [[person(1, 0.5), person(2, 0.5)], [person(None, 0.5)]])
def test_ambiguous_users_do_not_gain_pickup_attribution(people):
    a = InteractionAnalyzer()
    baseline(a)
    for i in range(6):
        a.process(1+i*0.2, [obj(0.5)], people)
    assert a.finish()["events"] == []


def test_missing_wrists_never_mean_release():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for i in range(15):
        a.process(2+i*0.2, [obj(0.5)], [person(wrists_visible=False)])
    assert a.finish()["total"]["putdown_count"] == 0


def test_short_occlusion_preserves_one_session_but_recovery_cannot_close_immediately():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    a.process(2.0, [], [person()])
    a.process(2.2, [obj(0.65, continuous=False)], [person()])
    assert a.report()["total"]["putdown_count"] == 0
    for i in range(6):
        a.process(2.4+i*0.2, [obj(0.65)], [person()])
    r = a.finish()
    assert r["total"]["completed_count"] == 1
    assert r["sessions"][0]["observation_gaps"] == 1


def test_long_occlusion_and_reappearance_cannot_complete_old_session():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for i in range(13):
        a.process(2+i*0.2, [], [person()])
    for i in range(10):
        a.process(5+i*0.2, [obj(0.6)], [person()])
    r = a.finish()
    assert r["total"]["putdown_count"] == 0
    assert r["sessions"][0]["end_reason"] == "object_lost"


def test_actor_loss_and_possible_handover_are_incomplete():
    for handover in (False, True):
        a = InteractionAnalyzer()
        baseline(a); pickup(a)
        for i in range(13):
            people = [person(), person(2, 0.5)] if handover else []
            a.process(2+i*0.2, [obj(0.5)], people)
        for i in range(6):
            a.process(5+i*0.2, [obj(0.5)], [person()])
        r = a.finish()
        assert r["total"]["putdown_count"] == 0
        assert r["sessions"][0]["end_reason"] == ("possible_handover" if handover else "user_track_lost")


def test_gap_or_relocalized_box_does_not_create_motion():
    for timestamp, continuous in ((1.0, False), (5.0, True)):
        a = InteractionAnalyzer()
        baseline(a)
        a.process(timestamp, [obj(0.6, continuous=continuous)], [person(1, 0.6)])
        for i in range(1, 8):
            a.process(timestamp+i*0.2, [obj(0.6)], [person(1, 0.6)])
        assert a.finish()["events"] == []


def test_real_fast_pickup_keeps_precontact_evidence_through_short_tracking_break():
    fixture = json.loads((Path(__file__).parent / "fixtures/vlx_dog_fast_pickup_trace.json").read_text())
    a = InteractionAnalyzer()
    for row in fixture["observations"]:
        a.process(row["timestamp_seconds"],
                  [ObjectEvidence(**o) for o in row["objects"]],
                  [PersonEvidence(**p) for p in row["people"]])
    result = a.finish()
    assert result["total"]["pickup_count"] == 1
    session = result["sessions"][0]
    assert session["object_key"] == "yellow_robot_dog"
    assert session["user_id"] == 2
    lo, hi = fixture["expected_pickup_range"]
    assert lo <= session["pickup_seconds"] <= hi
    assert session["pickup_confirmed_seconds"] > session["pickup_seconds"]
    assert session["observation_gaps"] > 0
    assert session["status"] == "incomplete"
    assert session["putdown_seconds"] is None


def test_precontact_cannot_bridge_unrelated_relocalization_or_user_change():
    for different_user in (False, True):
        a = InteractionAnalyzer()
        baseline(a)
        a.process(1.0, [obj()], [person(1, 0.4)])
        # Either no co-moving hand or a different person after the jump.
        for i in range(8):
            p = person(2, 0.6) if different_user else person(1, 0.4)
            a.process(1.2+i*0.2, [obj(0.6, continuous=i != 0)], [p])
        assert a.finish()["events"] == []


def test_real_other_hand_and_different_guitar_cannot_bridge_a_pickup():
    fixture = json.loads((Path(__file__).parent / "fixtures/vlx_guitar_instance_switch_trace.json").read_text())
    a = InteractionAnalyzer()
    for row in fixture["observations"]:
        a.process(row["timestamp_seconds"],
                  [ObjectEvidence(**o) for o in row["objects"]],
                  [PersonEvidence(**p) for p in row["people"]])
    assert a.finish()["events"] == []


def test_real_guitar_box_change_before_hand_contact_cannot_count_as_pickup():
    fixture = json.loads((Path(__file__).parent / "fixtures/vlx_guitar_precontact_box_change_trace.json").read_text())
    a = InteractionAnalyzer()
    for row in fixture["observations"]:
        a.process(row["timestamp_seconds"],
                  [ObjectEvidence(**o) for o in row["objects"]],
                  [PersonEvidence(**p) for p in row["people"]])
    assert a.finish()["events"] == []


@pytest.mark.parametrize("actor", ["blue_user", "black_user"])
def test_real_guitar_lift_starts_holding_timer_despite_changed_detection_extent(actor):
    fixture = json.loads((Path(__file__).parent / f"fixtures/vlx_guitar_{actor}_holding_trace.json").read_text())
    a = InteractionAnalyzer()
    checked = False
    for row in fixture["observations"]:
        decision = a.process(row["timestamp_seconds"],
                             [ObjectEvidence(**o) for o in row["objects"]],
                             [PersonEvidence(**p) for p in row["people"]])
        if abs(row["timestamp_seconds"] - fixture["check_holding_at"]) < 0.001:
            state = next(o for o in decision["objects"] if o["object_key"] == "white_guitar")
            assert state["state"] == "holding"
            assert state["user_id"] == fixture["expected_user_id"]
            lo, hi = fixture["expected_pickup_range"]
            assert row["timestamp_seconds"] - hi <= state["elapsed_holding_seconds"] <= row["timestamp_seconds"] - lo
            checked = True
    assert checked
    sessions = [s for s in a.finish()["sessions"] if s["user_id"] == fixture["expected_user_id"]]
    assert sessions
    assert all(s["pickup_seconds"] >= fixture["earliest_possible_pickup"] for s in sessions)


def test_old_contact_cannot_recover_pickup_after_long_missing_interval():
    a = InteractionAnalyzer()
    baseline(a)
    a.process(1.0, [obj()], [person(1, 0.4)])
    for i in range(13):
        a.process(1.2+i*0.2, [], [person(1)])
    for i in range(8):
        a.process(4+i*0.2, [obj(0.6+i*0.01, continuous=i != 0)], [person(1, 0.6+i*0.01)])
    assert a.finish()["events"] == []


def test_either_previously_observed_contacting_hand_can_start_pickup():
    a = InteractionAnalyzer()
    baseline(a)
    def two_hands(moving_x):
        return PersonEvidence(1, (0.05, 0.1, 0.85, 0.95), ((0.44, 0.44), (moving_x+0.05, 0.44)))
    a.process(1.0, [obj()], [two_hands(0.4)])
    for i, x in enumerate((0.425, 0.45, 0.475, 0.5)):
        a.process(1.2+i*0.2, [obj(x)], [two_hands(x)])
    session, = a.finish()["sessions"]
    assert session["pickup_seconds"] == pytest.approx(1.2)
    assert session["user_id"] == 1


def test_box_extent_change_with_moving_hand_is_not_object_translation():
    a = InteractionAnalyzer()
    for i in range(5):
        a.process(i*0.2, [ObjectEvidence(KEY, (0.4, 0.3, 0.5, 0.7))], [person()])
    for i in range(12):
        y = 0.3+min(i, 5)*0.035
        p = PersonEvidence(1, (0.05, 0.1, 0.85, 0.95), ((0.45, y+0.01), (0.1, 0.2)))
        a.process(1+i*0.2, [ObjectEvidence(KEY, (0.4, y, 0.5, 0.7))], [p])
    assert a.finish()["events"] == []


@pytest.mark.parametrize("reappear_at,x,expected", [(3.6, 0.4, 1), (3.6, 0.6, 0), (35.0, 0.4, 0)])
def test_only_recent_matching_rest_pose_can_rearm_after_loss(reappear_at, x, expected):
    a = InteractionAnalyzer()
    baseline(a)
    for i in range(12):
        a.process(1+i*0.2, [], [person()])
    for i in range(6):
        a.process(reappear_at+i*0.2, [obj(x, continuous=i != 0)], [person(1, x)])
    # Reacquiring and touching a known pose alone must never count a pickup.
    assert a.report()["total"]["pickup_count"] == 0
    for i in range(6):
        moved = x+0.025*(i+1)
        a.process(reappear_at+1.2+i*0.2, [obj(moved)], [person(1, moved)])
    result = a.finish()
    assert result["total"]["pickup_count"] == expected
    assert result["total"]["putdown_count"] == 0


def test_pending_pickup_cannot_transfer_to_a_different_user():
    a = InteractionAnalyzer()
    baseline(a)
    a.process(1.0, [obj()], [person(1, 0.4)])
    a.process(1.2, [obj(0.425)], [person(1, 0.425)])
    for i in range(6):
        x = 0.45+i*0.025
        a.process(1.4+i*0.2, [obj(x)], [person(2, x)])
    assert a.finish()["events"] == []


def test_moving_away_from_hand_is_not_stable_release():
    a = InteractionAnalyzer()
    baseline(a); pickup(a)
    for i in range(15):
        a.process(2+i*0.2, [obj(0.5+i*0.01)], [person()])
    assert a.finish()["total"]["putdown_count"] == 0


def test_invalid_observations_fail_before_modifying_state():
    a = InteractionAnalyzer()
    a.process(0, [obj()], [])
    for t, objects, people in ((0, [obj()], []), (float("nan"), [], []),
                               (1, [obj(), obj()], []), (1, [obj()], [person(), person()])):
        with pytest.raises(ValueError):
            a.process(t, objects, people)
    assert a.last_time == 0
    with pytest.raises(ValueError):
        InteractionConfig(pickup_confirm_seconds=0)


def test_two_objects_can_be_held_by_one_user_with_independent_durations():
    a = InteractionAnalyzer()
    second = TARGETS[2].key
    for i in range(20):
        t = i*0.2
        if t < 1:
            x, contact = 0.4, None
        elif t < 1.2:
            x, contact = 0.4, 0.4
        elif t < 2:
            x = 0.425+round((t-1.2)/0.2)*0.025
            contact = x
        else:
            x, contact = 0.5, None
        a.process(t, [obj(x), obj(x, second)], [person(1, contact)])
    r = a.finish()
    assert r["total"]["completed_count"] == 2
    assert r["by_user"][0]["completed_count"] == 2
    assert len(r["by_user_object"]) == 2
    assert len({e["event_id"] for e in r["events"]}) == 6


def detection(x, appearance):
    return PersonDetection((x, 0.2, x+0.15, 0.8), ((x, 0.4), (x+0.1, 0.4)), appearance)


def test_users_numbered_on_first_appearance_and_follow_detection_reordering():
    tracker = AnonymousUsers()
    first = tracker.update(0, [detection(0.65, (0, 1)), detection(0.15, (1, 0))])
    assert [p.user_id for p in first] == [2, 1]
    second = tracker.update(0.2, [detection(0.16, (1, 0)), detection(0.64, (0, 1))])
    assert [p.user_id for p in second] == [1, 2]
    assert AnonymousUsers().update(0, [detection(0.15, (1, 0))])[0].user_id == 1


def test_clothing_reentry_retains_video_user_and_ambiguity_is_not_guessed():
    tracker = AnonymousUsers()
    tracker.update(0, [detection(0.1, (1, 0)), detection(0.7, (0, 1))])
    tracker.update(3, [])
    assert tracker.update(10, [detection(0.5, (1, 0))])[0].user_id == 1
    assert tracker.report()[0]["appearance_matched_reentries"] == 1
    ambiguous = AnonymousUsers()
    ambiguous.update(0, [detection(0.1, (1, 0)), detection(0.7, (1, 0))])
    ambiguous.update(3, [])
    result = ambiguous.update(5, [detection(0.4, (1, 0))])
    assert result[0].user_id is None
    assert len(ambiguous.report()) == 2


def test_object_parser_rejects_duplicate_instances_and_cross_class_same_box():
    def row(index, box):
        return dict(label=TARGETS[index].phrase, **dict(zip(("xmin", "ymin", "xmax", "ymax"), box)))
    selected, ambiguous, _ = parse_objects({"result_bbox_list": [row(0, (10, 10, 30, 30)), row(0, (70, 70, 90, 90))]}, 100, 100)
    assert selected == [] and ambiguous == [0]
    selected, ambiguous, _ = parse_objects({"result_bbox_list": [row(3, (10, 10, 30, 30)), row(5, (10, 10, 30, 30))]}, 100, 100)
    assert selected == [] and ambiguous == [3, 5]
    with pytest.raises(ValueError):
        parse_objects({"result_bbox_list": [row(0, (10, 10, 101, 30))]}, 100, 100)


def mock_vision(outputs):
    from scripts.vlx_interaction_vision import VideoVision
    from scripts.vlx_live_tracking import LiveTracks
    class Worker:
        calls = 0
        def run_task(self, *_args, **_kwargs):
            result = outputs[min(self.calls, len(outputs)-1)]
            self.calls += 1
            return result
    class People:
        users = AnonymousUsers()
        def infer(self, *_):
            return []
    vision = VideoVision.__new__(VideoVision)
    vision.config = {"recognition_interval_seconds": 0.8, "max_new_tokens": 256,
                     "interaction": {"max_observation_gap_seconds": 0.75}}
    vision.people, vision.worker = People(), Worker()
    vision.proposer = lambda image: [[20, 20, 50, 50]]
    vision.tracks = LiveTracks(history_seconds=2, ttl_seconds=1.6)
    vision.last_recognition = -float("inf")
    vision.previous_boxes = {}
    vision.last_observation = None
    vision.ambiguous_classes = set()
    vision.correction_candidates = {}
    return vision


def raw_box(index, box=(20, 20, 50, 50)):
    return {"label": TARGETS[index].phrase, **dict(zip(("xmin", "ymin", "xmax", "ymax"), box))}


def test_partial_and_full_boxes_for_one_region_cannot_count_as_two_objects():
    raw = {"result_bbox_list": [raw_box(2, (20, 10, 50, 70)), raw_box(5, (20, 30, 50, 70))]}
    selected, ambiguous, _ = parse_objects(raw, 100, 100)
    assert selected == [] and ambiguous == [2, 5]


def test_known_pink_attribute_only_vetoes_contradictory_yellow_not_pale_pink():
    raw = {"result_bbox_list": [raw_box(2), raw_box(5)]}
    frame = np.full((100, 100, 3), (20, 200, 230), np.uint8)
    rejected = []
    selected, ambiguous, _ = parse_objects(raw, 100, 100, frame=frame, appearance_rejections=rejected)
    assert [d["class_id"] for d in selected] == [5]
    assert ambiguous == [] and rejected[0]["class_id"] == 2
    frame[:] = (215, 205, 230)
    selected, _, _ = parse_objects({"result_bbox_list": [raw_box(2)]}, 100, 100, frame=frame)
    assert [d["class_id"] for d in selected] == [2]


def test_actual_user_pink_reference_is_kept_and_yellow_bee_label_is_vetoed():
    from scripts.vlx_target_evidence import appearance_contradiction
    directory = Path(__file__).parent / "fixtures"
    reference = cv2.imread(str(directory / "vlx_user_pink_plush_reference.png"))
    bee = cv2.imread(str(directory / "vlx_bee_mislabeled_pink_crop.png"))
    for frame, rejected in ((reference, False), (bee, True)):
        height, width = frame.shape[:2]
        assert bool(appearance_contradiction("pink_plush", (0, 0, width, height), frame)) == rejected
        assert appearance_contradiction("bee_plush", (0, 0, width, height), frame) is None


def test_low_texture_object_gets_fresh_direct_evidence_instead_of_frozen_box():
    vision = mock_vision([{"result_bbox_list": [raw_box(0)]}])
    frame = np.full((100, 100, 3), 70, dtype=np.uint8)
    vision.advance(frame, 1, 0)
    objects, _, _ = vision.observe(frame, 1, 0)
    assert len(objects) == 1 and not objects[0].continuous
    vision.advance(frame, 2, 0.2)
    objects, _, diagnostics = vision.observe(frame, 2, 0.2)
    assert objects[0].continuous
    assert vision.worker.calls == 2
    assert diagnostics["recognition"]["timestamp_seconds"] == 0.2


def test_model_cross_label_jump_is_not_reintroduced_by_direct_boxes():
    vision = mock_vision([{"result_bbox_list": [raw_box(0)]}, {"result_bbox_list": [raw_box(2)]}])
    frame = np.random.default_rng(42).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    for i in range(6):
        t = i*0.2
        vision.advance(frame, i+1, t)
        objects, _, _ = vision.observe(frame, i+1, t)
        assert all(o.key != TARGETS[2].key for o in objects)
    assert 2 in vision.ambiguous_classes


@pytest.mark.parametrize("known, shifted", [((20, 20, 60, 60), (10, 20, 50, 60)),
                                           ((36, 20, 75, 75), (15, 25, 67, 70))])
def test_new_label_cannot_claim_an_offset_box_on_a_known_moving_instance(known, shifted):
    # Real failure: an orange-toy label was seeded on the already tracked bee
    # with IoU around .6, then its duplicate track suppressed the held object.
    vision = mock_vision([{"result_bbox_list": [raw_box(5, known)]},
                          {"result_bbox_list": [raw_box(0, shifted)]}])
    frame = np.random.default_rng(29).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    for i in range(7):
        vision.advance(frame, i+1, i*.2)
        objects, _, _ = vision.observe(frame, i+1, i*.2)
        assert [o.key for o in objects] == ["bee_plush"]


def test_ambiguous_model_confirmation_stays_unknown_between_inferences():
    vision = mock_vision([{"result_bbox_list": [raw_box(0)]}, {"result_bbox_list": [raw_box(0), raw_box(0, (65, 65, 90, 90))]}])
    frame = np.random.default_rng(9).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    for i in range(6):
        t = i*0.2
        vision.advance(frame, i+1, t)
        objects, _, _ = vision.observe(frame, i+1, t)
        if i >= 4:
            assert objects == []
    assert vision.worker.calls == 2


def test_repeated_consistent_model_correction_replaces_stale_track_without_motion():
    vision = mock_vision([{"result_bbox_list": [raw_box(0)]},
                          {"result_bbox_list": [raw_box(0, (65, 65, 90, 90))]}])
    frame = np.random.default_rng(19).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    for i in range(9):
        t = i*.2
        vision.advance(frame, i+1, t)
        objects, _, _ = vision.observe(frame, i+1, t)
        if 4 <= i < 8:
            assert len(objects) == 1
            assert objects[0].bbox == pytest.approx((.2, .2, .5, .5), abs=1e-5)
    assert len(objects) == 1
    assert objects[0].bbox == pytest.approx((.65, .65, .9, .9))
    assert not objects[0].continuous


def test_single_spurious_jump_preserves_pixel_verified_track_until_correction():
    vision = mock_vision([{"result_bbox_list": [raw_box(0)]},
                          {"result_bbox_list": [raw_box(0, (65, 65, 90, 90))]},
                          {"result_bbox_list": [raw_box(0)]}])
    frame = np.random.default_rng(19).integers(0, 255, (100, 100, 3), dtype=np.uint8)
    for i in range(9):
        t = i*.2
        vision.advance(frame, i+1, t)
        objects, _, _ = vision.observe(frame, i+1, t)
        assert len(objects) == 1
        assert objects[0].bbox == pytest.approx((.2, .2, .5, .5), abs=1e-5)
        if i:
            assert objects[0].continuous


def test_vfr_pts_not_inference_time_or_fps_drives_timeline():
    probe = {"streams": [{"width": 32, "height": 32}], "frames": [
        {"best_effort_timestamp_time": "10.0"}, {"best_effort_timestamp_time": "10.04"},
        {"best_effort_timestamp_time": "10.19", "duration_time": "0.11"}]}
    timeline = parse_timeline(probe)
    assert timeline["timestamps"] == pytest.approx([0, 0.04, 0.19])
    assert timeline["duration_seconds"] == pytest.approx(0.30)
    probe["frames"][1]["best_effort_timestamp_time"] = "10.0"
    with pytest.raises(ValueError):
        parse_timeline(probe)


class FakeVision:
    def __init__(self, *_):
        self.advanced = []

    def advance(self, frame, sequence, timestamp):
        self.advanced.append(sequence)

    def observe(self, frame, sequence, timestamp):
        if timestamp < 1:
            x, contact = 0.4, None
        elif timestamp < 1.2:
            x, contact = 0.4, 0.4
        elif timestamp < 2:
            x = 0.425 + round((timestamp-1.2)/0.2)*0.025
            contact = x
        else:
            x, contact = 0.5, None
        return [obj(x)], [person(1, contact)], {"fixture": True}

    def users_report(self):
        return [{"user_id": 1, "user_label": "用户1"}]


def isolated_io_config(tmp_path):
    """Exercise real I/O without reading deployment/model configuration."""
    vlx_path = tmp_path / "vlx-test.yaml"
    vlx_path.write_text(json.dumps({
        "model_path": str(tmp_path / "unused-model"),
        "external_repository": str(tmp_path / "unused-repository"),
        "detector_checkpoint": str(tmp_path / "unused-detector.pt"),
    }), encoding="utf-8")
    config_path = tmp_path / "interaction-test.yaml"
    config_path.write_text(json.dumps({
        "vlx_config": str(vlx_path), "pose_weights": str(tmp_path / "unused-pose.pt"),
        "font_path": str(tmp_path / "unused-font.ttf"), "runtime_root": str(tmp_path),
        "sample_fps": 5.0, "preview_fps": 5.0, "recognition_interval_seconds": .8,
        "tracking_ttl_seconds": 6.0, "appearance_tracking": "csrt",
        "pose_confidence": .4, "wrist_confidence": .45, "pose_image_size": 1280,
        "max_new_tokens": 256, "interaction": {},
    }), encoding="utf-8")
    return load_config(config_path)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="media tools unavailable")
def test_real_video_io_with_injected_vision_exports_counts_timestamps_and_preview(tmp_path):
    from PIL import ImageFont
    font_path = None
    for candidate in ("C:/Windows/Fonts/msyh.ttc", "DejaVuSans.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            font_path = ImageFont.truetype(candidate, 17).path
            break
        except OSError:
            continue
    if font_path is None:
        pytest.skip("a TrueType font is required for the media I/O test")
    video = tmp_path / "source.mp4"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 5, (128, 96))
    assert writer.isOpened()
    for _ in range(20):
        writer.write(np.full((96, 128, 3), 70, dtype=np.uint8))
    writer.release()
    config, vlx = isolated_io_config(tmp_path)
    config["font_path"] = font_path
    output = tmp_path / "result"
    result = run_analysis(video, output, config, vlx, vision_factory=FakeVision)
    assert result["processed_samples"] == 20
    assert result["total"]["completed_count"] == 1
    assert result["sessions"][0]["holding_seconds"] == pytest.approx(0.8)
    assert (output / "annotated.mp4").stat().st_size > 0
    assert json.loads((output / "status.json").read_text())["status"] == "completed"
    assert len((output / "observations.jsonl").read_text(encoding="utf-8").splitlines()) == 20
    assert len((output / "events.jsonl").read_text(encoding="utf-8").splitlines()) == 3
    with pytest.raises(FileExistsError):
        run_analysis(video, output, config, vlx, vision_factory=FakeVision)


def test_pipeline_failure_keeps_failure_status_and_no_success_report(tmp_path, monkeypatch):
    import scripts.run_vlx_interaction_video as runner
    config, vlx = isolated_io_config(tmp_path)
    def fail(_):
        raise ValueError("unreadable video")
    monkeypatch.setattr(runner, "probe_video", fail)
    output = tmp_path / "failed"
    with pytest.raises(ValueError, match="unreadable"):
        run_analysis(tmp_path / "source.mp4", output, config, vlx, vision_factory=FakeVision)
    assert json.loads((output / "status.json").read_text())["status"] == "failed"
    assert not (output / "result.json").exists()
