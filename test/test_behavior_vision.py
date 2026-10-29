from __future__ import annotations

import json
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_vision import (
    CameraRole,
    BehaviorVisionAdapter,
    BlueVestDetector,
    GroundPlaneProjector,
    PersonRoleResolver,
    PersonTrackDetection,
    VestDetection,
    draw_behavior_area_overlay,
    localize_staff_greeting_overlay_line,
    load_behavior_camera_configs,
)
from agent.behavior_monitor import PolygonArea
from scripts.diagnose_staff_role_integrity import validate_subject_truth


class FakeBoxes:
    def __init__(self, xyxy, conf, cls, ids=None):
        self.xyxy = xyxy
        self.conf = conf
        self.cls = cls
        self.id = ids


class FakeKeypoints:
    def __init__(self, xy, conf):
        self.xy = xy
        self.conf = conf


class FakeResult:
    def __init__(self, boxes, names, keypoints=None):
        self.boxes = boxes
        self.names = names
        self.keypoints = keypoints


class FakeDetectionModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def track(self, frame, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


class FakePoseModel:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def predict(self, frame, **kwargs):
        self.calls.append(kwargs)
        return [self.result]


class FakeVestModel(FakePoseModel):
    pass


def detection_result():
    xyxy = [[20, 10, 120, 90]]
    conf = [0.95]
    cls = [0]
    ids = [5]
    return FakeResult(FakeBoxes(xyxy, conf, cls, ids), {0: "person"})


def overlapping_detection_result():
    return FakeResult(
        FakeBoxes(
            [[20, 10, 120, 110], [25, 10, 125, 110]],
            [0.95, 0.94],
            [0, 0],
            [5, 6],
        ),
        {0: "person"},
    )


def pose_result():
    xy = [[[0.0, 0.0] for _ in range(17)]]
    confidence = [[0.0 for _ in range(17)]]
    xy[0][9] = [75.0, 45.0]
    xy[0][10] = [80.0, 46.0]
    confidence[0][9] = 0.9
    confidence[0][10] = 0.8
    return FakeResult(
        FakeBoxes([[20, 10, 120, 90]], [0.9], [0]),
        {0: "person"},
        FakeKeypoints(xy, confidence),
    )


def vest_result():
    return FakeResult(
        FakeBoxes(
            [[50, 20, 90, 55], [10, 10, 20, 20], [30, 30, 40, 40]],
            [0.68, 0.99, 0.67],
            [0, 1, 0],
        ),
        {0: "staff_vest", 1: "person"},
    )


def empty_vest_result():
    return FakeResult(FakeBoxes([], [], []), {0: "staff_vest"})


class BlueVestDetectorTests(unittest.TestCase):
    def test_vest_detection_rejects_invalid_confidence(self):
        for confidence in (-0.01, 1.01, float("nan"), float("inf"), True):
            with self.subTest(confidence=confidence):
                with self.assertRaises(ValueError):
                    VestDetection(
                        bbox=(10.0, 10.0, 20.0, 20.0),
                        confidence=confidence,
                        class_name="staff_vest",
                    )

    def test_detect_keeps_only_staff_vest_at_configured_threshold(self):
        model = FakeVestModel(vest_result())
        detector = BlueVestDetector(
            model=model,
            device="cuda:0",
            confidence_threshold=0.68,
        )

        detections = detector.detect(np.zeros((100, 200, 3), dtype=np.uint8))

        self.assertEqual(
            [
                VestDetection(
                    bbox=(50.0, 20.0, 90.0, 55.0),
                    confidence=0.68,
                    class_name="staff_vest",
                )
            ],
            detections,
        )
        self.assertEqual("cuda:0", model.calls[0]["device"])
        self.assertEqual(0.68, model.calls[0]["conf"])

    def test_detect_rejects_non_finite_model_confidence(self):
        model = FakeVestModel(
            FakeResult(
                FakeBoxes([[50, 20, 90, 55]], [float("nan")], [0]),
                {0: "staff_vest"},
            )
        )

        with self.assertRaises(ValueError):
            BlueVestDetector(model=model).detect(
                np.zeros((100, 200, 3), dtype=np.uint8)
            )


class PersonRoleResolverTests(unittest.TestCase):
    @staticmethod
    def _track(track_id, bbox):
        return PersonTrackDetection(
            track_id=track_id,
            track_token=f"runtime:{track_id}",
            bbox=bbox,
            confidence=0.9,
        )

    @staticmethod
    def _vest(bbox=(50.0, 25.0, 90.0, 65.0)):
        return VestDetection(
            bbox=bbox,
            confidence=0.9,
            class_name="staff_vest",
        )

    def test_ambiguous_vest_overlap_assigns_no_hit(self):
        resolver = PersonRoleResolver()
        tracks = [
            self._track(1, (20.0, 10.0, 120.0, 110.0)),
            self._track(2, (25.0, 10.0, 125.0, 110.0)),
        ]

        roles = resolver.resolve(tracks, [self._vest()])

        self.assertEqual([0, 0], [role.vest_hits for role in roles])
        self.assertEqual(["uncertain", "uncertain"], [role.role for role in roles])
        self.assertEqual("ambiguous", resolver.last_association_diagnostics[0].status)

    def test_confirmed_staff_remains_staff_across_short_vest_misses(self):
        resolver = PersonRoleResolver()
        track = self._track(1, (20.0, 10.0, 120.0, 110.0))
        for _ in range(3):
            role = resolver.resolve([track], [self._vest()])[0]
        self.assertEqual("staff", role.role)

        missed_roles = [resolver.resolve([track], [])[0] for _ in range(5)]

        self.assertEqual(["staff"] * 5, [role.role for role in missed_roles])

    def test_insufficient_evidence_is_unknown(self):
        resolver = PersonRoleResolver()
        track = self._track(1, (20.0, 10.0, 120.0, 110.0))

        roles = [resolver.resolve([track], [])[0] for _ in range(4)]

        self.assertEqual(["unknown"] * 4, [role.role for role in roles])

    def test_overlapping_staff_and_customer_does_not_give_customer_a_hit(self):
        resolver = PersonRoleResolver()
        staff = self._track(1, (40.0, 10.0, 110.0, 110.0))
        customer = self._track(2, (10.0, 0.0, 130.0, 150.0))

        roles = resolver.resolve([staff, customer], [self._vest()])

        by_token = {role.track_token: role for role in roles}
        self.assertEqual(0, by_token[customer.track_token].vest_hits)
        self.assertLessEqual(sum(role.vest_hits for role in roles), 1)

    def test_one_vest_contributes_to_at_most_one_track(self):
        resolver = PersonRoleResolver(ambiguity_margin=0.0)
        tracks = [
            self._track(1, (20.0, 10.0, 120.0, 110.0)),
            self._track(2, (30.0, 5.0, 150.0, 140.0)),
        ]

        roles = resolver.resolve(tracks, [self._vest()])

        self.assertLessEqual(sum(role.vest_hits for role in roles), 1)

    def test_merged_person_bbox_rejects_low_torso_vest_evidence(self):
        resolver = PersonRoleResolver()
        merged_customer_track = self._track(
            23,
            (901.2353515625, 464.5238647461, 1221.8760986328, 1330.4172363281),
        )
        staff_vest = self._vest(
            (954.2398071289, 830.9846801758, 1161.3588867188, 1176.9406738281)
        )

        role = resolver.resolve([merged_customer_track], [staff_vest])[0]

        self.assertEqual(0, role.vest_hits)
        self.assertEqual(
            "no_candidate", resolver.last_association_diagnostics[0].status
        )


class StaffGreetingSubjectTruthTests(unittest.TestCase):
    SOURCE_SHA256 = "a" * 64
    FRAME_SIZE = [2560, 1440]

    @staticmethod
    def _fixture(*subjects):
        return {
            "schema_version": "staff_greeting_subject_truth_v1",
            "coordinate_mode": "pixel_xyxy",
            "source_sha256": StaffGreetingSubjectTruthTests.SOURCE_SHA256,
            "source_frame_size": StaffGreetingSubjectTruthTests.FRAME_SIZE,
            "frames": [{"pts_seconds": 68.0, "subjects": list(subjects)}],
        }

    @staticmethod
    def _row(*tracks, associations=()):
        person_tracks = []
        observations = []
        for track_id, bbox, role, vest_hits in tracks:
            token = f"candidate:{track_id}"
            person_tracks.append(
                {
                    "track_id": track_id,
                    "track_token": token,
                    "bbox": list(bbox),
                    "confidence": 0.9,
                }
            )
            observations.append(
                {
                    "track_id": track_id,
                    "track_token": token,
                    "role": role,
                    "vest_hits": vest_hits,
                    "required_hits": 3,
                }
            )
        return {
            "pts_seconds": 68.0,
            "source_sha256": StaffGreetingSubjectTruthTests.SOURCE_SHA256,
            "frame_width": StaffGreetingSubjectTruthTests.FRAME_SIZE[0],
            "frame_height": StaffGreetingSubjectTruthTests.FRAME_SIZE[1],
            "person_tracks": person_tracks,
            "observations": observations,
            "vest_associations": list(associations),
        }

    def test_matching_source_provenance_passes(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (13, (10.0, 10.0, 60.0, 100.0), "customer", 0)
        )

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("pass", report["status"])
        self.assertEqual("verified", report["source_provenance_status"])

    def test_wrong_source_sha256_fails_closed(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (13, (10.0, 10.0, 60.0, 100.0), "customer", 0)
        )
        candidate["source_sha256"] = "b" * 64

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("fail", report["status"])
        self.assertEqual("subject_truth_source_mismatch", report["reason"])

    def test_wrong_source_frame_size_fails_closed(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (13, (10.0, 10.0, 60.0, 100.0), "customer", 0)
        )
        candidate["frame_width"] = 1920

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("fail", report["status"])
        self.assertEqual("subject_truth_frame_size_mismatch", report["reason"])

    def test_track_id_change_passes_when_subject_bbox_and_role_match(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (13, (10.0, 10.0, 60.0, 100.0), "customer", 0)
        )

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("pass", report["status"])
        self.assertEqual(13, report["frames"][0]["subjects"][0]["track_id"])

    def test_customer_matched_to_staff_fails(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row((13, (10.0, 10.0, 60.0, 100.0), "staff", 3))

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("fail", report["status"])
        self.assertEqual(
            "expected_customer_observed_staff",
            report["frames"][0]["subjects"][0]["reason"],
        )

    def test_staff_with_assigned_vest_evidence_passes(self):
        fixture = self._fixture(
            {
                "subject": "blue_vest_employee",
                "expected_role": "staff",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (42, (10.0, 10.0, 60.0, 100.0), "staff", 5),
            associations=(
                {
                    "status": "assigned",
                    "assigned_track_token": "candidate:42",
                },
            ),
        )

        report = validate_subject_truth([candidate], fixture)

        self.assertEqual("pass", report["status"])

    def test_ambiguous_subject_match_is_unresolved_without_guessing(self):
        fixture = self._fixture(
            {
                "subject": "black_shirt_customer",
                "expected_role": "customer",
                "bbox": [10.0, 10.0, 60.0, 100.0],
            }
        )
        candidate = self._row(
            (13, (10.0, 10.0, 60.0, 100.0), "customer", 0),
            (57, (11.0, 10.0, 61.0, 100.0), "customer", 0),
        )

        report = validate_subject_truth([candidate], fixture)

        subject = report["frames"][0]["subjects"][0]
        self.assertEqual("unresolved", report["status"])
        self.assertEqual("ambiguous", subject["match_status"])
        self.assertIsNone(subject["track_id"])


class BehaviorVisionAdapterTests(unittest.TestCase):
    def test_chinese_overlay_localizes_state_and_draws_brand_below_track_box(self):
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            staff_vest_model=FakeVestModel(vest_result()),
            pose_model=FakePoseModel(pose_result()),
            overlay_language="zh-CN",
            overlay_brand="秒象科技",
        )
        frame = np.zeros((300, 600, 3), dtype=np.uint8)
        for _ in range(3):
            adapter.process_frame(frame)
        captured = []

        def capture(rendered, items):
            captured.extend(items)
            return rendered

        with patch(
            "agent.behavior_vision.measure_overlay_text",
            return_value=(120, 30),
        ), patch(
            "agent.behavior_vision.draw_overlay_texts_bgr",
            side_effect=capture,
        ):
            adapter.render_debug_overlay(
                frame,
                {
                    adapter.last_observations[0].track_token: (
                        "GREETING STATE: WAITING_FOR_GREETING",
                    )
                },
            )

        texts = [item.text for item in captured]
        self.assertIn("秒象科技", texts)
        self.assertIn("轨迹 5｜店员", texts)
        self.assertIn("迎宾状态：等待迎宾", texts)
        brand = next(item for item in captured if item.text == "秒象科技")
        self.assertGreaterEqual(brand.position[1], 90)

    def test_chinese_overlay_line_translation_preserves_measurements(self):
        self.assertEqual(
            "世界坐标：(1.23,4.56)m",
            localize_staff_greeting_overlay_line("WORLD: (1.23,4.56)m"),
        )
        self.assertEqual(
            "所在区域：业务区域外",
            localize_staff_greeting_overlay_line("AREA: OUTSIDE_ROI"),
        )
        self.assertEqual(
            "迎宾状态：不符合迎宾条件",
            localize_staff_greeting_overlay_line(
                "GREETING STATE: NOT_ELIGIBLE"
            ),
        )

    def test_byte_track_and_vest_history_assign_staff_role(self):
        detector = FakeDetectionModel(detection_result())
        vest_model = FakeVestModel(vest_result())
        pose = FakePoseModel(pose_result())
        adapter = BehaviorVisionAdapter(
            store_id="store-1",
            camera_id="entrance-1",
            person_model=detector,
            staff_vest_model=vest_model,
            pose_model=pose,
            device="cuda:0",
            runtime_id="runtime",
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        first = adapter.process_frame(frame)
        second = adapter.process_frame(frame)
        third = adapter.process_frame(frame)

        self.assertEqual("unknown", first[0].role)
        self.assertEqual("unknown", second[0].role)
        self.assertEqual("staff", third[0].role)
        self.assertEqual("cuda:0", detector.calls[0]["device"])
        self.assertEqual("cuda:0", vest_model.calls[0]["device"])
        self.assertEqual("cuda:0", pose.calls[0]["device"])
        self.assertEqual("bytetrack.yaml", detector.calls[0]["tracker"])
        self.assertEqual([0], detector.calls[0]["classes"])
        self.assertEqual(3, len(detector.calls))
        self.assertEqual(3, len(vest_model.calls))
        self.assertEqual(3, len(pose.calls))
        self.assertEqual(5, third[0].track_id)
        self.assertEqual("store-1:entrance-1:runtime:5", third[0].track_token)
        self.assertEqual(2, len(third[0].wrists))
        self.assertAlmostEqual(0.375, third[0].wrists[0].x)
        self.assertAlmostEqual(0.45, third[0].wrists[0].y)

        lines = adapter.role_debug_lines(third[0].track_token)
        self.assertEqual(
            (
                "Track 5",
                "TOKEN: store-1:entrance-1:runtime:5",
                "ROLE: STAFF",
                "VEST: 0.68",
                "HITS: 3/5",
            ),
            lines,
        )
        debug = adapter.render_debug_overlay(frame)
        self.assertFalse(np.shares_memory(frame, debug))
        self.assertGreater(np.count_nonzero(debug), 0)

    def test_configured_person_inference_resolution_is_forwarded(self):
        detector = FakeDetectionModel(detection_result())
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=detector,
            staff_vest_model=FakeVestModel(empty_vest_result()),
            pose_model=FakePoseModel(pose_result()),
            person_inference_imgsz=1280,
        )

        adapter.process_frame(np.zeros((100, 200, 3), dtype=np.uint8))

        self.assertEqual(1280, detector.calls[0]["imgsz"])

    def test_overlapping_people_receive_uncertain_instead_of_shared_vest(self):
        adapter = BehaviorVisionAdapter(
            store_id="store-1",
            camera_id="entrance-1",
            person_model=FakeDetectionModel(overlapping_detection_result()),
            staff_vest_model=FakeVestModel(
                FakeResult(
                    FakeBoxes([[50, 25, 90, 65]], [0.91], [0]),
                    {0: "staff_vest"},
                )
            ),
            pose_model=FakePoseModel(pose_result()),
            runtime_id="runtime",
        )

        observations = adapter.process_frame(
            np.zeros((120, 200, 3), dtype=np.uint8)
        )

        self.assertEqual(["uncertain", "uncertain"], [item.role for item in observations])
        self.assertEqual(
            {
                "store-1:entrance-1:runtime:5",
                "store-1:entrance-1:runtime:6",
            },
            {item.track_token for item in observations},
        )
        self.assertIn(
            "REASON: overlapping_candidates",
            adapter.role_debug_lines(observations[0].track_token),
        )

    def test_person_without_vest_becomes_customer_after_history(self):
        detector = FakeDetectionModel(detection_result())
        vest_model = FakeVestModel(empty_vest_result())
        pose = FakePoseModel(pose_result())
        adapter = BehaviorVisionAdapter(
            camera_id="display-1",
            person_model=detector,
            staff_vest_model=vest_model,
            pose_model=pose,
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        observations = [adapter.process_frame(frame)[0] for _ in range(5)]

        self.assertEqual(["unknown"] * 4 + ["customer"], [item.role for item in observations])

    def test_known_member_can_be_customer_before_role_window_finishes(self):
        detector = FakeDetectionModel(detection_result())
        vest_model = FakeVestModel(empty_vest_result())
        pose = FakePoseModel(pose_result())
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=detector,
            staff_vest_model=vest_model,
            pose_model=pose,
            identity_resolver=lambda track_id: {
                "member_id": "member-gold",
                "member_tier": "gold",
            },
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        observation = adapter.process_frame(frame)[0]

        self.assertEqual("customer", observation.role)
        self.assertEqual("member-gold", observation.member_id)
        self.assertEqual("gold", observation.member_tier)

    def test_vest_detection_can_be_disabled_for_behavior_camera(self):
        detector = FakeDetectionModel(detection_result())
        pose = FakePoseModel(pose_result())
        adapter = BehaviorVisionAdapter(
            camera_id="display-1",
            person_model=detector,
            pose_model=pose,
            staff_vest_enabled=False,
        )

        observation = adapter.process_frame(
            np.zeros((100, 200, 3), dtype=np.uint8)
        )[0]

        self.assertEqual("unknown", observation.role)
        self.assertEqual(1, len(detector.calls))
        self.assertEqual(1, len(pose.calls))

    def test_runtime_statistics_are_logged_once_per_five_second_window(self):
        now = [0.0]
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            staff_vest_model=FakeVestModel(vest_result()),
            pose_model=FakePoseModel(pose_result()),
            runtime_id="session-123",
            statistics_clock=lambda: now[0],
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        with patch("agent.behavior_vision.logger.info") as log:
            adapter.process_frame(frame)
            now[0] = 4.99
            adapter.process_frame(frame)
            self.assertFalse(log.called)

            now[0] = 5.0
            adapter.process_frame(frame)

        self.assertEqual(1, log.call_count)
        message = log.call_args.args[0] % log.call_args.args[1:]
        for expected in (
            "camera_id=entrance-1",
            "runtime_session_id=session-123",
            "fps=",
            "person_inference_ms=",
            "vest_inference_ms=",
            "pose_inference_ms=",
            "active_tracks=1",
            "vest_detections=1",
        ):
            self.assertIn(expected, message)

    def test_spatial_debug_uses_foot_point_not_bbox_center_for_region(self):
        entry_trigger = PolygonArea(
            "entry",
            "entrance",
            ((0.2, 0.3), (0.5, 0.3), (0.5, 0.6), (0.2, 0.6)),
        )
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            staff_vest_model=FakeVestModel(empty_vest_result()),
            pose_model=FakePoseModel(pose_result()),
            ground_projector=GroundPlaneProjector((1, 0, 0, 0, 1, 0, 0, 0, 1)),
            entry_trigger=entry_trigger,
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        observation = adapter.process_frame(frame)[0]

        self.assertTrue(entry_trigger.contains((0.35, 0.5)))
        self.assertEqual((0.35, 0.9), observation.foot_point)
        self.assertFalse(entry_trigger.contains(observation.foot_point))
        self.assertIn(
            "AREA: OUTSIDE_ROI",
            adapter.spatial_debug_lines(observation.track_token),
        )

    def test_spatial_debug_reports_entry_trigger_when_foot_is_inside(self):
        entry_trigger = PolygonArea(
            "entry",
            "entrance",
            ((0.2, 0.8), (0.5, 0.8), (0.5, 1.0), (0.2, 1.0)),
        )
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            pose_model=FakePoseModel(pose_result()),
            staff_vest_enabled=False,
            entry_trigger=entry_trigger,
        )

        observation = adapter.process_frame(
            np.zeros((100, 200, 3), dtype=np.uint8)
        )[0]

        self.assertIn(
            "AREA: ENTRY_TRIGGER",
            adapter.spatial_debug_lines(observation.track_token),
        )

    def test_staff_outside_business_areas_remains_tracked(self):
        distant_area = PolygonArea(
            "greeting",
            "greeting",
            ((0.7, 0.1), (0.9, 0.1), (0.9, 0.3), (0.7, 0.3)),
        )
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            staff_vest_model=FakeVestModel(vest_result()),
            pose_model=FakePoseModel(pose_result()),
            greeting_area=distant_area,
        )
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        observations = [adapter.process_frame(frame)[0] for _ in range(3)]

        self.assertEqual("staff", observations[-1].role)
        self.assertIn(
            "AREA: OUTSIDE_ROI",
            adapter.spatial_debug_lines(observations[-1].track_token),
        )

    def test_adapter_warns_once_when_frame_size_differs_from_calibration(self):
        adapter = BehaviorVisionAdapter(
            camera_id="entrance-1",
            person_model=FakeDetectionModel(detection_result()),
            pose_model=FakePoseModel(pose_result()),
            staff_vest_enabled=False,
            calibration_image_size=(2560, 1440),
        )
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)

        with self.assertWarnsRegex(RuntimeWarning, "does not match"):
            adapter.process_frame(frame)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            adapter.process_frame(frame)

        self.assertEqual([], caught)

    def test_ground_projector_maps_normalized_point(self):
        projector = GroundPlaneProjector((2, 0, 1, 0, 3, 2, 0, 0, 1))
        self.assertEqual((2.0, 3.5), projector.project((0.5, 0.5)))

    def test_reception_ground_projector_maps_control_points_and_center(self):
        projector = GroundPlaneProjector((
            7.7008130081, 0.0, -0.5113821138,
            0.0, 6.0, -3.375,
            0.0, 0.0, 1.0,
        ))
        mappings = (
            ((0.06640625, 0.5625), (0.0, 0.0)),
            ((0.546875, 0.5625), (3.7, 0.0)),
            ((0.06640625, 0.72916667), (0.0, 1.0)),
            ((0.546875, 0.72916667), (3.7, 1.0)),
            ((0.306640625, 0.645833335), (1.85, 0.5)),
        )

        for image_point, expected_world in mappings:
            with self.subTest(image_point=image_point):
                actual = projector.project(image_point)
                self.assertAlmostEqual(expected_world[0], actual[0], places=6)
                self.assertAlmostEqual(expected_world[1], actual[1], places=6)

    def test_ground_projector_rejects_pixel_coordinates_and_singular_matrix(self):
        projector = GroundPlaneProjector((1, 0, 0, 0, 1, 0, 0, 0, 1))

        with self.assertRaisesRegex(ValueError, "normalized"):
            projector.project((170, 810))
        with self.assertRaisesRegex(ValueError, "invertible"):
            GroundPlaneProjector((1, 0, 0, 0, 0, 0, 0, 0, 1))

    def test_camera_config_loader_reads_polygons_and_homography(self):
        payload = {
            "cameras": [
                {
                    "camera_id": "display-1",
                    "store_id": "store-1",
                    "source": "rtsp://camera/display-1",
                    "staff_vest_enabled": False,
                    "entrance_area": {
                        "area_id": "store-entry",
                        "points": [[0, 0], [0.4, 0], [0.4, 0.2], [0, 0.2]],
                    },
                    "greeting_area": {
                        "area_id": "reception-greeting",
                        "points": [[0, 0.2], [0.6, 0.2], [0.6, 0.6], [0, 0.6]],
                    },
                    "interaction_areas": [
                        {
                            "area_id": "hardware-front",
                            "points": [[0, 0], [0.5, 0], [0.5, 1], [0, 1]],
                        }
                    ],
                    "display_areas": [],
                    "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            configs = load_behavior_camera_configs(path)

        self.assertEqual("display-1", configs[0].camera_id)
        self.assertEqual("store-1", configs[0].store_id)
        self.assertFalse(configs[0].staff_vest_enabled)
        self.assertEqual("reception-greeting", configs[0].greeting_area.area_id)
        self.assertEqual("greeting", configs[0].greeting_area.area_type)
        self.assertEqual("hardware-front", configs[0].interaction_areas[0].area_id)
        self.assertIsNotNone(configs[0].ground_projector)

    def test_camera_config_loader_reads_zone_runtime_parameters(self):
        payload = {
            "cameras": [{
                "camera_id": "cam02",
                "store_id": "store-1",
                "source": "csv-replay://cam02",
                "camera_role": "customer_behavior",
                "zone_runtime": {
                    "zone_enter_stable_frames": 2,
                    "zone_exit_stable_frames": 3,
                    "zone_track_lost_seconds": 2.5,
                    "dwell_minimum_seconds": 1.0,
                },
                "interaction_areas": [{
                    "area_id": "display_island",
                    "points": [[0.2, 0.2], [0.5, 0.2], [0.5, 0.9], [0.2, 0.9]],
                }],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_behavior_camera_configs(path)[0].monitor_config

        self.assertEqual(2, config.zone_enter_stable_frames)
        self.assertEqual(3, config.zone_exit_stable_frames)
        self.assertEqual(2.5, config.zone_track_lost_seconds)
        self.assertEqual(1.0, config.dwell_minimum_seconds)

    def test_reception_config_loads_separate_entry_and_provisional_calibration(self):
        payload = {
            "cameras": [{
                "camera_id": "entrance-1",
                "store_id": "store-1",
                "source": "0",
                "camera_role": "reception_entry",
                "staff_vest_enabled": True,
                "entrance": {
                    "outside_stable_frames": 3,
                    "inside_stable_frames": 3,
                    "crossing_timeout_seconds": 3.0,
                    "rearm_outside_frames": 5,
                    "minimum_crossing_displacement": 0.15,
                },
                "entry_trigger": {
                    "area_id": "front-entry-trigger",
                    "points": [
                        [0.06640625, 0.5625],
                        [0.546875, 0.5625],
                        [0.546875, 0.72916667],
                        [0.06640625, 0.72916667],
                    ],
                },
                "greeting_area": {
                    "area_id": "front-greeting-area",
                    "points": [
                        [0.0703125, 0.72916667],
                        [0.5859375, 0.72916667],
                        [0.5859375, 0.96458333],
                        [0.0703125, 0.96458333],
                    ],
                },
                "calibration_status": "provisional",
                "calibration_image_size": [2560, 1440],
                "ground_homography": [
                    7.7008130081, 0.0, -0.5113821138,
                    0.0, 6.0, -3.375,
                    0.0, 0.0, 1.0,
                ],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            camera = load_behavior_camera_configs(path)[0]

        self.assertEqual("front-entry-trigger", camera.entry_trigger.area_id)
        self.assertEqual("front-greeting-area", camera.greeting_area.area_id)
        self.assertEqual(CameraRole.RECEPTION_ENTRY, camera.camera_role)
        self.assertEqual(
            (
                (0.0703125, 0.72916667),
                (0.5859375, 0.72916667),
                (0.5859375, 0.96458333),
                (0.0703125, 0.96458333),
            ),
            camera.greeting_area.points,
        )
        self.assertEqual("provisional", camera.calibration_status)
        self.assertEqual((2560, 1440), camera.calibration_image_size)
        self.assertEqual(3, camera.monitor_config.outside_stable_frames)
        self.assertEqual(3, camera.monitor_config.inside_stable_frames)
        self.assertEqual(3.0, camera.monitor_config.crossing_timeout_seconds)
        self.assertEqual(5, camera.monitor_config.rearm_outside_frames)
        self.assertEqual(0.15, camera.monitor_config.minimum_crossing_displacement)
        self.assertEqual(["A", "B", "C", "D"], [p.label for p in camera.ground_control_points])

        rendered = draw_behavior_area_overlay(
            np.zeros((144, 256, 3), dtype=np.uint8), camera
        )
        for control_point in camera.ground_control_points:
            x = round(control_point.image_point[0] * 256)
            y = round(control_point.image_point[1] * 144)
            self.assertTrue(np.all(rendered[y, x] > 0), control_point.label)
        center_x = round(0.306640625 * 256)
        center_y = round(0.645833335 * 144)
        self.assertTrue(np.all(rendered[center_y, center_x] > 0))

    def test_reception_config_resolves_controlled_demo_greeting_profile(self):
        payload = {
            "greeting_profiles": {
                "production_candidate": {
                    "greeting_staff_travel_metres": 0.40,
                    "greeting_duration_seconds": 1.5,
                    "greeting_distance_metres": 1.2,
                    "minimum_distance_reduction_metres": 0.40,
                    "greeting_area_exit_grace_seconds": 0.5,
                }
            },
            "cameras": [{
                "camera_id": "entrance-1",
                "store_id": "store-1",
                "source": "0",
                "camera_role": "reception_entry",
                "greeting": {
                    "profile": "controlled_demo",
                    "greeting_window_seconds": 30.0,
                    "greeting_staff_travel_metres": 0.25,
                    "greeting_duration_seconds": 1.0,
                    "greeting_distance_metres": 1.2,
                    "minimum_distance_reduction_metres": 0.35,
                    "greeting_area_exit_grace_seconds": 0.8,
                    "greeting_area_enter_stable_frames": 3,
                    "greeting_area_exit_stable_frames": 5,
                },
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_behavior_camera_configs(path)[0].monitor_config

        self.assertEqual("controlled_demo", config.greeting_profile)
        self.assertEqual(30.0, config.greeting_window_seconds)
        self.assertEqual(0.25, config.greeting_staff_travel_metres)
        self.assertEqual(1.0, config.greeting_duration_seconds)
        self.assertEqual(1.2, config.greeting_distance_metres)
        self.assertEqual(0.35, config.minimum_distance_reduction_metres)
        self.assertEqual(0.8, config.greeting_area_exit_grace_seconds)
        self.assertEqual(3, config.greeting_area_enter_stable_frames)
        self.assertEqual(5, config.greeting_area_exit_stable_frames)

    def test_reception_config_resolves_opt_in_demo_relaxed_profile(self):
        payload = {
            "cameras": [{
                "camera_id": "entrance-1",
                "store_id": "store-1",
                "source": "0",
                "camera_role": "reception_entry",
                "greeting": {"profile": "demo_relaxed"},
            }],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            config = load_behavior_camera_configs(path)[0].monitor_config

        self.assertEqual("demo_relaxed", config.greeting_profile)
        self.assertEqual(0.10, config.greeting_staff_travel_metres)
        self.assertEqual(0.05, config.greeting_staff_toward_customer_metres)
        self.assertEqual(0.5, config.greeting_duration_seconds)
        self.assertEqual(2.0, config.greeting_distance_metres)
        self.assertEqual(0.05, config.minimum_distance_reduction_metres)
        self.assertEqual(5, config.greeting_trend_window_samples)
        self.assertEqual(5, config.greeting_trend_minimum_samples)
        self.assertEqual(0.05, config.greeting_trend_minimum_reduction_metres)
        self.assertEqual(0.5, config.greeting_trend_decreasing_ratio)
        self.assertEqual(0.5, config.confirmed_confidence)

    def test_non_reception_camera_rejects_greeting_area(self):
        payload = {
            "cameras": [{
                "camera_id": "display-1",
                "store_id": "store-1",
                "source": "0",
                "camera_role": "customer_behavior",
                "greeting_area": {
                    "area_id": "invalid-greeting",
                    "points": [[0.1, 0.4], [0.7, 0.4], [0.7, 0.8], [0.1, 0.8]],
                },
                "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "reception_entry"):
                load_behavior_camera_configs(path)

    def test_reception_config_rejects_homography_that_misses_control_points(self):
        payload = {
            "cameras": [{
                "camera_id": "entrance-1",
                "store_id": "store-1",
                "source": "0",
                "entry_trigger": {
                    "area_id": "front-entry-trigger",
                    "points": [
                        [0.06640625, 0.5625],
                        [0.546875, 0.5625],
                        [0.546875, 0.72916667],
                        [0.06640625, 0.72916667],
                    ],
                },
                "greeting_area": {
                    "area_id": "front-greeting-area",
                    "points": [[0.08, 0.74], [0.54, 0.74], [0.5, 0.95], [0.1, 0.95]],
                },
                "calibration_status": "provisional",
                "calibration_image_size": [2560, 1440],
                "ground_homography": [1, 0, 0, 0, 1, 0, 0, 0, 1],
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "control point"):
                load_behavior_camera_configs(path)

    def test_area_overlay_draws_entry_and_greeting_without_mutating_frame(self):
        payload = {
            "cameras": [{
                "camera_id": "entrance-1",
                "source": "rtsp://camera/entrance-1",
                "entrance_area": {
                    "area_id": "entry",
                    "points": [[0.1, 0.1], [0.4, 0.1], [0.4, 0.3], [0.1, 0.3]],
                },
                "greeting_area": {
                    "area_id": "greeting",
                    "points": [[0.1, 0.4], [0.7, 0.4], [0.7, 0.8], [0.1, 0.8]],
                },
            }]
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "behavior.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            camera = load_behavior_camera_configs(path)[0]
        frame = np.zeros((100, 200, 3), dtype=np.uint8)

        rendered = draw_behavior_area_overlay(frame, camera)

        self.assertFalse(np.shares_memory(frame, rendered))
        self.assertTrue(np.all(frame == 0))
        self.assertGreater(np.count_nonzero(rendered[:, :, 1]), 0)
        self.assertGreater(np.count_nonzero(rendered[:, :, 0]), 0)


if __name__ == "__main__":
    unittest.main()
