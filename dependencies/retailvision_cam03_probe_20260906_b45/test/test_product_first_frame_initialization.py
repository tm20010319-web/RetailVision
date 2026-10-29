import copy
import unittest
from datetime import datetime, timezone

import numpy as np

from agent.product_first_frame_initialization import (
    DenseInitializationPolicy,
    DenseProductInitializationError,
    FakeInstancePromptAdapter,
    InitializationFailureCode,
    InitializationFrameRef,
    InstancePromptResult,
    MaskConfirmation,
    TargetSelection,
    build_proposal,
    commit_confirmed,
    frame_sha256,
    map_roi_selection_to_full_frame,
    mask_sha256,
    restore_roi_mask,
)
from agent.sam31_product_tracking import (
    MultiProductTrackRuntime,
    ProductCandidate,
    ProductDisplaySurface,
    ProductTrackStatus,
)


WIDTH = 1280
HEIGHT = 720
KEYS = tuple(f"product_{index}" for index in range(5))
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def rectangle(x1, y1, x2, y2):
    value = np.zeros((HEIGHT, WIDTH), dtype=bool)
    value[y1:y2, x1:x2] = True
    return value


def candidate(candidate_id, bounds):
    mask = rectangle(*bounds)
    ys, xs = np.where(mask)
    return ProductCandidate(
        local_obj_id=candidate_id,
        bbox=tuple(float(value) for value in bounds),
        centroid=(float(np.mean(xs)), float(np.mean(ys))),
        confidence=1.0,
        mask=mask,
    )


class DenseProductInitializationTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        self.surface = ProductDisplaySurface(
            ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
        )
        self.policy = DenseInitializationPolicy(
            approved_product_keys=frozenset(KEYS),
            product_display_surface=self.surface,
            frame_size=(WIDTH, HEIGHT),
            bbox_mask_fill_min=0.25,
            bbox_frame_area_max=0.30,
            component_noise_area_min=4,
            component_significant_ratio_min=0.05,
            pairwise_mask_overlap_max=0.10,
        )
        self.bounds = (
            (100, 300, 180, 400),
            (300, 300, 380, 400),
            (500, 300, 580, 400),
            (700, 300, 780, 400),
            (900, 300, 980, 400),
        )

    def frame_ref(self, *, digest=None, source_pts=9009, session="run-dense"):
        return InitializationFrameRef(
            analysis_run_id=session,
            source_seq=17,
            source_pts=source_pts,
            time_base_num=1,
            time_base_den=90000,
            presentation_seconds=source_pts / 90000,
            frame_sha256=digest or frame_sha256(self.frame),
            frame_size=(WIDTH, HEIGHT),
            roi_xyxy=(0, 0, WIDTH, HEIGHT),
            coordinate_transform_version="roi_to_full_ai_frame_v1",
        )

    def selections(self, count=3, *, order=None, reference_slot=None):
        indexes = tuple(range(count)) if order is None else tuple(order)
        values = []
        for index in indexes:
            x1, y1, x2, y2 = self.bounds[index]
            values.append(TargetSelection(
                product_key=KEYS[index],
                bbox_xyxy=(x1 - 1, y1 - 1, x2 + 1, y2 + 1),
                positive_points=((x1 + 2, y1 + 2),),
                negative_points=((x2 + 2, y1 + 2),),
                catalog_reference_image="human-only.jpg",
                reference_slot=reference_slot,
                initialization_hint="human-only hint",
            ))
        return tuple(values)

    def adapter(self, count=3, *, ids=None, masks=None, sessions=None):
        candidate_ids = tuple(range(101, 101 + count)) if ids is None else tuple(ids)
        result_masks = tuple(rectangle(*self.bounds[index]) for index in range(count)) if masks is None else tuple(masks)
        session_ids = ("one-session",) * count if sessions is None else tuple(sessions)
        return FakeInstancePromptAdapter({
            index + 1: InstancePromptResult(
                candidate_id=candidate_ids[index],
                masks=(result_masks[index],),
                session_id=session_ids[index],
            )
            for index in range(count)
        })

    def proposal(self, count=3, **kwargs):
        return build_proposal(
            self.frame_ref(),
            kwargs.pop("selections", self.selections(count)),
            kwargs.pop("adapter", self.adapter(count)),
            frame=self.frame,
            policy=kwargs.pop("policy", self.policy),
        )

    def runtime(self, count=3):
        return MultiProductTrackRuntime(
            camera_id="cam-dense",
            runtime_instance_id="dpi-01a-test",
            products={key: "smart_toy" for key in KEYS[:count]},
            product_display_surface=self.surface,
            frame_width=WIDTH,
            frame_height=HEIGHT,
        )

    def confirmations(self, proposal, *, rejected_key=None, bad_hash_key=None):
        return tuple(MaskConfirmation(
            product_key=item.product_key,
            candidate_id=item.candidate_id,
            frame_sha256=proposal.frame_ref.frame_sha256,
            mask_sha256=("0" * 64 if item.product_key == bad_hash_key else item.mask_sha256),
            status=("rejected" if item.product_key == rejected_key else "confirmed"),
            confirmed_at=NOW,
        ) for item in proposal.masks)

    def assert_failure(self, proposal, code):
        self.assertEqual("REJECTED", proposal.status)
        self.assertIn(code, proposal.failure_codes)
        self.assertEqual((), proposal.masks)

    def test_dense_three_and_five_products_are_ready_and_use_one_single_frame_session(self):
        for count in (3, 5):
            with self.subTest(count=count):
                adapter = self.adapter(count)
                proposal = self.proposal(count, adapter=adapter)
                self.assertEqual("READY_FOR_CONFIRMATION", proposal.status)
                self.assertEqual(count, len(proposal.masks))
                self.assertEqual(list(range(1, count + 1)), [call["obj_id"] for call in adapter.calls])
                self.assertTrue(all(call["frame_index"] == 0 for call in adapter.calls))

    def test_selection_and_candidate_id_order_are_preserved_without_key_hardcoding(self):
        selections = self.selections(3, order=(2, 0, 1))
        adapter = self.adapter(3, ids=(901, 305, 712), masks=(
            rectangle(*self.bounds[2]), rectangle(*self.bounds[0]), rectangle(*self.bounds[1]),
        ))
        proposal = self.proposal(3, selections=selections, adapter=adapter)
        self.assertEqual((KEYS[2], KEYS[0], KEYS[1]), proposal.selected_product_keys)
        self.assertEqual((901, 305, 712), tuple(item.candidate_id for item in proposal.masks))

    def test_key_and_count_policy_fail_closed(self):
        duplicate = list(self.selections(3))
        duplicate[2] = TargetSelection(KEYS[0], duplicate[2].bbox_xyxy, duplicate[2].positive_points)
        self.assert_failure(self.proposal(3, selections=duplicate), InitializationFailureCode.PRODUCT_KEY_DUPLICATE)
        invalid = list(self.selections(3))
        invalid[2] = TargetSelection("not-approved", invalid[2].bbox_xyxy, invalid[2].positive_points)
        self.assert_failure(self.proposal(3, selections=invalid), InitializationFailureCode.PRODUCT_KEY_INVALID)
        self.assert_failure(self.proposal(3, selections=self.selections(3)[:2]), InitializationFailureCode.TARGET_COUNT_OUT_OF_POLICY)

    def test_prompt_geometry_failures_are_objective(self):
        invalid = list(self.selections(3))
        invalid[0] = TargetSelection(KEYS[0], (-1, 0, 10, 10), ((2, 2),))
        self.assert_failure(self.proposal(3, selections=invalid), InitializationFailureCode.BBOX_OUT_OF_BOUNDS)

        loose = list(self.selections(3))
        loose[0] = TargetSelection(KEYS[0], (0, 0, 1000, 700), ((102, 302),))
        self.assert_failure(self.proposal(3, selections=loose), InitializationFailureCode.BBOX_TOO_LOOSE)

    def test_positive_negative_empty_and_ambiguous_masks_fail_closed(self):
        masks = list(rectangle(*value) for value in self.bounds[:3])
        masks[0][302, 102] = False
        self.assert_failure(self.proposal(3, adapter=self.adapter(3, masks=masks)), InitializationFailureCode.POSITIVE_POINT_MISS)

        masks = list(rectangle(*value) for value in self.bounds[:3])
        masks[0][302, 182] = True
        self.assert_failure(self.proposal(3, adapter=self.adapter(3, masks=masks)), InitializationFailureCode.NEGATIVE_POINT_HIT)

        results = self.adapter(3).results
        results[1] = InstancePromptResult(101, (), "one-session")
        self.assert_failure(self.proposal(3, adapter=FakeInstancePromptAdapter(results)), InitializationFailureCode.MASK_EMPTY)

        results = self.adapter(3).results
        results[1] = InstancePromptResult(101, (rectangle(*self.bounds[0]), rectangle(*self.bounds[0])), "one-session")
        self.assert_failure(self.proposal(3, adapter=FakeInstancePromptAdapter(results)), InitializationFailureCode.CANDIDATE_AMBIGUOUS)

    def test_candidate_reuse_and_mixed_sessions_fail_closed(self):
        self.assert_failure(self.proposal(3, adapter=self.adapter(3, ids=(101, 101, 103))), InitializationFailureCode.CANDIDATE_REUSED)
        self.assert_failure(self.proposal(3, adapter=self.adapter(3, sessions=("a", "b", "a"))), InitializationFailureCode.FRAME_MISMATCH)

    def test_frame_digest_and_coordinate_space_bind_the_batch(self):
        wrong = self.frame_ref(digest="0" * 64)
        proposal = build_proposal(wrong, self.selections(3), self.adapter(3), frame=self.frame, policy=self.policy)
        self.assert_failure(proposal, InitializationFailureCode.FRAME_MISMATCH)
        resized = self.frame[:-1]
        proposal = build_proposal(self.frame_ref(), self.selections(3), self.adapter(3), frame=resized, policy=self.policy)
        self.assert_failure(proposal, InitializationFailureCode.COORDINATE_SPACE_MISMATCH)

    def test_significant_components_fail_but_one_pixel_noise_is_diagnostic_only(self):
        multi = list(rectangle(*value) for value in self.bounds[:3])
        multi[0][500:540, 100:140] = True
        self.assert_failure(self.proposal(3, adapter=self.adapter(3, masks=multi)), InitializationFailureCode.MASK_MULTI_INSTANCE)

        noisy = list(rectangle(*value) for value in self.bounds[:3])
        noisy[0][500, 100] = True
        proposal = self.proposal(3, adapter=self.adapter(3, masks=noisy))
        self.assertEqual("READY_FOR_CONFIRMATION", proposal.status)
        diagnostics = proposal.masks[0].diagnostics["component_diagnostics"]
        self.assertEqual(1, diagnostics["ignored_noise_component_count"])

    def test_pairwise_mask_conflict_and_outside_surface_fail_closed(self):
        overlapping = [rectangle(*value) for value in self.bounds[:3]]
        overlapping[1] = overlapping[0].copy()
        selection = list(self.selections(3))
        selection[1] = TargetSelection(KEYS[1], selection[0].bbox_xyxy, selection[0].positive_points)
        self.assert_failure(self.proposal(3, selections=selection, adapter=self.adapter(3, masks=overlapping)), InitializationFailureCode.PAIRWISE_MASK_CONFLICT)

        limited_surface = ProductDisplaySurface(((0.0, 0.0), (0.20, 0.0), (0.20, 1.0), (0.0, 1.0)))
        policy = DenseInitializationPolicy(frozenset(KEYS), limited_surface, frame_size=(WIDTH, HEIGHT))
        self.assert_failure(self.proposal(3, policy=policy), InitializationFailureCode.MASK_OUTSIDE_SURFACE)

    def test_unconfirmed_or_hash_mismatch_never_mutates_runtime(self):
        proposal = self.proposal(3)
        for confirmations, code in (
            (self.confirmations(proposal)[:-1], InitializationFailureCode.MASK_NOT_CONFIRMED),
            (self.confirmations(proposal, rejected_key=KEYS[0]), InitializationFailureCode.MASK_NOT_CONFIRMED),
            (self.confirmations(proposal, bad_hash_key=KEYS[0]), InitializationFailureCode.MASK_HASH_MISMATCH),
        ):
            runtime = self.runtime(3)
            with self.subTest(code=code), self.assertRaises(DenseProductInitializationError) as raised:
                commit_confirmed(proposal, confirmations, runtime)
            self.assertIn(code, raised.exception.codes)
            self.assertEqual({}, runtime._tracks)
            self.assertEqual({key: 1 for key in KEYS[:3]}, runtime._next_segment)

    def test_mask_mutation_after_proposal_invalidates_confirmation(self):
        proposal = self.proposal(3)
        confirmations = self.confirmations(proposal)
        proposal.masks[0].mask[0, 0] = True
        runtime = self.runtime(3)
        with self.assertRaises(DenseProductInitializationError) as raised:
            commit_confirmed(proposal, confirmations, runtime)
        self.assertIn(InitializationFailureCode.MASK_HASH_MISMATCH, raised.exception.codes)
        self.assertEqual({}, runtime._tracks)

    def test_active_and_missing_pending_reinitialization_are_rejected(self):
        proposal = self.proposal(3)
        confirmations = self.confirmations(proposal)
        runtime = self.runtime(3)
        commit_confirmed(proposal, confirmations, runtime)
        with self.assertRaises(DenseProductInitializationError) as active:
            commit_confirmed(proposal, confirmations, runtime)
        self.assertIn(InitializationFailureCode.TRACK_NOT_LOST, active.exception.codes)
        runtime.process_window(candidates=(), observed_at=NOW, source_seq=20, window_no=1)
        self.assertEqual(ProductTrackStatus.MISSING_PENDING, runtime.status(KEYS[0]))
        with self.assertRaises(DenseProductInitializationError) as pending:
            commit_confirmed(proposal, confirmations, runtime)
        self.assertIn(InitializationFailureCode.TRACK_NOT_LOST, pending.exception.codes)

    def test_lost_track_gets_a_new_segment_only_after_formal_lost(self):
        first = self.proposal(3)
        runtime = self.runtime(3)
        original = commit_confirmed(first, self.confirmations(first), runtime)
        for window_no in range(1, 4):
            runtime.process_window(candidates=(), observed_at=NOW, source_seq=20 + window_no, window_no=window_no)
        self.assertTrue(all(runtime.status(key) is ProductTrackStatus.LOST for key in KEYS[:3]))

        second = self.proposal(3, adapter=self.adapter(3, ids=(201, 202, 203)))
        replacement = commit_confirmed(second, self.confirmations(second), runtime)
        self.assertTrue(all(item.product_track_id == 2 for item in replacement.tracks))
        self.assertTrue(all(item.product_track_token != original.tracks[index].product_track_token for index, item in enumerate(replacement.tracks)))

    def test_runtime_batch_is_atomic_when_one_requested_track_is_active(self):
        runtime = self.runtime(3)
        runtime.initialize_assignments(
            assignments={KEYS[1]: candidate(22, self.bounds[1])},
            observed_at=NOW, source_seq=1,
        )
        tracks_before = copy.deepcopy(runtime._tracks)
        segments_before = dict(runtime._next_segment)
        with self.assertRaisesRegex(ValueError, "parallel Product Track"):
            runtime.initialize_assignments(
                assignments={
                    KEYS[0]: candidate(31, self.bounds[0]),
                    KEYS[1]: candidate(32, self.bounds[1]),
                },
                observed_at=NOW, source_seq=2,
            )
        self.assertEqual(segments_before, runtime._next_segment)
        self.assertEqual(set(tracks_before), set(runtime._tracks))
        self.assertNotIn(KEYS[0], runtime._tracks)
        self.assertEqual(tracks_before[KEYS[1]]["token"], runtime._tracks[KEYS[1]]["token"])

    def test_roi_coordinates_and_mask_are_restored_to_full_frame(self):
        selection = TargetSelection("product_0", (2, 3, 12, 13), ((5, 6),), ((15, 8),))
        mapped = map_roi_selection_to_full_frame(selection, roi_xyxy=(30, 40, 80, 90), frame_size=(WIDTH, HEIGHT))
        self.assertEqual((32, 43, 42, 53), mapped.bbox_xyxy)
        self.assertEqual(((35, 46),), mapped.positive_points)
        roi_mask = np.ones((50, 50), dtype=bool)
        full = restore_roi_mask(roi_mask, roi_xyxy=(30, 40, 80, 90), frame_size=(WIDTH, HEIGHT))
        self.assertEqual((HEIGHT, WIDTH), full.shape)
        self.assertEqual(2500, int(np.count_nonzero(full)))

    def test_exact_source_pts_timebase_is_authoritative_even_when_presentation_is_display_only(self):
        ref = self.frame_ref(source_pts=-9009)
        self.assertAlmostEqual(-9009 / 90000, ref.authoritative_seconds)
        self.assertEqual(-9009, ref.to_dict()["source_pts"])
        self.assertEqual({"num": 1, "den": 90000}, ref.to_dict()["time_base"])

    def test_reference_slot_does_not_change_runtime_track_identity(self):
        proposals = [self.proposal(3, selections=self.selections(3, reference_slot=value)) for value in ("left", "right")]
        tokens = []
        for proposal in proposals:
            result = commit_confirmed(proposal, self.confirmations(proposal), self.runtime(3))
            tokens.append(tuple(item.product_track_token for item in result.tracks))
        self.assertEqual(tokens[0], tokens[1])

    def test_rolling_windows_keep_tokens_without_manual_reannotation(self):
        proposal = self.proposal(3)
        runtime = self.runtime(3)
        initialized = commit_confirmed(proposal, self.confirmations(proposal), runtime)
        expected = {item.product_key: item.product_track_token for item in initialized.tracks}
        for window_no in range(1, 5):
            results = runtime.process_window(
                candidates=tuple(candidate(500 + window_no * 10 + index, bounds) for index, bounds in enumerate(self.bounds[:3])),
                observed_at=NOW, source_seq=20 + window_no, window_no=window_no,
            )
            self.assertEqual(expected, {item.product_key: item.product_track_token for item in results})

    def test_mask_hash_is_binary_and_deterministic(self):
        first = rectangle(*self.bounds[0])
        second = first.astype(np.uint8) * 255
        self.assertEqual(mask_sha256(first), mask_sha256(second))


if __name__ == "__main__":
    unittest.main()
