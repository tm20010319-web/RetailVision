"""Durable Customer Entry Event, Visit, and identity orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from agent._customer_visit_identity import (
    CustomerVisitIdentityConflict,
    CustomerVisitIdentityError,
    attach_identity_from_evidence,
)
from agent.behavior_events import EntryEventInput
from agent.models import CustomerEntryConfirmedEvent, IdentityEvidence, Visit, VisitEntryEvidence
from agent.visit_merge_service import resolve_canonical_visit_id
from agent.reusable_visit_resolver import ReusableVisitValidator
from agent.visit_activity import touch_confirmed_visit_activity_in_transaction


class CustomerEntryVisitError(RuntimeError):
    pass


class InvalidCustomerEntry(CustomerEntryVisitError, ValueError):
    pass


class CustomerEntryConflict(CustomerEntryVisitError):
    pass


@dataclass(frozen=True)
class CustomerEntryObservation:
    store_id: str
    camera_id: str
    tracker_track_id: int
    runtime_session_id: str
    direction: str
    occurred_at: datetime
    confirmed_at: datetime
    staff_status: str
    staff_source: Optional[str]
    staff_confidence: Optional[float]
    entry_confidence: float
    dedup_key: str
    schema_version: int = 1
    id: Optional[str] = None

    @property
    def track_token(self) -> str:
        return (
            f"{self.store_id}:{self.camera_id}:"
            f"{self.runtime_session_id}:{self.tracker_track_id}"
        )

    @classmethod
    def from_behavior_entry(
        cls,
        value: EntryEventInput,
        *,
        store_id: str,
        runtime_session_id: str,
    ) -> "CustomerEntryObservation":
        expected_token = (
            f"{store_id}:{value.camera_id}:"
            f"{runtime_session_id}:{value.track_id}"
        )
        if value.track_token != expected_token:
            raise InvalidCustomerEntry(
                "behavior track_token does not match store, camera, runtime, and track"
            )
        return cls(
            store_id=store_id,
            camera_id=value.camera_id,
            tracker_track_id=value.track_id,
            runtime_session_id=runtime_session_id,
            direction="outside_to_inside",
            occurred_at=value.occurred_at,
            confirmed_at=value.occurred_at,
            staff_status="customer",
            staff_source="behavior_role",
            staff_confidence=None,
            entry_confidence=value.confidence,
            dedup_key=value.entry_key,
        )


@dataclass(frozen=True)
class CustomerVisitResult:
    visit_id: str
    entry_event_id: str
    member_id: Optional[str]
    visitor_type: Optional[str]
    identity_status: Optional[str]
    identity_source: Optional[str]
    visit_source: Optional[str]
    verification_status: Optional[str]

    @property
    def id(self) -> str:
        return self.visit_id


class CustomerEntryVisit:
    """Confirm entries and attach identity behind one transactional interface."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] = datetime.now,
        identity_association_seconds: float = 2.0,
        identity_association_mode: str = "legacy",
        track_identity_resolver: Optional[Callable[[CustomerEntryObservation], Optional[dict[str, Any]]]] = None,
        reusable_visit_validator: Optional[ReusableVisitValidator] = None,
        runtime_lease_validator: Optional[
            Callable[[Session, CustomerEntryObservation], None]
        ] = None,
    ) -> None:
        if identity_association_seconds <= 0:
            raise ValueError("identity_association_seconds must be positive")
        if identity_association_mode not in {"new", "legacy"}:
            raise ValueError("identity_association_mode must be new or legacy")
        self.session_factory = session_factory
        self.clock = clock
        self.identity_association = timedelta(
            seconds=identity_association_seconds
        )
        self.identity_association_mode = identity_association_mode
        self.track_identity_resolver = track_identity_resolver
        self.reusable_visit_validator = reusable_visit_validator or ReusableVisitValidator()
        self.runtime_lease_validator = runtime_lease_validator
        if self.identity_association_mode == "new" and self.runtime_lease_validator is None:
            raise ValueError("new mode requires runtime_lease_validator")

    def confirm_entry(
        self,
        entry: CustomerEntryObservation,
    ) -> CustomerVisitResult:
        self._validate_entry(entry)
        cached_identity = self._confirmed_cache_identity(entry)
        db = self.session_factory()
        try:
            self._begin_write(db)
            self._validate_runtime_lease(db, entry)
            stored_entry = (
                db.query(CustomerEntryConfirmedEvent)
                .filter(
                    CustomerEntryConfirmedEvent.dedup_key == entry.dedup_key
                )
                .one_or_none()
            )
            if stored_entry is None:
                stored_entry = CustomerEntryConfirmedEvent(
                    id=entry.id,
                    entry_key=entry.dedup_key,
                    dedup_key=entry.dedup_key,
                    store_id=entry.store_id,
                    camera_id=entry.camera_id,
                    track_id=entry.tracker_track_id,
                    tracker_track_id=entry.tracker_track_id,
                    track_token=entry.track_token,
                    runtime_session_id=entry.runtime_session_id,
                    direction=entry.direction,
                    occurred_at=entry.occurred_at,
                    confirmed_at=entry.confirmed_at,
                    staff_status=entry.staff_status,
                    staff_source=entry.staff_source,
                    staff_confidence=entry.staff_confidence,
                    confidence=entry.entry_confidence,
                    schema_version=entry.schema_version,
                )
                db.add(stored_entry)
                db.flush()
            else:
                self._validate_replay(stored_entry, entry)

            evidence = db.query(VisitEntryEvidence).filter(
                VisitEntryEvidence.entry_event_id == stored_entry.id
            ).one_or_none()
            visit = db.get(Visit, resolve_canonical_visit_id(db, evidence.visit_id)) if evidence else (
                db.query(Visit)
                .filter(Visit.entry_event_id == stored_entry.id)
                .one_or_none()
            )
            if self.identity_association_mode == "new" and cached_identity is not None:
                confirmed_visit = self._existing_canonical_visit_from_confirmed_cache(
                    db, entry, cached_identity
                )
                if confirmed_visit is None:
                    raise CustomerEntryConflict(
                        "confirmed_cache_visit_provenance_conflict"
                    )
                if visit is not None and visit.id != confirmed_visit.id:
                    raise CustomerEntryConflict(
                        "confirmed_cache_visit_provenance_conflict"
                    )
                visit = confirmed_visit
            if visit is None:
                visit = Visit(
                    entry_event_id=stored_entry.id,
                    store_id=stored_entry.store_id,
                    camera_id=stored_entry.camera_id,
                    track_id=stored_entry.tracker_track_id,
                    entry_time=stored_entry.occurred_at,
                    zones_visited=["entrance"],
                    is_member=False,
                    visitor_type="anonymous",
                    identity_status="anonymous",
                    visit_source="entrance_crossing",
                    verification_status="confirmed",
                )
                db.add(visit)
                db.flush()
            elif visit.entry_event_id is None:
                visit.entry_event_id = stored_entry.id
            if evidence is None:
                db.add(VisitEntryEvidence(
                    visit_id=visit.id,
                    entry_event_id=stored_entry.id,
                    evidence_role="initial" if visit.entry_event_id == stored_entry.id else "repeated",
                ))
            if cached_identity is not None:
                touch_confirmed_visit_activity_in_transaction(
                    db,
                    visit_id=visit.id,
                    member_id=cached_identity["member_id"],
                    store_id=entry.store_id,
                    observed_at=entry.occurred_at,
                    transaction_now=self.clock(),
                    validator=self.reusable_visit_validator,
                )
            self._validate_runtime_lease(db, entry)
            db.commit()
            db.refresh(visit)
            return self._result(visit)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _confirmed_cache_identity(
        self, entry: CustomerEntryObservation
    ) -> Optional[dict[str, Any]]:
        if self.identity_association_mode != "new" or self.track_identity_resolver is None:
            return None
        binding = self.track_identity_resolver(entry)
        if not isinstance(binding, dict):
            return None
        if (
            binding.get("identity_status") != "confirmed"
            or binding.get("shadow") is True
            or binding.get("track_token") != entry.track_token
            or not binding.get("member_id")
            or not binding.get("visit_id")
        ):
            return None
        return binding

    def _existing_canonical_visit_from_confirmed_cache(
        self,
        db: Session,
        entry: CustomerEntryObservation,
        binding: Optional[dict[str, Any]],
    ) -> Optional[Visit]:
        if binding is None:
            return None
        try:
            canonical_id = resolve_canonical_visit_id(db, binding["visit_id"])
        except (KeyError, ValueError):
            return None
        visit = db.get(Visit, canonical_id)
        if visit is None:
            return None
        if (
            visit.store_id != entry.store_id
            or visit.member_id != binding["member_id"]
            or visit.exit_time is not None
            or visit.superseded_by_visit_id is not None
        ):
            return None
        return self.reusable_visit_validator.resolve(
            db,
            visit_id=visit.id,
            member_id=binding["member_id"],
            store_id=entry.store_id,
            now=entry.occurred_at,
        )

    def _validate_runtime_lease(
        self, db: Session, entry: CustomerEntryObservation
    ) -> None:
        if self.identity_association_mode != "new":
            return
        if self.runtime_lease_validator is None:
            return
        self.runtime_lease_validator(db, entry)

    def attach_member_identity(
        self,
        visit_id: str,
        identity_evidence_id: str,
        *,
        member_id: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> CustomerVisitResult:
        if self.identity_association_mode == "new":
            raise CustomerVisitIdentityError(
                "new mode delegates member identity attachment to ReusableVisitResolver"
            )
        self._validate_identity_request(
            visit_id,
            identity_evidence_id,
            member_id=member_id,
            confidence=confidence,
        )
        db = self.session_factory()
        try:
            self._begin_write(db)
            visit = self._visit_for_update(db, visit_id)
            if visit is None:
                raise CustomerVisitIdentityError(
                    f"visit not found: {visit_id}"
                )
            attach_identity_from_evidence(
                db,
                visit,
                identity_evidence_id,
                clock=self.clock,
                expected_member_id=member_id,
                expected_confidence=confidence,
            )
            db.commit()
            db.refresh(visit)
            return self._result(visit)
        except CustomerVisitIdentityError as error:
            if error.persist_evidence:
                db.commit()
            else:
                db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def attach_recognition_identity(
        self,
        identity_evidence_id: str,
        *,
        camera_id: str,
        occurred_at: datetime,
        member_id: str,
        confidence: float,
    ) -> Optional[CustomerVisitResult]:
        """Attach only when one contemporaneous entrance Visit is available."""
        if self.identity_association_mode == "new":
            raise CustomerVisitIdentityError(
                "new mode delegates recognition identity attachment to ReusableVisitResolver"
            )
        self._validate_identity_request(
            "candidate",
            identity_evidence_id,
            member_id=member_id,
            confidence=confidence,
        )
        if not isinstance(camera_id, str) or not camera_id.strip():
            raise ValueError("camera_id is required")
        if not isinstance(occurred_at, datetime):
            raise ValueError("occurred_at must be a datetime")

        db = self.session_factory()
        try:
            self._begin_write(db)
            evidence = db.get(IdentityEvidence, identity_evidence_id)
            if evidence is None:
                raise CustomerVisitIdentityError(
                    f"identity evidence not found: {identity_evidence_id}"
                )
            if evidence.matched_visit_id:
                visit = self._visit_for_update(db, evidence.matched_visit_id)
                if visit is None:
                    raise CustomerVisitIdentityConflict(
                        "matched identity evidence references a missing visit"
                    )
            else:
                visit = self._unique_identity_candidate(
                    db,
                    camera_id=camera_id,
                    occurred_at=occurred_at,
                )
                if visit is None:
                    db.rollback()
                    return None
            attach_identity_from_evidence(
                db,
                visit,
                identity_evidence_id,
                clock=self.clock,
                expected_member_id=member_id,
                expected_confidence=confidence,
            )
            db.commit()
            db.refresh(visit)
            return self._result(visit)
        except CustomerVisitIdentityError as error:
            if error.persist_evidence:
                db.commit()
            else:
                db.rollback()
            raise
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _unique_identity_candidate(
        self,
        db: Session,
        *,
        camera_id: str,
        occurred_at: datetime,
    ) -> Optional[Visit]:
        query = db.query(Visit).filter(
            Visit.camera_id == camera_id,
            Visit.entry_event_id.is_not(None),
            Visit.visit_source == "entrance_crossing",
            Visit.verification_status == "confirmed",
            Visit.entry_time >= occurred_at - self.identity_association,
            Visit.entry_time <= occurred_at + self.identity_association,
        )
        if db.get_bind().dialect.name != "sqlite":
            query = query.with_for_update()
        candidates = query.order_by(Visit.entry_time, Visit.id).limit(2).all()
        return candidates[0] if len(candidates) == 1 else None

    @staticmethod
    def _visit_for_update(db: Session, visit_id: str) -> Optional[Visit]:
        query = db.query(Visit).filter(Visit.id == visit_id)
        if db.get_bind().dialect.name != "sqlite":
            query = query.with_for_update()
        return query.one_or_none()

    @staticmethod
    def _begin_write(db: Session) -> None:
        if db.get_bind().dialect.name == "sqlite":
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        else:
            db.begin()

    @staticmethod
    def _validate_entry(value: CustomerEntryObservation) -> None:
        if not isinstance(value, CustomerEntryObservation):
            raise InvalidCustomerEntry(
                "entry must be a CustomerEntryObservation"
            )
        for name in (
            "store_id",
            "camera_id",
            "runtime_session_id",
            "direction",
            "staff_status",
            "dedup_key",
        ):
            item = getattr(value, name)
            if not isinstance(item, str) or not item.strip():
                raise InvalidCustomerEntry(f"{name} is required")
        if value.direction != "outside_to_inside":
            raise InvalidCustomerEntry(
                "direction must be outside_to_inside for a customer entry"
            )
        if value.staff_status != "customer":
            raise InvalidCustomerEntry(
                "staff_status must be customer for a customer entry"
            )
        if (
            isinstance(value.tracker_track_id, bool)
            or not isinstance(value.tracker_track_id, int)
            or value.tracker_track_id < 0
        ):
            raise InvalidCustomerEntry(
                "tracker_track_id must be a non-negative integer"
            )
        if not isinstance(value.occurred_at, datetime):
            raise InvalidCustomerEntry("occurred_at must be a datetime")
        if not isinstance(value.confirmed_at, datetime):
            raise InvalidCustomerEntry("confirmed_at must be a datetime")
        if value.confirmed_at < value.occurred_at:
            raise InvalidCustomerEntry(
                "confirmed_at must not precede occurred_at"
            )
        if (
            isinstance(value.schema_version, bool)
            or not isinstance(value.schema_version, int)
            or value.schema_version <= 0
        ):
            raise InvalidCustomerEntry(
                "schema_version must be a positive integer"
            )
        CustomerEntryVisit._confidence(
            value.entry_confidence,
            "entry_confidence",
            required=True,
        )
        CustomerEntryVisit._confidence(
            value.staff_confidence,
            "staff_confidence",
            required=False,
        )

    @staticmethod
    def _validate_identity_request(
        visit_id: str,
        identity_evidence_id: str,
        *,
        member_id: Optional[str],
        confidence: Optional[float],
    ) -> None:
        for value, name in (
            (visit_id, "visit_id"),
            (identity_evidence_id, "identity_evidence_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if member_id is not None and (
            not isinstance(member_id, str) or not member_id.strip()
        ):
            raise ValueError(
                "member_id must be a non-empty string when provided"
            )
        CustomerEntryVisit._confidence(
            confidence,
            "confidence",
            required=False,
        )

    @staticmethod
    def _confidence(value: object, name: str, *, required: bool) -> None:
        if value is None and not required:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidCustomerEntry(
                f"{name} must be a number between 0 and 1"
            )
        if not 0 <= value <= 1:
            raise InvalidCustomerEntry(f"{name} must be between 0 and 1")

    @staticmethod
    def _validate_replay(
        stored: CustomerEntryConfirmedEvent,
        incoming: CustomerEntryObservation,
    ) -> None:
        immutable_values = {
            "store_id": incoming.store_id,
            "camera_id": incoming.camera_id,
            "track_token": incoming.track_token,
            "direction": incoming.direction,
            "occurred_at": incoming.occurred_at,
        }
        conflicts = [
            name
            for name, expected in immutable_values.items()
            if getattr(stored, name) != expected
        ]
        if conflicts:
            raise CustomerEntryConflict(
                "dedup_key conflicts with immutable entry fields: "
                + ", ".join(conflicts)
            )

    @staticmethod
    def _result(visit: Visit) -> CustomerVisitResult:
        if visit.entry_event_id is None:
            raise CustomerEntryVisitError(
                "customer entry Visit has no entry_event_id"
            )
        return CustomerVisitResult(
            visit_id=visit.id,
            entry_event_id=visit.entry_event_id,
            member_id=visit.member_id,
            visitor_type=visit.visitor_type,
            identity_status=visit.identity_status,
            identity_source=visit.identity_source,
            visit_source=visit.visit_source,
            verification_status=visit.verification_status,
        )
