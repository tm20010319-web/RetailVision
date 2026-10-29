from __future__ import annotations

import unittest

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.behavior_vision import (
    PersonRoleResolver,
    PersonTrackDetection,
    VestDetection,
)


def person(
    track_id: int = 7,
    *,
    token: str = "store-1:entrance-1:runtime-1:7",
    bbox=(20.0, 10.0, 120.0, 110.0),
    is_frame_clipped: bool = False,
) -> PersonTrackDetection:
    return PersonTrackDetection(
        track_id=track_id,
        track_token=token,
        bbox=bbox,
        confidence=0.95,
        is_frame_clipped=is_frame_clipped,
    )


def vest(
    bbox=(50.0, 25.0, 90.0, 65.0),
    confidence: float = 0.91,
) -> VestDetection:
    return VestDetection(
        bbox=bbox,
        confidence=confidence,
        class_name="staff_vest",
    )


class PersonRoleResolverTests(unittest.TestCase):
    def test_torso_vest_confirms_staff_after_three_hits(self):
        resolver = PersonRoleResolver()

        roles = [
            resolver.resolve([person()], [vest()])[0]
            for _ in range(3)
        ]

        self.assertEqual(["unknown", "unknown", "staff"], [item.role for item in roles])
        self.assertEqual("staff_vest_yolo", roles[-1].role_source)
        self.assertEqual(3, roles[-1].vest_hits)
        self.assertEqual(5, roles[-1].window_size)
        self.assertEqual(0.91, roles[-1].vest_confidence)

    def test_vest_outside_every_person_does_not_confirm_staff(self):
        resolver = PersonRoleResolver()
        wall_vest = vest(bbox=(150.0, 20.0, 190.0, 70.0))

        role = None
        for _ in range(5):
            role = resolver.resolve([person()], [wall_vest])[0]

        self.assertEqual("customer", role.role)
        self.assertEqual(0, role.vest_hits)

    def test_frame_clipped_person_accepts_lower_visible_torso_vest(self):
        resolver = PersonRoleResolver()
        clipped = person(
            bbox=(20.0, 10.0, 120.0, 110.0),
            is_frame_clipped=True,
        )
        lower_visible_vest = vest(bbox=(50.0, 65.0, 90.0, 105.0))

        role = None
        for _ in range(3):
            role = resolver.resolve([clipped], [lower_visible_vest])[0]

        self.assertEqual("staff", role.role)

    def test_single_false_positive_does_not_confirm_staff(self):
        resolver = PersonRoleResolver()

        resolver.resolve([person()], [vest()])
        roles = [resolver.resolve([person()], [])[0] for _ in range(4)]

        self.assertEqual("customer", roles[-1].role)
        self.assertEqual(1, roles[-1].vest_hits)

    def test_vest_below_threshold_does_not_participate(self):
        resolver = PersonRoleResolver(vest_confidence_threshold=0.68)

        role = None
        for _ in range(5):
            role = resolver.resolve([person()], [vest(confidence=0.67)])[0]

        self.assertEqual("customer", role.role)
        self.assertEqual(0, role.vest_hits)

    def test_staff_role_is_sticky_after_single_missed_detection(self):
        resolver = PersonRoleResolver()
        for _ in range(3):
            resolver.resolve([person()], [vest()])

        role = resolver.resolve([person()], [])[0]

        self.assertEqual("staff", role.role)
        self.assertEqual(3, role.vest_hits)

    def test_staff_survives_eight_missing_frames(self):
        resolver = PersonRoleResolver(maximum_missing_frames=8)
        for _ in range(3):
            resolver.resolve([person()], [vest()])
        for _ in range(8):
            resolver.resolve([], [])

        role = resolver.resolve([person()], [])[0]

        self.assertEqual("staff", role.role)

    def test_track_state_is_cleared_after_missing_limit(self):
        resolver = PersonRoleResolver(maximum_missing_frames=8)
        for _ in range(3):
            resolver.resolve([person()], [vest()])
        for _ in range(9):
            resolver.resolve([], [])

        role = resolver.resolve([person()], [])[0]

        self.assertEqual("unknown", role.role)
        self.assertEqual(0, role.vest_hits)

    def test_reused_numeric_track_id_with_new_token_does_not_inherit_staff(self):
        resolver = PersonRoleResolver()
        for _ in range(3):
            resolver.resolve([person()], [vest()])
        reused = person(token="store-1:entrance-1:runtime-2:7")

        role = resolver.resolve([reused], [])[0]

        self.assertEqual("unknown", role.role)
        self.assertEqual(0, role.vest_hits)

    def test_one_vest_can_confirm_only_one_of_two_people(self):
        resolver = PersonRoleResolver()
        best = person(token="store-1:entrance-1:runtime-1:7")
        weaker = person(
            track_id=8,
            token="store-1:entrance-1:runtime-1:8",
            bbox=(0.0, 0.0, 200.0, 200.0),
        )

        roles = None
        for _ in range(3):
            roles = resolver.resolve([best, weaker], [vest()])

        by_token = {item.track_token: item for item in roles}
        self.assertEqual("staff", by_token[best.track_token].role)
        self.assertNotEqual("staff", by_token[weaker.track_token].role)
        self.assertEqual(0, by_token[weaker.track_token].vest_hits)

    def test_overlapping_candidates_with_small_score_gap_are_uncertain(self):
        resolver = PersonRoleResolver(ambiguity_margin=0.08)
        left = person(token="store-1:entrance-1:runtime-1:7")
        right = person(
            track_id=8,
            token="store-1:entrance-1:runtime-1:8",
            bbox=(25.0, 10.0, 125.0, 110.0),
        )

        roles = resolver.resolve([left, right], [vest()])

        self.assertEqual(["uncertain", "uncertain"], [item.role for item in roles])
        self.assertTrue(all(item.reason == "overlapping_candidates" for item in roles))
        self.assertTrue(all(item.vest_hits == 0 for item in roles))


if __name__ == "__main__":
    unittest.main()
