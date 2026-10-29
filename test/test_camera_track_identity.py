from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.camera_track_identity import (
    CacheProjectionInvariantViolation,
    CameraTrackIdentityCache,
    FaceSpatialEvidence,
    SameCameraRecognitionBinder,
    TrackIdentityBinding,
    TrackSnapshot,
    TrackSnapshotBuffer,
)


class CameraTrackIdentityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 5, 6, 0, 0)
        self.binder = SameCameraRecognitionBinder()

    def _track(
        self,
        track_id=1,
        *,
        x1=0.1,
        x2=None,
        y1=0.1,
        y2=0.9,
        role="customer",
    ):
        x2 = x1 + 0.3 if x2 is None else x2
        return TrackSnapshot(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            track_id=track_id,
            track_token=f"store-1:camera-1:runtime-1:{track_id}",
            observed_at=self.now,
            bbox=(x1, y1, x2, y2),
            confidence=0.9,
            role=role,
            stable_frames=3,
            is_frame_clipped=False,
            frame_size=(2560, 1440),
        )

    def test_unique_stable_customer_is_probable_without_spatial_evidence(self):
        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(self._track(),),
        )

        self.assertEqual("probable", decision.status)
        self.assertEqual(self._track().track_token, decision.track_token)

    def test_store_snapshot_buffer_counts_frames_and_prunes_by_ttl(self):
        current = [self.now]
        buffer = TrackSnapshotBuffer(ttl_seconds=5, clock=lambda: current[0])
        first = buffer.publish(self._track())
        current[0] += timedelta(milliseconds=100)
        second = buffer.publish(replace(self._track(), observed_at=current[0]))

        self.assertEqual(1, first.stable_frames)
        self.assertEqual(2, second.stable_frames)
        self.assertEqual((second,), buffer.snapshots(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            occurred_at=current[0],
        ))

        current[0] += timedelta(seconds=6)
        self.assertEqual((), buffer.snapshots(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            occurred_at=current[0],
        ))

    def test_multiple_eligible_customers_are_ambiguous(self):
        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(self._track(1, x1=0.1), self._track(2, x1=0.55)),
        )

        self.assertEqual("ambiguous", decision.status)
        self.assertIsNone(decision.track_token)

    def test_unique_spatial_match_is_confirmed(self):
        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(self._track(1, x1=0.1), self._track(2, x1=0.55)),
            face=FaceSpatialEvidence(
                bbox=(0.15, 0.15, 0.22, 0.25),
                normalized=True,
                quality_status="verified",
            ),
        )

        self.assertEqual("confirmed", decision.status)
        self.assertEqual(self._track(1).track_token, decision.track_token)

    def test_1920x1088_geometry_reports_unverified_eight_pixel_padding(self):
        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(self._track(),),
            face=FaceSpatialEvidence(
                bbox=(0.46875, 0.378676, 0.535417, 0.496324),
                normalized=True,
                quality_status="parsed",
                raw_bbox=(900.0, 412.0, 1028.0, 540.0),
                source_frame_size=(1920, 1088),
                occurred_at=self.now,
            ),
        )

        self.assertEqual("probable", decision.status)
        self.assertEqual("face_spatial_geometry_unverified", decision.reason)
        self.assertEqual(1, len(decision.spatial_diagnostics))
        diagnostic = decision.spatial_diagnostics[0]
        self.assertEqual("vertical_padding_unverified", diagnostic["geometry_status"])
        self.assertEqual(8, diagnostic["candidate_vertical_padding_pixels"])
        self.assertFalse(diagnostic["matched"])

    def test_unique_full_frame_spatial_match_reports_containment_and_confirms(self):
        track = self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9)
        face = FaceSpatialEvidence(
            bbox=(900 / 1920, 412 / 1080, 1028 / 1920, 540 / 1080),
            normalized=True,
            quality_status="parsed",
            raw_bbox=(900.0, 412.0, 1028.0, 540.0),
            source_frame_size=(1920, 1080),
            occurred_at=self.now,
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(track,),
            face=face,
        )

        self.assertEqual("confirmed", decision.status)
        self.assertEqual(track.track_token, decision.track_token)
        diagnostic = decision.spatial_diagnostics[0]
        self.assertEqual(list(track.bbox), diagnostic["person_bbox"])
        self.assertTrue(diagnostic["face_center_inside"])
        self.assertTrue(diagnostic["upper_body_check"])
        self.assertAlmostEqual(1.0, diagnostic["intersection_over_face_area"])
        self.assertTrue(diagnostic["containment"])
        self.assertTrue(diagnostic["matched"])

    def test_face_center_below_upper_body_does_not_match_person_track(self):
        track = self._track(x1=0.4, x2=0.65, y1=0.1, y2=0.9)
        face = FaceSpatialEvidence(
            bbox=(0.48, 0.62, 0.56, 0.72),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1920, 1080),
            occurred_at=self.now,
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(track,),
            face=face,
        )

        self.assertEqual("no_track_match", decision.status)
        self.assertEqual("face_spatial_no_match", decision.reason)
        diagnostic = decision.spatial_diagnostics[0]
        self.assertTrue(diagnostic["face_center_inside"])
        self.assertFalse(diagnostic["upper_body_check"])
        self.assertFalse(diagnostic["matched"])

    def test_face_spatial_evidence_outside_time_window_cannot_confirm(self):
        track = self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9)
        face = FaceSpatialEvidence(
            bbox=(0.47, 0.3, 0.54, 0.4),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1920, 1080),
            occurred_at=self.now + timedelta(seconds=2),
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(track,),
            face=face,
        )

        self.assertEqual("no_track_match", decision.status)
        self.assertEqual("no_eligible_customer_track", decision.reason)
        self.assertEqual((), decision.spatial_diagnostics)

    def test_primary_snapshot_is_nearest_to_face_reference_time(self):
        token_track = self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9)
        tracks = tuple(
            replace(token_track, observed_at=self.now + timedelta(seconds=offset))
            for offset in (-0.30, -0.05, 0.20, 0.45)
        )
        face = FaceSpatialEvidence(
            bbox=(0.47, 0.3, 0.54, 0.4),
            normalized=True,
            quality_status="verified",
            occurred_at=self.now,
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=tracks,
            face=face,
        )

        self.assertEqual(self.now - timedelta(seconds=0.05), decision.candidates[0].observed_at)
        self.assertAlmostEqual(
            0.05, decision.spatial_diagnostics[0]["track_time_delta_seconds"]
        )

    def test_better_support_containment_does_not_replace_primary(self):
        primary = replace(
            self._track(x1=0.49, x2=0.52, y1=0.2, y2=0.9),
            observed_at=self.now - timedelta(seconds=0.05),
        )
        support = replace(
            self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9),
            observed_at=self.now + timedelta(seconds=0.45),
        )
        face = FaceSpatialEvidence(
            bbox=(0.47, 0.3, 0.54, 0.4),
            normalized=True,
            quality_status="verified",
            occurred_at=self.now,
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(support, primary),
            face=face,
        )

        self.assertIs(primary, decision.candidates[0])
        self.assertEqual("no_track_match", decision.status)

    def test_primary_snapshot_exact_tie_prefers_earlier_frame(self):
        base = self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9)
        earlier = replace(base, observed_at=self.now - timedelta(seconds=0.05))
        later = replace(base, observed_at=self.now + timedelta(seconds=0.05))

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(later, earlier),
        )

        self.assertIs(earlier, decision.candidates[0])

    def test_face_spatial_evidence_from_other_camera_cannot_confirm(self):
        track = self._track(x1=0.42, x2=0.62, y1=0.2, y2=0.9)
        face = FaceSpatialEvidence(
            bbox=(0.47, 0.3, 0.54, 0.4),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1920, 1080),
            occurred_at=self.now,
            camera_id="camera-2",
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(track,),
            face=face,
        )

        self.assertEqual("probable", decision.status)
        self.assertEqual("face_spatial_camera_mismatch", decision.reason)
        diagnostic = decision.spatial_diagnostics[0]
        self.assertFalse(diagnostic["camera_match"])
        self.assertFalse(diagnostic["matched"])

    def test_multiple_spatially_matching_person_tracks_remain_ambiguous(self):
        face = FaceSpatialEvidence(
            bbox=(0.48, 0.3, 0.54, 0.4),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1920, 1080),
            occurred_at=self.now,
            camera_id="camera-1",
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(
                self._track(1, x1=0.4, x2=0.65, y1=0.2, y2=0.9),
                self._track(2, x1=0.45, x2=0.7, y1=0.15, y2=0.85),
            ),
            face=face,
        )

        self.assertEqual("ambiguous", decision.status)
        self.assertEqual("multiple_spatial_candidates", decision.reason)
        self.assertEqual(2, sum(item["matched"] for item in decision.spatial_diagnostics))

    def test_face_containment_ratio_not_person_face_iou_controls_matching(self):
        track = self._track(x1=0.45, x2=0.55, y1=0.1, y2=0.9)
        face = FaceSpatialEvidence(
            bbox=(0.4, 0.2, 0.6, 0.3),
            normalized=True,
            quality_status="parsed",
            source_frame_size=(1920, 1080),
            occurred_at=self.now,
            camera_id="camera-1",
        )

        decision = self.binder.evaluate(
            store_id="store-1",
            camera_id="camera-1",
            runtime_id="runtime-1",
            effective_occurred_at=self.now,
            tracks=(track,),
            face=face,
        )

        self.assertEqual("no_track_match", decision.status)
        diagnostic = decision.spatial_diagnostics[0]
        self.assertTrue(diagnostic["face_center_inside"])
        self.assertAlmostEqual(0.5, diagnostic["intersection_over_face_area"])
        self.assertFalse(diagnostic["containment"])
        self.assertFalse(diagnostic["matched"])

    def test_cache_rejects_probable_projection(self):
        current = [self.now]
        cache = CameraTrackIdentityCache(clock=lambda: current[0])
        with self.assertRaisesRegex(ValueError, "only accepts confirmed"):
            cache.bind(self._binding(track_id=1, status="probable"))

    def test_same_binding_projection_is_idempotent_and_refreshes_expiry(self):
        cache = CameraTrackIdentityCache(clock=lambda: self.now)
        confirmed = self._binding(track_id=1, status="confirmed")
        refreshed = replace(
            confirmed, expires_at=confirmed.expires_at + timedelta(seconds=5)
        )

        self.assertEqual("projected", cache.bind(confirmed))
        self.assertEqual("already_projected", cache.bind(refreshed))
        resolved = cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=1,
            track_token="store-1:camera-1:runtime-1:1",
        )

        self.assertEqual("binding-1", resolved["binding_id"])
        self.assertEqual(refreshed.expires_at, resolved["expires_at"])

    def test_active_tracks_are_pruned_only_within_one_camera_runtime(self):
        cache = CameraTrackIdentityCache(clock=lambda: self.now)
        camera_one = self._binding(track_id=1, status="confirmed")
        camera_two = replace(
            self._binding(track_id=2, status="confirmed"),
            camera_id="camera-2",
            track_token="store-1:camera-2:runtime-1:2",
        )
        cache.bind(camera_one)
        cache.bind(camera_two)

        cache.observe_active_track_tokens(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            active_track_tokens={camera_one.track_token},
            observed_at=self.now,
        )

        self.assertEqual("member-1", cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-2",
            runtime_instance_id="runtime-1",
            track_id=2,
            track_token="store-1:camera-2:runtime-1:2",
        )["member_id"])

    def test_different_binding_same_token_fails_closed(self):
        cache = CameraTrackIdentityCache(clock=lambda: self.now)
        first = self._binding(track_id=1, status="confirmed")
        cache.bind(first)

        with self.assertRaises(CacheProjectionInvariantViolation):
            cache.bind(replace(first, binding_id="binding-other"))

        self.assertIsNone(cache.resolve_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_id=1,
            track_token=first.track_token,
        ))

    def test_latest_exact_requires_complete_scope_and_token(self):
        buffer = TrackSnapshotBuffer(clock=lambda: self.now)
        stored = buffer.publish(self._track(track_id=7))

        self.assertEqual(stored, buffer.latest_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-1",
            track_token="store-1:camera-1:runtime-1:7",
        ))
        self.assertIsNone(buffer.latest_exact(
            store_id="store-1",
            camera_id="camera-1",
            runtime_instance_id="runtime-2",
            track_token="store-1:camera-1:runtime-1:7",
        ))

    def _binding(self, *, track_id, status):
        return TrackIdentityBinding(
            store_id="store-1", camera_id="camera-1", runtime_id="runtime-1",
            track_id=track_id, track_token=f"store-1:camera-1:runtime-1:{track_id}",
            member_id="member-1", member_tier="gold", visit_id="visit-1",
            recognition_event_id=f"event-{track_id}", status=status,
            method="spatial" if status == "confirmed" else "temporal",
            confidence=0.9, recognized_at=self.now,
            confirmed_at=self.now if status == "confirmed" else None,
            expires_at=self.now + timedelta(seconds=30),
            binding_id=f"binding-{track_id}",
            evaluation_id=f"evaluation-{track_id}",
            canonical_visit_id="visit-1",
        )


if __name__ == "__main__":
    unittest.main()
