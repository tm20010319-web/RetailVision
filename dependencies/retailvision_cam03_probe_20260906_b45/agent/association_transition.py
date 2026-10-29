"""Atomic transitions for the orthogonal recognition-inbox state axes."""

from __future__ import annotations

from datetime import datetime

from agent.models import DeviceRecognitionInbox


class RecognitionInboxTransitionService:
    """Keep routing status as a projection of the formal association state."""

    @staticmethod
    def waiting_member_mapping(row: DeviceRecognitionInbox, reason: str | None = None) -> None:
        row.status = "waiting_mapping"
        row.recognition_status = "pending_member_mapping"
        row.store_resolution_status = "missing"
        row.association_status = "pending"
        row.association_outcome = "pending"
        row.association_last_error = reason

    @staticmethod
    def waiting_store_mapping(row: DeviceRecognitionInbox, reason: str | None = None) -> None:
        row.status = "waiting_store_mapping"
        row.recognition_status = "accepted"
        row.store_resolution_status = "missing"
        row.association_status = "pending"
        row.association_outcome = "pending"
        row.association_last_error = reason

    @staticmethod
    def store_resolved(row: DeviceRecognitionInbox, store_id: str) -> None:
        if not store_id:
            raise ValueError("store_id is required")
        row.store_id = store_id
        row.status = "received"
        row.recognition_status = "accepted"
        row.store_resolution_status = "resolved"
        row.association_status = "pending"
        row.association_outcome = "pending"
        row.association_last_error = None

    @staticmethod
    def no_business_outcome(
        row: DeviceRecognitionInbox,
        *,
        recognition_status: str,
        store_resolution_status: str | None = None,
        reason: str,
    ) -> None:
        row.status = "processed"
        row.recognition_status = recognition_status
        if store_resolution_status is not None:
            row.store_resolution_status = store_resolution_status
        row.association_status = "terminal"
        row.association_outcome = "no_business_outcome"
        row.association_last_error = reason

    @staticmethod
    def associated(
        row: DeviceRecognitionInbox,
        *,
        visit_id: str,
        outcome: str,
        associated_at: datetime,
    ) -> None:
        if outcome not in {"new_member_arrival", "existing_visit_observation"}:
            raise ValueError("invalid association outcome")
        row.matched_visit_id = visit_id
        row.association_status = "matched"
        row.association_outcome = outcome
        row.associated_at = associated_at
