import unittest
from types import SimpleNamespace

import numpy as np

from agent.behavior.product_first_frame_initialization import InitializationFailureCode
from agent.behavior.sam31_real_adapter import Sam31InstancePromptAdapter


class FakePredictor:
    def __init__(self, *, bbox_ids=(7,), point_ids=(7,)):
        self.calls = []
        self.bbox_ids = bbox_ids
        self.point_ids = point_ids

    def handle_request(self, request):
        self.calls.append(request)
        if request["type"] == "close_session":
            return {"closed": True}
        if request.get("bounding_boxes") is not None:
            ids = self.bbox_ids
        else:
            ids = self.point_ids
        mask = np.zeros((720, 1280), dtype=bool)
        mask[300:400, 500:600] = True
        return {
            "frame_index": 0,
            "outputs": {
                "out_obj_ids": np.asarray(ids, dtype=np.int64),
                "out_binary_masks": np.asarray([mask for _ in ids]),
                "out_probs": np.asarray([0.91 for _ in ids], dtype=np.float32),
            },
        }


class ReusingBoxIdPredictor(FakePredictor):
    """Model the real multiplex bbox path: every bbox is backend id zero."""

    def handle_request(self, request):
        if request.get("bounding_boxes") is not None:
            self.bbox_ids = (0,)
        elif request.get("obj_id") is not None:
            requested = int(request["obj_id"])
            self.point_ids = (requested,) if requested == 0 else (0, requested)
        return super().handle_request(request)


class Sam31RealAdapterTests(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    def test_bbox_then_points_are_sent_in_one_session_with_normalized_coordinates(self):
        predictor = FakePredictor()
        adapter = Sam31InstancePromptAdapter(predictor=predictor, session_id="session-1")
        result = adapter.add_instance_prompt(
            frame=self.frame,
            frame_index=0,
            bbox=(500, 300, 600, 400),
            points=((550, 350), (610, 350)),
            point_labels=(1, 0),
            obj_id=1,
        )
        self.assertEqual(7, result.candidate_id)
        self.assertEqual((720, 1280), result.masks[0].shape)
        self.assertEqual(10000, int(np.count_nonzero(result.masks[0])))
        self.assertEqual(["bbox", "points"], result.diagnostics["call_sequence"])
        self.assertEqual("session-1", predictor.calls[0]["session_id"])
        self.assertIn("bounding_boxes", predictor.calls[0])
        self.assertNotIn("points", predictor.calls[0])
        self.assertEqual([[0.390625, 0.4166666666666667, 0.078125, 0.1388888888888889]], predictor.calls[0]["bounding_boxes"])
        self.assertEqual([[550 / 1280, 350 / 720], [610 / 1280, 350 / 720]], predictor.calls[1]["points"])
        self.assertEqual([1, 0], predictor.calls[1]["point_labels"])
        self.assertEqual(7, predictor.calls[1]["obj_id"])

    def test_multiple_bbox_candidates_fail_closed_before_point_refinement(self):
        predictor = FakePredictor(bbox_ids=(7, 8))
        adapter = Sam31InstancePromptAdapter(predictor=predictor, session_id="session-ambiguous")
        result = adapter.add_instance_prompt(
            frame=self.frame,
            frame_index=0,
            bbox=(500, 300, 600, 400),
            points=((550, 350),),
            point_labels=(1,),
            obj_id=1,
        )
        self.assertEqual((), result.masks)
        self.assertEqual(InitializationFailureCode.CANDIDATE_AMBIGUOUS, result.diagnostics["failure_code"])
        self.assertEqual(1, len(predictor.calls))

    def test_invalid_prompt_does_not_call_predictor_and_close_is_reliable(self):
        predictor = FakePredictor()
        adapter = Sam31InstancePromptAdapter(predictor=predictor, session_id="session-close")
        result = adapter.add_instance_prompt(
            frame=self.frame,
            frame_index=1,
            bbox=(500, 300, 600, 400),
            points=((550, 350),),
            point_labels=(1,),
            obj_id=1,
        )
        self.assertEqual((), result.masks)
        self.assertEqual(0, len(predictor.calls))
        adapter.close()
        adapter.close()
        self.assertEqual(1, len(predictor.calls))
        self.assertEqual("close_session", predictor.calls[0]["type"])

    def test_repeated_backend_bbox_id_uses_unique_requested_point_id(self):
        predictor = ReusingBoxIdPredictor()
        adapter = Sam31InstancePromptAdapter(predictor=predictor, session_id="session-reusing-box-id")
        first = adapter.add_instance_prompt(
            frame=self.frame,
            frame_index=0,
            bbox=(500, 300, 600, 400),
            points=((550, 350),),
            point_labels=(1,),
            obj_id=1,
        )
        second = adapter.add_instance_prompt(
            frame=self.frame,
            frame_index=0,
            bbox=(700, 300, 800, 400),
            points=((750, 350),),
            point_labels=(1,),
            obj_id=2,
        )
        self.assertEqual(0, first.candidate_id)
        self.assertTrue(first.masks)
        self.assertEqual(2, second.candidate_id)
        self.assertTrue(second.diagnostics["backend_obj_id_reused"])
        self.assertEqual(2, predictor.calls[3]["obj_id"])


if __name__ == "__main__":
    unittest.main()
