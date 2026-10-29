from __future__ import annotations

import unittest
import threading
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from _test_bootstrap import ensure_project_root_on_path

ensure_project_root_on_path()

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.db import configure_sqlite_foreign_keys
from agent.schema_evolution import (
    EntryDomainSchemaNotReadyError,
    evolve_schema,
    require_entry_schema,
)
from agent.customer_entry_visit import (
    CustomerEntryConflict,
    CustomerEntryObservation,
    CustomerEntryVisit,
    InvalidCustomerEntry,
    CustomerVisitIdentityConflict,
    CustomerVisitIdentityError,
)
from agent.identity_runtime_lease import RuntimeLeaseUnavailable
from agent.models import (
    CustomerEntryConfirmedEvent,
    IdentityEvidence,
    Member,
    Visit,
    VisitEntryEvidence,
)


class CustomerEntryVisitTests(unittest.TestCase):
    def setUp(self):
        self.engine = configure_sqlite_foreign_keys(create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self.service = CustomerEntryVisit(self.sessions)
        self.now = datetime(2026, 7, 28, 12, 0, 0)
        self.lease_validator = lambda _db, _entry: None

    def tearDown(self):
        self.engine.dispose()

    def test_new_mode_persists_only_anonymous_entry_fact_and_evidence(self):
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
        )

        visit = service.confirm_entry(self._entry("entry-new-mode"))

        db = self.sessions()
        try:
            stored = db.get(Visit, visit.id)
            self.assertEqual("store-1", stored.store_id)
            self.assertIsNone(stored.member_id)
            self.assertEqual(1, db.query(VisitEntryEvidence).count())
        finally:
            db.close()
        with self.assertRaises(CustomerVisitIdentityError):
            service.attach_member_identity(visit.id, "no-evidence")

    def test_new_mode_requires_runtime_lease_validator(self):
        with self.assertRaisesRegex(ValueError, "runtime_lease_validator"):
            CustomerEntryVisit(self.sessions, identity_association_mode="new")

    def test_new_mode_confirmed_track_reuses_member_visit_without_arrival_side_effect(self):
        db = self.sessions()
        db.add(Member(id="member-confirmed", name="Confirmed"))
        db.flush()
        member_visit = Visit(
            member_id="member-confirmed", store_id="store-1",
            entry_time=self.now - timedelta(minutes=5), last_seen_at=self.now - timedelta(seconds=5),
            is_member=True,
        )
        db.add(member_visit)
        db.commit()
        visit_id = member_visit.id
        db.close()
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
            track_identity_resolver=lambda entry: {
                "identity_status": "confirmed", "shadow": False,
                "track_token": entry.track_token, "member_id": "member-confirmed",
                "visit_id": visit_id,
            },
        )

        result = service.confirm_entry(self._entry("entry-confirmed-track"))

        db = self.sessions()
        try:
            self.assertEqual(visit_id, result.visit_id)
            self.assertEqual(1, db.query(Visit).count())
            evidence = db.query(VisitEntryEvidence).one()
            self.assertEqual(visit_id, evidence.visit_id)
            self.assertEqual("initial", evidence.evidence_role)
            self.assertEqual(0, db.get(Member, "member-confirmed").visit_count)
            self.assertEqual(self.now, db.get(Visit, visit_id).last_seen_at)
        finally:
            db.close()

    def test_new_mode_confirmed_cache_member_mismatch_fails_closed(self):
        db = self.sessions()
        db.add(Member(id="member-one", name="One"))
        db.add(Member(id="member-two", name="Two"))
        db.flush()
        visit = Visit(
            member_id="member-one",
            store_id="store-1",
            entry_time=self.now - timedelta(minutes=1),
            last_seen_at=self.now - timedelta(seconds=1),
            is_member=True,
        )
        db.add(visit)
        db.commit()
        visit_id = visit.id
        db.close()
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
            track_identity_resolver=lambda entry: {
                "identity_status": "confirmed",
                "shadow": False,
                "track_token": entry.track_token,
                "member_id": "member-two",
                "visit_id": visit_id,
            },
        )

        with self.assertRaisesRegex(
            CustomerEntryConflict, "confirmed_cache_visit_provenance_conflict"
        ):
            service.confirm_entry(self._entry("entry-member-mismatch"))

        db = self.sessions()
        try:
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(0, db.query(VisitEntryEvidence).count())
        finally:
            db.close()

    def test_new_mode_replay_revalidates_confirmed_cache_provenance(self):
        entry = self._entry("entry-replay-provenance")
        anonymous = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
        ).confirm_entry(entry)
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
            track_identity_resolver=lambda value: {
                "identity_status": "confirmed",
                "track_token": value.track_token,
                "member_id": "member-confirmed",
                "visit_id": anonymous.visit_id,
            },
        )

        with self.assertRaisesRegex(
            CustomerEntryConflict, "confirmed_cache_visit_provenance_conflict"
        ):
            service.confirm_entry(entry)

        with self.sessions() as db:
            stored = db.get(Visit, anonymous.visit_id)
            self.assertIsNone(stored.member_id)
            self.assertEqual(1, db.query(VisitEntryEvidence).count())

    def test_new_mode_confirmed_cache_resolves_superseded_visit_to_canonical(self):
        with self.sessions() as db:
            db.add(Member(id="member-canonical", name="Canonical"))
            canonical = Visit(
                id="visit-canonical",
                member_id="member-canonical",
                store_id="store-1",
                entry_time=self.now - timedelta(minutes=1),
                last_seen_at=self.now - timedelta(seconds=1),
                is_member=True,
            )
            superseded = Visit(
                id="visit-superseded",
                member_id="member-canonical",
                store_id="store-1",
                entry_time=self.now - timedelta(minutes=2),
                last_seen_at=self.now - timedelta(minutes=1),
                is_member=True,
                superseded_by_visit_id=canonical.id,
                exit_time=self.now - timedelta(seconds=30),
            )
            db.add_all((canonical, superseded))
            db.commit()
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
            track_identity_resolver=lambda value: {
                "identity_status": "confirmed",
                "track_token": value.track_token,
                "member_id": "member-canonical",
                "visit_id": "visit-superseded",
            },
        )

        result = service.confirm_entry(self._entry("entry-superseded-cache"))

        with self.sessions() as db:
            self.assertEqual("visit-canonical", result.visit_id)
            evidence = db.query(VisitEntryEvidence).one()
            self.assertEqual("visit-canonical", evidence.visit_id)
            self.assertEqual(self.now, db.get(Visit, "visit-canonical").last_seen_at)

    def test_new_mode_confirmed_cache_closed_canonical_fails_closed(self):
        with self.sessions() as db:
            db.add(Member(id="member-closed", name="Closed"))
            db.add(Visit(
                id="visit-closed",
                member_id="member-closed",
                store_id="store-1",
                entry_time=self.now - timedelta(minutes=1),
                last_seen_at=self.now - timedelta(seconds=1),
                exit_time=self.now,
                is_member=True,
            ))
            db.commit()
        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=self.lease_validator,
            track_identity_resolver=lambda value: {
                "identity_status": "confirmed",
                "track_token": value.track_token,
                "member_id": "member-closed",
                "visit_id": "visit-closed",
            },
        )

        with self.assertRaisesRegex(
            CustomerEntryConflict, "confirmed_cache_visit_provenance_conflict"
        ):
            service.confirm_entry(self._entry("entry-closed-cache"))

        with self.sessions() as db:
            self.assertEqual(0, db.query(VisitEntryEvidence).count())
            self.assertEqual(1, db.query(Visit).count())

    def test_new_mode_lease_loss_rolls_back_entry_and_anonymous_visit(self):
        validations = []

        def validate_lease(_db, _entry):
            validations.append(True)
            if len(validations) == 2:
                raise RuntimeLeaseUnavailable("runtime_lease_lost")

        service = CustomerEntryVisit(
            self.sessions,
            identity_association_mode="new",
            runtime_lease_validator=validate_lease,
        )

        with self.assertRaisesRegex(RuntimeLeaseUnavailable, "runtime_lease_lost"):
            service.confirm_entry(self._entry("entry-lease-lost"))

        with self.sessions() as db:
            self.assertEqual(0, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(0, db.query(Visit).count())
            self.assertEqual(0, db.query(VisitEntryEvidence).count())
        self.assertEqual(2, len(validations))

    def _entry(
        self, dedup_key: str = "entry-dedup-1"
    ) -> CustomerEntryObservation:
        return CustomerEntryObservation(
            store_id="store-1",
            camera_id="entrance-1",
            tracker_track_id=7,
            runtime_session_id="runtime-1",
            direction="outside_to_inside",
            occurred_at=self.now,
            confirmed_at=self.now,
            staff_status="customer",
            staff_source="blue_vest",
            staff_confidence=0.96,
            entry_confidence=0.88,
            dedup_key=dedup_key,
        )

    def assert_confirmation_rejected(
        self, entry: CustomerEntryObservation
    ) -> None:
        with self.assertRaises(InvalidCustomerEntry):
            self.service.confirm_entry(entry)
        db = self.sessions()
        try:
            self.assertEqual(0, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(0, db.query(Visit).count())
        finally:
            db.close()

    def test_staff_does_not_create_entry_or_visit(self):
        self.assert_confirmation_rejected(replace(self._entry(), staff_status="staff"))

    def test_uncertain_staff_status_does_not_create_entry_or_visit(self):
        self.assert_confirmation_rejected(
            replace(self._entry(), staff_status="uncertain")
        )

    def test_unknown_staff_status_does_not_create_entry_or_visit(self):
        self.assert_confirmation_rejected(replace(self._entry(), staff_status="unknown"))

    def test_inside_to_outside_does_not_create_entry_or_visit(self):
        self.assert_confirmation_rejected(
            replace(self._entry(), direction="inside_to_outside")
        )

    def test_invalid_direction_does_not_create_entry_or_visit(self):
        self.assert_confirmation_rejected(replace(self._entry(), direction="sideways"))

    def test_invalid_confirmation_values_leave_no_partial_writes(self):
        invalid_values = (
            replace(self._entry(), occurred_at="not-a-datetime"),
            replace(self._entry(), confirmed_at="not-a-datetime"),
            replace(self._entry(), confirmed_at=self.now - timedelta(seconds=1)),
            replace(self._entry(), schema_version=0),
            replace(self._entry(), schema_version=True),
            replace(self._entry(), staff_confidence=-0.01),
            replace(self._entry(), staff_confidence=1.01),
            replace(self._entry(), entry_confidence=-0.01),
            replace(self._entry(), entry_confidence=1.01),
        )
        for value in invalid_values:
            with self.subTest(value=value):
                self.assert_confirmation_rejected(value)

    def test_entry_and_staff_confidence_are_persisted_independently(self):
        self.service.confirm_entry(
            replace(self._entry("entry-confidence"), entry_confidence=0.81)
        )

        db = self.sessions()
        try:
            entry = db.query(CustomerEntryConfirmedEvent).one()
            self.assertEqual(0.81, entry.confidence)
            self.assertEqual(0.96, entry.staff_confidence)
        finally:
            db.close()

    def test_repeated_confirmation_creates_one_entry_and_one_visit(self):
        first = self.service.confirm_entry(self._entry())
        second = self.service.confirm_entry(self._entry())

        db = self.sessions()
        try:
            self.assertEqual(first.id, second.id)
            self.assertEqual(1, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(1, db.query(Visit).count())
            visit = db.query(Visit).one()
            self.assertEqual("anonymous", visit.visitor_type)
            self.assertEqual("anonymous", visit.identity_status)
            self.assertEqual("entrance_crossing", visit.visit_source)
            self.assertEqual("confirmed", visit.verification_status)
        finally:
            db.close()

    def test_same_dedup_key_with_different_camera_conflicts(self):
        self._assert_dedup_conflict(camera_id="entrance-2")

    def test_same_dedup_key_with_different_track_token_conflicts(self):
        self._assert_dedup_conflict(tracker_track_id=99)

    def test_same_dedup_key_with_different_occurred_at_conflicts(self):
        self._assert_dedup_conflict(
            occurred_at=self.now + timedelta(seconds=1),
            confirmed_at=self.now + timedelta(seconds=1),
        )

    def _assert_dedup_conflict(self, **changes) -> None:
        original = self._entry("dedup-conflict")
        visit = self.service.confirm_entry(original)

        with self.assertRaises(CustomerEntryConflict):
            self.service.confirm_entry(replace(original, **changes))

        db = self.sessions()
        try:
            entry = db.query(CustomerEntryConfirmedEvent).one()
            stored_visit = db.query(Visit).one()
            self.assertEqual(original.camera_id, entry.camera_id)
            self.assertEqual(original.track_token, entry.track_token)
            self.assertEqual(original.occurred_at, entry.occurred_at)
            self.assertEqual(visit.id, stored_visit.id)
            self.assertEqual(entry.id, stored_visit.entry_event_id)
        finally:
            db.close()

    def test_entry_without_identity_creates_anonymous_visit(self):
        visit = self.service.confirm_entry(self._entry("entry-anonymous"))

        db = self.sessions()
        try:
            stored = db.get(Visit, visit.id)
            self.assertIsNone(stored.member_id)
            self.assertEqual("anonymous", stored.visitor_type)
            self.assertEqual("anonymous", stored.identity_status)
            self.assertIsNone(stored.member_visit_counted_at)
        finally:
            db.close()

    def test_entry_event_unique_constraint_rejects_second_visit(self):
        visit = self.service.confirm_entry(self._entry("entry-unique-visit"))
        db = self.sessions()
        try:
            db.add(Visit(
                entry_event_id=visit.entry_event_id,
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source="entrance_crossing",
                verification_status="confirmed",
            ))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()
            self.assertEqual(1, db.query(Visit).count())
        finally:
            db.close()

    def test_entry_then_identity_attachment_creates_counted_member_visit(self):
        db = self.sessions()
        try:
            db.add(Member(id="member-1", name="Member", visit_count=0))
            db.commit()
            db.add(IdentityEvidence(
                canonical_event_id="evidence-at-entry",
                device_id="entrance-1",
                source="push",
                source_event_id="source-at-entry",
                occurred_at=self.now,
                member_id="member-1",
                unique_id="member-1",
                similarity=0.94,
                status="processed",
                association_status="pending",
            ))
            db.commit()
        finally:
            db.close()

        anonymous_visit = self.service.confirm_entry(self._entry("entry-member-1"))
        visit = self.service.attach_member_identity(
            anonymous_visit.id, "evidence-at-entry"
        )

        db = self.sessions()
        try:
            member = db.get(Member, "member-1")
            stored = db.get(Visit, visit.id)
            self.assertEqual("member-1", stored.member_id)
            self.assertEqual("member", stored.visitor_type)
            self.assertEqual("identified_member", stored.identity_status)
            self.assertEqual(self.now, stored.member_visit_counted_at)
            self.assertEqual(1, member.visit_count)
            evidence = db.get(IdentityEvidence, "evidence-at-entry")
            self.assertEqual("matched", evidence.association_status)
            self.assertEqual(stored.entry_event_id, evidence.matched_entry_event_id)
            self.assertEqual(stored.id, evidence.matched_visit_id)
            self.assertEqual(
                "store-1:entrance-1:runtime-1:7",
                evidence.matched_track_token,
            )
            self.assertEqual("push", stored.identity_source)
        finally:
            db.close()

    def test_identity_attachment_replay_does_not_increment_visit_count(self):
        db = self.sessions()
        try:
            db.add(Member(id="member-entry-replay", name="Replay", visit_count=0))
            db.commit()
            db.add(IdentityEvidence(
                canonical_event_id="evidence-entry-replay",
                device_id="entrance-1",
                source="push",
                source_event_id="source-entry-replay",
                occurred_at=self.now,
                member_id="member-entry-replay",
                unique_id="member-entry-replay",
                similarity=0.9,
                status="processed",
                association_status="pending",
            ))
            db.commit()
        finally:
            db.close()
        visit = self.service.confirm_entry(self._entry("entry-identity-replay"))
        first = self.service.attach_member_identity(
            visit.id, "evidence-entry-replay"
        )
        second = self.service.attach_member_identity(
            visit.id, "evidence-entry-replay"
        )

        db = self.sessions()
        try:
            self.assertEqual(first.id, second.id)
            self.assertEqual(1, db.get(Member, "member-entry-replay").visit_count)
            self.assertEqual(
                "matched",
                db.get(IdentityEvidence, "evidence-entry-replay").association_status,
            )
        finally:
            db.close()

    def test_anonymous_visit_is_upgraded_without_changing_visit_id(self):
        visit = self.service.confirm_entry(self._entry("entry-upgrade-1"))
        db = self.sessions()
        try:
            db.add(Member(id="member-2", name="Member Two", visit_count=0))
            db.commit()
            db.add(IdentityEvidence(
                canonical_event_id="evidence-1",
                device_id="entrance-1",
                source="push",
                source_event_id="source-1",
                occurred_at=self.now,
                member_id="member-2",
                unique_id="person-2",
                similarity=0.93,
                status="processed",
                association_status="pending",
            ))
            db.commit()
        finally:
            db.close()

        upgraded = self.service.attach_member_identity(
            visit.id,
            "evidence-1",
        )

        db = self.sessions()
        try:
            stored = db.get(Visit, visit.id)
            evidence = db.get(IdentityEvidence, "evidence-1")
            self.assertEqual(visit.id, upgraded.id)
            self.assertEqual("member-2", stored.member_id)
            self.assertEqual("member", stored.visitor_type)
            self.assertEqual(1, db.get(Member, "member-2").visit_count)
            self.assertEqual("matched", evidence.association_status)
            self.assertEqual(visit.id, evidence.matched_visit_id)
            self.assertEqual(stored.entry_event_id, evidence.matched_entry_event_id)
        finally:
            db.close()

    def test_identity_upgrade_does_not_create_another_entry(self):
        visit = self.service.confirm_entry(self._entry("entry-count-stable"))
        self._add_member_and_evidence("member-count-stable", "evidence-count-stable")

        self.service.attach_member_identity(
            visit.id,
            "evidence-count-stable",
        )

        db = self.sessions()
        try:
            self.assertEqual(1, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(1, db.query(Visit).count())
        finally:
            db.close()

    def test_identity_replay_does_not_increment_member_visit_twice(self):
        visit = self.service.confirm_entry(self._entry("entry-replay-1"))
        self._add_member_and_evidence("member-replay", "evidence-replay")

        first = self.service.attach_member_identity(
            visit.id, "evidence-replay"
        )
        second = self.service.attach_member_identity(
            visit.id, "evidence-replay"
        )

        db = self.sessions()
        try:
            self.assertEqual(first.id, second.id)
            self.assertEqual(1, db.get(Member, "member-replay").visit_count)
            self.assertIsNotNone(db.get(Visit, visit.id).member_visit_counted_at)
        finally:
            db.close()

    def test_same_visit_and_member_with_new_evidence_is_idempotent(self):
        visit = self.service.confirm_entry(self._entry("entry-same-member"))
        self._add_member_and_evidence("member-same", "evidence-same-1")
        self.service.attach_member_identity(
            visit.id, "evidence-same-1"
        )
        self._add_evidence("member-same", "evidence-same-2")

        rebound = self.service.attach_member_identity(
            visit.id, "evidence-same-2"
        )

        db = self.sessions()
        try:
            self.assertEqual(visit.id, rebound.id)
            self.assertEqual(1, db.get(Member, "member-same").visit_count)
            self.assertEqual(
                "matched",
                db.get(IdentityEvidence, "evidence-same-2").association_status,
            )
        finally:
            db.close()

    def test_different_member_cannot_overwrite_visit_identity(self):
        visit = self.service.confirm_entry(self._entry("entry-conflict-1"))
        self._add_member_and_evidence("member-first", "evidence-first")
        self._add_member_and_evidence("member-second", "evidence-second")
        self.service.attach_member_identity(
            visit.id, "evidence-first"
        )

        with self.assertRaises(CustomerVisitIdentityConflict):
            self.service.attach_member_identity(
                visit.id, "evidence-second"
            )

        db = self.sessions()
        try:
            stored = db.get(Visit, visit.id)
            conflicting = db.get(IdentityEvidence, "evidence-second")
            self.assertEqual("member-first", stored.member_id)
            self.assertEqual(1, db.get(Member, "member-first").visit_count)
            self.assertEqual(0, db.get(Member, "member-second").visit_count)
            self.assertEqual("ambiguous", conflicting.association_status)
            self.assertEqual("visit_already_bound_to_another_member", conflicting.match_reason)
        finally:
            db.close()

    def test_unmapped_evidence_cannot_attach_identity(self):
        self._assert_ineligible_evidence(
            status="unmapped", expected_association_status="expired"
        )

    def test_ignored_evidence_cannot_attach_identity(self):
        self._assert_ineligible_evidence(
            status="ignored", expected_association_status="expired"
        )

    def test_expired_evidence_cannot_attach_identity(self):
        self._assert_ineligible_evidence(
            association_status="expired", expected_association_status="expired"
        )

    def test_evidence_without_member_cannot_attach_identity(self):
        visit = self.service.confirm_entry(self._entry("entry-no-member"))
        self._add_evidence(None, "evidence-no-member")

        with self.assertRaises(CustomerVisitIdentityError):
            self.service.attach_member_identity(visit.id, "evidence-no-member")

        self._assert_visit_remains_anonymous(visit.id)

    def test_compatibility_member_must_match_evidence(self):
        visit = self.service.confirm_entry(self._entry("entry-member-mismatch"))
        self._add_member_and_evidence("member-evidence", "evidence-member-mismatch")

        with self.assertRaises(CustomerVisitIdentityConflict):
            self.service.attach_member_identity(
                visit.id,
                "evidence-member-mismatch",
                member_id="member-caller",
            )

        self._assert_visit_remains_anonymous(visit.id)

    def test_evidence_already_matched_to_other_visit_is_rejected(self):
        first = self.service.confirm_entry(self._entry("entry-evidence-first"))
        second = self.service.confirm_entry(
            replace(
                self._entry("entry-evidence-second"),
                tracker_track_id=8,
            )
        )
        self._add_member_and_evidence("member-bound", "evidence-bound")
        self.service.attach_member_identity(first.id, "evidence-bound")

        with self.assertRaises(CustomerVisitIdentityConflict):
            self.service.attach_member_identity(second.id, "evidence-bound")

        self._assert_visit_remains_anonymous(second.id)
        db = self.sessions()
        try:
            evidence = db.get(IdentityEvidence, "evidence-bound")
            self.assertEqual("matched", evidence.association_status)
            self.assertEqual(first.id, evidence.matched_visit_id)
            self.assertEqual(2, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(2, db.query(Visit).count())
        finally:
            db.close()

    def test_legacy_visit_cannot_use_automatic_identity_attachment(self):
        self._assert_visit_is_not_eligible_for_automatic_identity(
            entry_event_id=None,
            visit_source="legacy",
            verification_status="legacy",
        )

    def test_recognition_fallback_visit_cannot_use_automatic_identity_attachment(self):
        visit = self.service.confirm_entry(self._entry("entry-fallback-visit"))
        self._set_visit_eligibility(
            visit.id,
            visit_source="recognition_fallback",
            verification_status="confirmed",
        )
        self._assert_existing_visit_is_not_eligible(visit.id, "fallback")

    def test_unconfirmed_visit_cannot_use_automatic_identity_attachment(self):
        visit = self.service.confirm_entry(self._entry("entry-unconfirmed-visit"))
        self._set_visit_eligibility(
            visit.id,
            visit_source="entrance_crossing",
            verification_status="pending",
        )
        self._assert_existing_visit_is_not_eligible(visit.id, "unconfirmed")

    def test_matched_evidence_must_match_target_entry(self):
        visit = self.service.confirm_entry(self._entry("entry-matched-consistency"))
        other_visit = self.service.confirm_entry(
            replace(
                self._entry("entry-matched-consistency-other"), tracker_track_id=17
            )
        )
        self._add_member_and_evidence(
            "member-matched-consistency", "evidence-matched-consistency"
        )
        db = self.sessions()
        try:
            evidence = db.get(IdentityEvidence, "evidence-matched-consistency")
            evidence.association_status = "matched"
            evidence.matched_visit_id = visit.id
            evidence.matched_entry_event_id = other_visit.entry_event_id
            db.commit()
        finally:
            db.close()

        with self.assertRaises(CustomerVisitIdentityConflict):
            self.service.attach_member_identity(
                visit.id, "evidence-matched-consistency"
            )

        self._assert_visit_remains_anonymous(visit.id)

    def test_existing_matched_entry_cannot_be_overwritten(self):
        visit = self.service.confirm_entry(self._entry("entry-existing-match"))
        other_visit = self.service.confirm_entry(
            replace(self._entry("entry-existing-match-other"), tracker_track_id=18)
        )
        self._add_member_and_evidence(
            "member-existing-match", "evidence-existing-match"
        )
        db = self.sessions()
        try:
            evidence = db.get(IdentityEvidence, "evidence-existing-match")
            evidence.matched_entry_event_id = other_visit.entry_event_id
            db.commit()
        finally:
            db.close()

        with self.assertRaises(CustomerVisitIdentityConflict):
            self.service.attach_member_identity(
                visit.id, "evidence-existing-match"
            )

        self._assert_visit_remains_anonymous(visit.id)

    def _assert_visit_is_not_eligible_for_automatic_identity(
        self,
        *,
        entry_event_id: str | None,
        visit_source: str,
        verification_status: str,
    ) -> None:
        db = self.sessions()
        try:
            visit = Visit(
                entry_event_id=entry_event_id,
                visitor_type="anonymous",
                identity_status="anonymous",
                visit_source=visit_source,
                verification_status=verification_status,
            )
            db.add(visit)
            db.commit()
            visit_id = visit.id
        finally:
            db.close()
        self._assert_existing_visit_is_not_eligible(visit_id, "legacy")

    def _assert_existing_visit_is_not_eligible(
        self, visit_id: str, suffix: str
    ) -> None:
        member_id = f"member-ineligible-{suffix}"
        evidence_id = f"evidence-ineligible-{suffix}"
        self._add_member_and_evidence(member_id, evidence_id)

        with self.assertRaises(CustomerVisitIdentityError):
            self.service.attach_member_identity(visit_id, evidence_id)

        self._assert_visit_remains_anonymous(visit_id)
        db = self.sessions()
        try:
            self.assertEqual(0, db.get(Member, member_id).visit_count)
            self.assertEqual(
                "pending", db.get(IdentityEvidence, evidence_id).association_status
            )
        finally:
            db.close()

    def _set_visit_eligibility(
        self,
        visit_id: str,
        *,
        visit_source: str,
        verification_status: str,
    ) -> None:
        db = self.sessions()
        try:
            visit = db.get(Visit, visit_id)
            visit.visit_source = visit_source
            visit.verification_status = verification_status
            db.commit()
        finally:
            db.close()

    def _assert_ineligible_evidence(
        self,
        *,
        status: str = "processed",
        association_status: str = "pending",
        expected_association_status: str | None = None,
    ) -> None:
        visit = self.service.confirm_entry(
            self._entry(f"entry-{status}-{association_status}")
        )
        self._add_member_and_evidence(
            f"member-{status}-{association_status}",
            f"evidence-{status}-{association_status}",
            status=status,
            association_status=association_status,
        )

        with self.assertRaises(CustomerVisitIdentityError):
            self.service.attach_member_identity(
                visit.id, f"evidence-{status}-{association_status}"
            )

        self._assert_visit_remains_anonymous(visit.id)
        db = self.sessions()
        try:
            self.assertEqual(1, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(1, db.query(Visit).count())
            self.assertEqual(
                0,
                db.get(Member, f"member-{status}-{association_status}").visit_count,
            )
            evidence = db.get(
                IdentityEvidence, f"evidence-{status}-{association_status}"
            )
            self.assertEqual(
                expected_association_status or association_status,
                evidence.association_status,
            )
        finally:
            db.close()

    def _assert_visit_remains_anonymous(self, visit_id: str) -> None:
        db = self.sessions()
        try:
            visit = db.get(Visit, visit_id)
            self.assertIsNone(visit.member_id)
            self.assertEqual("anonymous", visit.identity_status)
        finally:
            db.close()

    def test_recognition_attaches_to_one_contemporaneous_entry_visit(self):
        visit = self.service.confirm_entry(
            self._entry("entry-recognition-match")
        )
        self._add_member_and_evidence(
            "member-recognition-match",
            "evidence-recognition-match",
        )

        attached = self.service.attach_recognition_identity(
            "evidence-recognition-match",
            camera_id="entrance-1",
            occurred_at=self.now,
            member_id="member-recognition-match",
            confidence=0.93,
        )

        db = self.sessions()
        try:
            self.assertEqual(visit.id, attached.id)
            self.assertEqual(1, db.query(Visit).count())
            stored = db.get(Visit, visit.id)
            self.assertEqual("member-recognition-match", stored.member_id)
            self.assertEqual(1, db.get(Member, stored.member_id).visit_count)
            evidence = db.get(
                IdentityEvidence,
                "evidence-recognition-match",
            )
            self.assertEqual("matched", evidence.association_status)
            self.assertEqual(visit.id, evidence.matched_visit_id)
        finally:
            db.close()

    def test_recognition_does_not_guess_between_two_entry_visits(self):
        first = self.service.confirm_entry(self._entry("entry-ambiguous-first"))
        second_time = self.now + timedelta(milliseconds=500)
        second = self.service.confirm_entry(
            replace(
                self._entry("entry-ambiguous-second"),
                tracker_track_id=8,
                occurred_at=second_time,
                confirmed_at=second_time,
            )
        )
        self._add_member_and_evidence(
            "member-ambiguous-entry",
            "evidence-ambiguous-entry",
        )

        attached = self.service.attach_recognition_identity(
            "evidence-ambiguous-entry",
            camera_id="entrance-1",
            occurred_at=self.now,
            member_id="member-ambiguous-entry",
            confidence=0.93,
        )

        self.assertIsNone(attached)
        self._assert_visit_remains_anonymous(first.id)
        self._assert_visit_remains_anonymous(second.id)
        db = self.sessions()
        try:
            evidence = db.get(IdentityEvidence, "evidence-ambiguous-entry")
            self.assertEqual("pending", evidence.association_status)
            self.assertEqual(0, db.get(Member, "member-ambiguous-entry").visit_count)
        finally:
            db.close()

    def _add_member_and_evidence(
        self,
        member_id: str,
        evidence_id: str,
        *,
        status: str = "processed",
        association_status: str = "pending",
    ) -> None:
        db = self.sessions()
        try:
            db.add(Member(id=member_id, name=member_id, visit_count=0))
            db.commit()
        finally:
            db.close()
        self._add_evidence(
            member_id,
            evidence_id,
            status=status,
            association_status=association_status,
        )

    def _add_evidence(
        self,
        member_id: str | None,
        evidence_id: str,
        *,
        status: str = "processed",
        association_status: str = "pending",
    ) -> None:
        db = self.sessions()
        try:
            db.add(IdentityEvidence(
                canonical_event_id=evidence_id,
                device_id="entrance-1",
                source="push",
                source_event_id=f"source-{evidence_id}",
                occurred_at=self.now,
                member_id=member_id,
                unique_id=member_id or "unmapped-person",
                similarity=0.93,
                status=status,
                association_status=association_status,
            ))
            db.commit()
        finally:
            db.close()


class EntryDomainMigrationTests(unittest.TestCase):
    def test_initialized_entry_domain_schema_is_ready(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            evolve_schema(engine)
            require_entry_schema(engine)
        finally:
            engine.dispose()

    def test_same_named_non_unique_index_is_not_ready(self):
        engine = self._readiness_engine(
            entry_index="CREATE INDEX uq_customer_entry_dedup_key "
            "ON customer_entry_events(dedup_key)",
            visit_index="CREATE UNIQUE INDEX uq_visits_entry_event_id "
            "ON visits(entry_event_id)",
        )
        try:
            with self.assertRaisesRegex(
                EntryDomainSchemaNotReadyError, "unique=True"
            ):
                require_entry_schema(engine)
        finally:
            engine.dispose()

    def test_same_named_index_on_wrong_column_is_not_ready(self):
        engine = self._readiness_engine(
            entry_index="CREATE UNIQUE INDEX uq_customer_entry_dedup_key "
            "ON customer_entry_events(wrong_column)",
            visit_index="CREATE UNIQUE INDEX uq_visits_entry_event_id "
            "ON visits(entry_event_id)",
        )
        try:
            with self.assertRaisesRegex(
                EntryDomainSchemaNotReadyError, r"columns=\['dedup_key'\]"
            ):
                require_entry_schema(engine)
        finally:
            engine.dispose()

    def test_missing_required_index_is_not_ready(self):
        engine = self._readiness_engine(
            entry_index="CREATE UNIQUE INDEX uq_customer_entry_dedup_key "
            "ON customer_entry_events(dedup_key)",
            visit_index=None,
        )
        try:
            with self.assertRaisesRegex(
                EntryDomainSchemaNotReadyError, "uq_visits_entry_event_id"
            ):
                require_entry_schema(engine)
        finally:
            engine.dispose()

    def test_migration_cli_returns_one_when_schema_is_not_ready(self):
        path = Path.cwd() / f"entry-domain-cli-conflict-{uuid4().hex}.sqlite3"
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE customer_entry_events (id VARCHAR(64) PRIMARY KEY, "
                    "entry_key VARCHAR(192), camera_id VARCHAR(64), track_id INTEGER, "
                    "track_token VARCHAR(128), occurred_at DATETIME, member_id VARCHAR(64), "
                    "member_tier VARCHAR(20), confidence FLOAT, created_at DATETIME)"
                )
                for event_id in ("entry-1", "entry-2"):
                    connection.exec_driver_sql(
                        "INSERT INTO customer_entry_events VALUES "
                        "(?, 'duplicate-key', 'entrance-1', 7, 'runtime:7', "
                        "'2026-07-20 10:00:00', NULL, NULL, 0.9, "
                        "'2026-07-20 10:00:00')",
                        (event_id,),
                    )
        finally:
            engine.dispose()

        result = subprocess.run(
            [
                sys.executable,
                str(Path.cwd() / "scripts" / "migrate_entry_domain.py"),
                "--database-url",
                f"sqlite:///{path}",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            self.assertEqual(1, result.returncode)
            self.assertIn("failed readiness check", result.stderr)
        finally:
            path.unlink(missing_ok=True)

    @staticmethod
    def _readiness_engine(*, entry_index: str, visit_index: str | None):
        engine = create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE customer_entry_events "
                "(id VARCHAR(64) PRIMARY KEY, dedup_key VARCHAR(192), "
                "wrong_column VARCHAR(192))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE visits "
                "(id VARCHAR(64) PRIMARY KEY, entry_event_id VARCHAR(64))"
            )
            connection.exec_driver_sql(entry_index)
            if visit_index is not None:
                connection.exec_driver_sql(visit_index)
        return engine

    def test_legacy_sqlite_rows_are_extended_and_marked_without_relinking(self):
        path = Path.cwd() / f"entry-domain-migration-{uuid4().hex}.sqlite3"
        engine = configure_sqlite_foreign_keys(create_engine(f"sqlite:///{path}"))
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE members (id VARCHAR(64) PRIMARY KEY, name VARCHAR(128), "
                    "visit_count INTEGER, last_visit DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE visits (id VARCHAR(64) PRIMARY KEY, member_id VARCHAR(64), "
                    "entry_time DATETIME, is_member BOOLEAN, created_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE customer_entry_events (id VARCHAR(64) PRIMARY KEY, "
                    "entry_key VARCHAR(192), camera_id VARCHAR(64), track_id INTEGER, "
                    "track_token VARCHAR(128), occurred_at DATETIME, member_id VARCHAR(64), "
                    "member_tier VARCHAR(20), confidence FLOAT, created_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE behavior_events (id VARCHAR(64) PRIMARY KEY, "
                    "event_key VARCHAR(192), customer_track_token VARCHAR(128))"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE device_recognition_inbox (canonical_event_id VARCHAR(64) PRIMARY KEY, "
                    "device_id VARCHAR(64), source VARCHAR(16), source_event_id VARCHAR(128), "
                    "occurred_at DATETIME, member_id VARCHAR(64), unique_id VARCHAR(64), "
                    "status VARCHAR(24), received_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO members VALUES ('member-old', 'Old', 1, NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO visits VALUES "
                    "('visit-old', 'member-old', '2026-07-20 10:00:00', 1, '2026-07-20 10:00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO customer_entry_events VALUES "
                    "('entry-old', 'dedup-old', 'entrance-1', 7, 'runtime:7', "
                    "'2026-07-20 10:00:00', NULL, NULL, 0.9, '2026-07-20 10:00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO behavior_events VALUES ('behavior-old', 'behavior-key', 'runtime:7')"
                )

            evolve_schema(engine)
            evolve_schema(engine)

            inspector = inspect(engine)
            visit_columns = {item["name"] for item in inspector.get_columns("visits")}
            entry_columns = {
                item["name"] for item in inspector.get_columns("customer_entry_events")
            }
            behavior_columns = {
                item["name"] for item in inspector.get_columns("behavior_events")
            }
            self.assertTrue(
                {"entry_event_id", "visitor_type", "member_visit_counted_at"}
                <= visit_columns
            )
            self.assertTrue(
                {"dedup_key", "runtime_session_id", "tracker_track_id"}
                <= entry_columns
            )
            self.assertTrue(
                {
                    "track_token",
                    "entry_event_id",
                    "visit_id",
                    "is_demo",
                    "staff_name",
                    "staff_attribution_source",
                }
                <= behavior_columns
            )
            with engine.connect() as connection:
                legacy = connection.exec_driver_sql(
                    "SELECT visit_source, verification_status, entry_event_id, "
                    "member_visit_counted_at FROM visits WHERE id = 'visit-old'"
                ).one()
                entry = connection.exec_driver_sql(
                    "SELECT dedup_key, tracker_track_id, confidence "
                    "FROM customer_entry_events "
                    "WHERE id = 'entry-old'"
                ).one()
                behavior_token = connection.exec_driver_sql(
                    "SELECT track_token, is_demo FROM behavior_events WHERE id = 'behavior-old'"
                ).one()
            self.assertEqual(("legacy", "legacy", None), legacy[:3])
            self.assertIsNotNone(legacy.member_visit_counted_at)
            self.assertEqual(("dedup-old", 7, 0.9), entry)
            self.assertEqual(("runtime:7", 0), behavior_token)
        finally:
            engine.dispose()
            path.unlink(missing_ok=True)

    def test_duplicate_legacy_dedup_keys_are_reported_without_deleting_rows(self):
        path = Path.cwd() / f"entry-domain-conflict-{uuid4().hex}.sqlite3"
        engine = create_engine(f"sqlite:///{path}")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE customer_entry_events (id VARCHAR(64) PRIMARY KEY, "
                    "entry_key VARCHAR(192), camera_id VARCHAR(64), track_id INTEGER, "
                    "track_token VARCHAR(128), occurred_at DATETIME, member_id VARCHAR(64), "
                    "member_tier VARCHAR(20), confidence FLOAT, created_at DATETIME)"
                )
                for event_id in ("entry-1", "entry-2"):
                    connection.exec_driver_sql(
                        "INSERT INTO customer_entry_events VALUES "
                        "(?, 'duplicate-key', 'entrance-1', 7, 'runtime:7', "
                        "'2026-07-20 10:00:00', NULL, NULL, 0.9, "
                        "'2026-07-20 10:00:00')",
                        (event_id,),
                    )

            with self.assertLogs("agent.schema_evolution", level="ERROR") as logs:
                evolve_schema(engine)

            with engine.connect() as connection:
                count = connection.exec_driver_sql(
                    "SELECT COUNT(*) FROM customer_entry_events"
                ).scalar_one()
                indexes = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA index_list('customer_entry_events')"
                    ).all()
                }
            self.assertEqual(2, count)
            self.assertNotIn("uq_customer_entry_dedup_key", indexes)
            self.assertIn("duplicates", "\n".join(logs.output))
            with self.assertRaisesRegex(
                EntryDomainSchemaNotReadyError,
                "uq_customer_entry_dedup_key",
            ):
                require_entry_schema(engine)
        finally:
            engine.dispose()
            path.unlink(missing_ok=True)


class EntryDomainConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.path = Path.cwd() / f"entry-domain-concurrency-{uuid4().hex}.sqlite3"
        self.engine = configure_sqlite_foreign_keys(create_engine(
            f"sqlite:///{self.path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        ))
        evolve_schema(self.engine)
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
        self.now = datetime(2026, 7, 28, 13, 0, 0)

    def tearDown(self):
        self.engine.dispose()
        self.path.unlink(missing_ok=True)

    def _entry(self, key: str) -> CustomerEntryObservation:
        return CustomerEntryObservation(
            store_id="store-1",
            camera_id="entrance-1",
            tracker_track_id=11,
            runtime_session_id="runtime-concurrent",
            direction="outside_to_inside",
            occurred_at=self.now,
            confirmed_at=self.now,
            staff_status="customer",
            staff_source="blue_vest",
            staff_confidence=0.93,
            entry_confidence=0.9,
            dedup_key=key,
        )

    def test_concurrent_entry_confirmation_creates_one_visit(self):
        barrier = threading.Barrier(2)
        results, errors = [], []
        lock = threading.Lock()

        def confirm():
            try:
                barrier.wait(timeout=5)
                result = CustomerEntryVisit(self.sessions).confirm_entry(
                    self._entry("entry-concurrent")
                )
                with lock:
                    results.append(result.id)
            except Exception as error:
                with lock:
                    errors.append(error)

        threads = [threading.Thread(target=confirm) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        db = self.sessions()
        try:
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([], errors)
            self.assertEqual(1, len(set(results)))
            self.assertEqual(1, db.query(CustomerEntryConfirmedEvent).count())
            self.assertEqual(1, db.query(Visit).count())
        finally:
            db.close()

    def test_concurrent_identity_attachment_counts_member_once(self):
        visit = CustomerEntryVisit(self.sessions).confirm_entry(
            self._entry("identity-concurrent")
        )
        db = self.sessions()
        try:
            db.add(Member(id="member-concurrent", name="Concurrent", visit_count=0))
            db.commit()
            db.add(IdentityEvidence(
                canonical_event_id="evidence-concurrent",
                device_id="entrance-1",
                source="push",
                source_event_id="source-concurrent",
                occurred_at=self.now,
                member_id="member-concurrent",
                unique_id="member-concurrent",
                similarity=0.95,
                status="processed",
                association_status="pending",
            ))
            db.commit()
        finally:
            db.close()

        barrier = threading.Barrier(2)
        results, errors = [], []
        lock = threading.Lock()

        def attach():
            try:
                barrier.wait(timeout=5)
                result = CustomerEntryVisit(self.sessions).attach_member_identity(
                    visit.id,
                    "evidence-concurrent",
                )
                with lock:
                    results.append(result.id)
            except Exception as error:
                with lock:
                    errors.append(error)

        threads = [threading.Thread(target=attach) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        db = self.sessions()
        try:
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual([], errors)
            self.assertEqual(1, len(set(results)))
            self.assertEqual(1, db.get(Member, "member-concurrent").visit_count)
            self.assertIsNotNone(db.get(Visit, visit.id).member_visit_counted_at)
        finally:
            db.close()

    def test_concurrent_different_members_only_bind_once(self):
        visit = CustomerEntryVisit(self.sessions).confirm_entry(
            self._entry("identity-member-race")
        )
        db = self.sessions()
        try:
            for suffix in ("a", "b"):
                member_id = f"member-race-{suffix}"
                db.add(Member(id=member_id, name=member_id, visit_count=0))
            db.commit()
            for suffix in ("a", "b"):
                member_id = f"member-race-{suffix}"
                db.add(IdentityEvidence(
                    canonical_event_id=f"evidence-race-{suffix}",
                    device_id="entrance-1",
                    source="push",
                    source_event_id=f"source-race-{suffix}",
                    occurred_at=self.now,
                    member_id=member_id,
                    unique_id=member_id,
                    similarity=0.95,
                    status="processed",
                    association_status="pending",
                ))
            db.commit()
        finally:
            db.close()

        barrier = threading.Barrier(2)
        successes, errors = [], []
        lock = threading.Lock()

        def attach(evidence_id: str) -> None:
            try:
                barrier.wait(timeout=5)
                result = CustomerEntryVisit(self.sessions).attach_member_identity(
                    visit.id, evidence_id
                )
                with lock:
                    successes.append(result.member_id)
            except Exception as error:
                with lock:
                    errors.append(error)

        threads = [
            threading.Thread(target=attach, args=(f"evidence-race-{suffix}",))
            for suffix in ("a", "b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        db = self.sessions()
        try:
            stored = db.get(Visit, visit.id)
            visit_counts = [
                db.get(Member, f"member-race-{suffix}").visit_count
                for suffix in ("a", "b")
            ]
            evidence_statuses = {
                db.get(IdentityEvidence, f"evidence-race-{suffix}").association_status
                for suffix in ("a", "b")
            }
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(1, len(successes))
            self.assertEqual(1, len(errors))
            self.assertIsInstance(errors[0], CustomerVisitIdentityConflict)
            self.assertEqual(successes[0], stored.member_id)
            self.assertEqual(1, sum(visit_counts))
            self.assertEqual({"matched", "ambiguous"}, evidence_statuses)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
