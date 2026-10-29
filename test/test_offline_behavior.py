from __future__ import annotations

import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from agent.offline_behavior import (  # noqa: E402
    CustomerOrdinalObservation,
    MemberProfileEnrichment,
    OfflineBehaviorCanonicalizerV1,
    OfflineBehaviorIdentityProjector,
    OfflineMemberProfileEnricher,
    OfflineDisplayIdentity,
    OfflinePersonDisplayIdentityResolver,
    OfflineRunCustomerOrdinalRegistry,
    PreparedOfflineBehaviorPublication,
    Stage9BehaviorFactAdapter,
)
from agent.offline_identity import OfflineTrackIdentityAssociation  # noqa: E402


class OfflineBehaviorContractTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 8, 17, 2, 0, 0)
        self.adapter = Stage9BehaviorFactAdapter()

    def test_pickup_only_does_not_fabricate_holding_or_putdown(self):
        result = self._normalize((self._raw("PICK_UP", self.start),))

        self.assertEqual(["merchandise_pick_up"], [item.event_type for item in result.facts])
        self.assertEqual((self.start, self.start), (result.facts[0].started_at, result.facts[0].ended_at))

    def test_complete_session_builds_three_normalized_facts(self):
        end = self.start + timedelta(seconds=4)
        result = self._normalize((
            self._raw("PICK_UP", self.start),
            self._raw("HOLDING", self.start, holding_duration_seconds=0.0),
            self._raw("PUT_DOWN", end),
        ))

        self.assertEqual(
            ["merchandise_pick_up", "merchandise_holding", "merchandise_put_down"],
            [item.event_type for item in result.facts],
        )
        holding = result.facts[1]
        self.assertEqual((self.start, end), (holding.started_at, holding.ended_at))
        self.assertEqual(4.0, holding.holding_duration_seconds)
        self.assertNotEqual(0.0, holding.holding_duration_seconds)

    def test_degraded_session_uses_relative_media_time_and_null_absolute_time(self):
        result = self.adapter.normalize(
            analysis_job_id="job",
            analysis_run_id="run",
            source_video_id="video",
            raw_events=(
                self._raw("PICK_UP", datetime(2000, 1, 1), source_pts_seconds=7.6),
                self._raw("HOLDING", datetime(2000, 1, 1), source_pts_seconds=7.6),
                self._raw("PUT_DOWN", datetime(2000, 1, 1), source_pts_seconds=16.4),
            ),
            absolute_event_time_status="unavailable",
            behavior_time_quality="degraded",
        )

        self.assertEqual(
            [
                (None, None, 7.6, 7.6),
                (None, None, 7.6, 16.4),
                (None, None, 16.4, 16.4),
            ],
            [
                (
                    fact.started_at,
                    fact.ended_at,
                    fact.relative_started_at_seconds,
                    fact.relative_ended_at_seconds,
                )
                for fact in result.facts
            ],
        )
        self.assertEqual(8.8, result.facts[1].holding_duration_seconds)

    def test_degraded_normalized_facts_do_not_serialize_a_synthetic_epoch(self):
        result = self.adapter.normalize(
            analysis_job_id="job",
            analysis_run_id="run",
            source_video_id="video",
            raw_events=(
                self._raw(
                    "PICK_UP", datetime(1970, 1, 1),
                    source_pts_seconds=7.6, event_id="raw-pickup",
                ),
            ),
            absolute_event_time_status="unavailable",
            behavior_time_quality="degraded",
        )

        serialized = json.dumps([asdict(fact) for fact in result.facts], default=str)
        self.assertNotIn("1970-01-01", serialized)
        self.assertNotIn("2000-01-01", serialized)

    def test_authoritative_session_keeps_real_utc_naive_event_time(self):
        video_started_at = datetime(2026, 8, 17, 2, 0, 0)
        result = self.adapter.normalize(
            analysis_job_id="job",
            analysis_run_id="run",
            source_video_id="video",
            raw_events=(
                self._raw(
                    "PICK_UP",
                    video_started_at + timedelta(seconds=7.6),
                    source_pts_seconds=7.6,
                ),
            ),
            absolute_event_time_status="available",
            behavior_time_quality="authoritative",
        )

        self.assertEqual(
            (video_started_at + timedelta(seconds=7.6),) * 2,
            (result.facts[0].started_at, result.facts[0].ended_at),
        )

    def test_inconsistent_actor_cannot_close_holding_interval(self):
        result = self._normalize((
            self._raw("PICK_UP", self.start),
            self._raw(
                "PUT_DOWN", self.start + timedelta(seconds=2),
                person_track_token="store:cam:run:other",
            ),
        ))

        self.assertEqual(["merchandise_pick_up"], [item.event_type for item in result.facts])
        self.assertIn("session_actor_mismatch", {item.reason for item in result.diagnostics})

    def test_confirmed_only_identity_crosses_business_fact_boundary(self):
        fact = self._normalize((self._raw("PICK_UP", self.start),)).facts[0]
        probable = self._association("probable", member_id=None, candidate="member-a")
        confirmed = self._association("confirmed", member_id="member-a", candidate=None)
        projector = OfflineBehaviorIdentityProjector()
        enrichment = MemberProfileEnrichment(
            status="unavailable", member_name=None, member_tier=None,
            reason="member_service_timeout",
        )

        probable_event = projector.project(
            fact=fact, association=probable, enrichment=enrichment,
            artifact_manifest_hash="v1:sha256:artifact",
            association_record_ref="track:store:cam:run:23",
            store_id="store",
        )
        confirmed_event = projector.project(
            fact=fact, association=confirmed, enrichment=enrichment,
            artifact_manifest_hash="v1:sha256:artifact",
            association_record_ref="track:store:cam:run:23",
            store_id="store",
        )

        self.assertIsNone(probable_event.member_id)
        self.assertNotIn("candidate_member_id", str(probable_event.details))
        self.assertEqual("member-a", confirmed_event.member_id)
        self.assertIsNone(confirmed_event.member_tier)
        self.assertEqual("unavailable", confirmed_event.details["member_enrichment_status"])

    def test_canonical_manifest_is_order_independent_and_rejects_duplicate_keys(self):
        facts = self._normalize((
            self._raw("PICK_UP", self.start),
            self._raw("PUT_DOWN", self.start + timedelta(seconds=2)),
        )).facts
        projector = OfflineBehaviorIdentityProjector()
        association = self._association("unmatched", member_id=None, candidate=None)
        events = tuple(
            projector.project(
                fact=fact, association=association, enrichment=None,
                artifact_manifest_hash="v1:sha256:artifact",
                association_record_ref="track:store:cam:run:23",
                store_id="store",
            )
            for fact in facts
        )

        first = PreparedOfflineBehaviorPublication.create(
            analysis_job_id="job", analysis_run_id="run", source_video_id="video",
            events=events,
        )
        second = PreparedOfflineBehaviorPublication.create(
            analysis_job_id="job", analysis_run_id="run", source_video_id="video",
            events=reversed(events),
        )

        self.assertEqual(first.event_manifest_hash, second.event_manifest_hash)
        self.assertEqual(first.canonical_manifest_bytes, second.canonical_manifest_bytes)
        with self.assertRaisesRegex(ValueError, "duplicate_event_key"):
            PreparedOfflineBehaviorPublication.create(
                analysis_job_id="job", analysis_run_id="run", source_video_id="video",
                events=(events[0], replace(events[0], confidence=0.1)),
            )

    def test_event_key_ignores_enrichment_and_processing_time(self):
        fact = self._normalize((self._raw("PICK_UP", self.start),)).facts[0]
        canonicalizer = OfflineBehaviorCanonicalizerV1()

        self.assertEqual(canonicalizer.event_key(fact), canonicalizer.event_key(replace(fact, product_name="New display name")))

    def test_overlay_uses_final_status_and_run_local_ordinal(self):
        observations = (
            CustomerOrdinalObservation("run", "track-b", self.start, 2, 20),
            CustomerOrdinalObservation("run", "track-a", self.start, 1, 10),
        )
        registry = OfflineRunCustomerOrdinalRegistry.build(
            analysis_run_id="run", observations=observations, authoritative_time=True
        )
        resolver = OfflinePersonDisplayIdentityResolver()

        self.assertEqual(1, registry.ordinal_for("track-a"))
        probable = resolver.resolve_label(OfflineDisplayIdentity(
            analysis_run_id="run", track_token="track-a", customer_ordinal=1,
            identity_status="probable", member_id=None, member_name=None,
            artifact_only=False,
        ))
        confirmed = resolver.resolve_label(OfflineDisplayIdentity(
            analysis_run_id="run", track_token="track-b", customer_ordinal=2,
            identity_status="confirmed", member_id="member-a", member_name=None,
            artifact_only=False,
        ))

        self.assertEqual("顾客1 · 疑似会员", probable)
        self.assertEqual("会员 member-a", confirmed)

    def test_member_profile_failure_neither_downgrades_confirmed_nor_upgrades_probable(self):
        enricher = OfflineMemberProfileEnricher()

        confirmed = enricher.enrich(
            identity_status="confirmed", member_id="member-a", member_principal_proven=True,
            member_reader=lambda _: (_ for _ in ()).throw(TimeoutError()),
        )
        probable = enricher.enrich(
            identity_status="probable", member_id="member-a", member_principal_proven=True,
            member_reader=lambda _: {"name": "Name", "tier": "VIP"},
        )

        self.assertEqual(("unavailable", None, None), (confirmed.status, confirmed.member_name, confirmed.member_tier))
        self.assertEqual(("unavailable", "identity_not_confirmed"), (probable.status, probable.reason))

    def _normalize(self, events):
        return self.adapter.normalize(
            analysis_job_id="job", analysis_run_id="run", source_video_id="video",
            raw_events=events,
        )

    def _association(self, status, *, member_id, candidate):
        return OfflineTrackIdentityAssociation(
            analysis_run_id="run", track_token="store:cam:run:23", status=status,
            member_id=member_id, candidate_member_id=candidate,
            valid_from=self.start, valid_to=self.start + timedelta(seconds=10),
            evidence_ids=(), method="same_camera", confidence=0.9,
        )

    def _raw(self, event_type, occurred_at, **overrides):
        values = dict(
            event_id=f"raw-{event_type.lower()}-{occurred_at.isoformat()}",
            event_type=event_type,
            observed_at=occurred_at,
            source_seq=1,
            camera_id="cam",
            product_key="sku-a",
            product_name="Product A",
            product_track_token="product:cam:run:1",
            person_track_id=23,
            person_track_token="store:cam:run:23",
            interaction_session_id="session-1",
            confidence=0.9,
            holding_duration_seconds=None,
        )
        values.update(overrides)
        return SimpleNamespace(**values)


if __name__ == "__main__":
    unittest.main()
