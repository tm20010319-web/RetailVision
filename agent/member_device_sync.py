"""Explicit-consent member synchronization with a camera face group."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Callable, Optional

from agent.camera_face_api import CameraApiError, CameraOperationResult, CameraFaceApiClient
from agent.device_identity import DeviceIdentityRepository, DeviceMemberMappingData


logger = logging.getLogger(__name__)


class MemberDeviceSyncError(RuntimeError):
    """A sanitized synchronization failure safe for business logs."""


def _opaque_alias(member_id: str) -> str:
    digest = hashlib.sha256(member_id.encode("utf-8")).hexdigest()[:20]
    return f"m-{digest}"


class MemberDeviceSyncService:
    """Manage authorized photos without generating or storing face vectors."""

    def __init__(
        self,
        *,
        client: CameraFaceApiClient,
        repository: DeviceIdentityRepository,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.client = client
        self.repository = repository
        self.clock = clock

    def authorize_member(
        self,
        *,
        member_id: str,
        photo_bytes: bytes,
        declared_sex: str,
        group_id: int,
        group_uuid: str,
        alias: Optional[str] = None,
        face_uuid: Optional[str] = None,
    ) -> DeviceMemberMappingData:
        if declared_sex not in {"male", "female"}:
            raise ValueError("declared_sex must be supplied by the member")
        if not photo_bytes:
            raise ValueError("an explicitly authorized member photo is required")

        existing = self.repository.get_mapping(self.client.device_id, member_id)
        resolved_alias = alias or _opaque_alias(member_id)
        resolved_face_uuid = (
            face_uuid
            or (existing.face_uuid if existing is not None else None)
            or uuid.uuid4().hex
        )
        pending = DeviceMemberMappingData(
            device_id=self.client.device_id,
            member_id=member_id,
            group_id=group_id,
            group_uuid=group_uuid,
            person_id=existing.person_id if existing else None,
            unique_id=existing.unique_id if existing else "",
            face_uuid=resolved_face_uuid,
            sync_status="pending",
            is_enabled=False,
            member_active=existing.member_active if existing else True,
        )
        self.repository.save_mapping(pending)

        try:
            self.client.update_person_and_face_image(
                alias=resolved_alias,
                sex=declared_sex,
                group_uuid=group_uuid,
                face_uuid=resolved_face_uuid,
                image_bytes=photo_bytes,
                system_time=self.clock(),
            )
            people = self.client.query_person_info(
                group_id=group_id,
                alias=resolved_alias,
                page_size=100,
                page_num=1,
            )
        except CameraApiError as exc:
            pending.sync_status = "failed"
            pending.last_error = "device_request_failed"
            self.repository.save_mapping(pending)
            logger.warning(
                "Device member synchronization failed: device_id=%s error_type=%s "
                "message=%s retryable=%s",
                self.client.device_id,
                type(exc).__name__,
                str(exc),
                exc.retryable,
            )
            raise MemberDeviceSyncError("member device synchronization failed") from exc

        confirmed = next(
            (
                person
                for person in people
                if person.get("alias") == resolved_alias
                and (
                    not person.get("face_uuid")
                    or person.get("face_uuid") == resolved_face_uuid
                )
            ),
            None,
        )
        if confirmed is None or str(confirmed.get("model_state", "")).lower() != "success":
            pending.sync_status = "failed"
            pending.last_error = "model_not_ready"
            self.repository.save_mapping(pending)
            raise MemberDeviceSyncError("device face model was not confirmed")

        pending.person_id = int(confirmed["person_id"])
        pending.unique_id = str(confirmed.get("unique_id") or resolved_face_uuid)
        pending.sync_status = "synced"
        pending.is_enabled = True
        pending.last_sync_at = self.clock()
        pending.last_error = None
        self.repository.save_mapping(pending)
        return pending

    def revoke_member(self, member_id: str) -> CameraOperationResult:
        mapping = self.repository.get_mapping(self.client.device_id, member_id)
        if mapping is None:
            raise MemberDeviceSyncError("member has no mapping on this device")

        # Local authorization is disabled before any network operation so a
        # device outage can never leave the member recognized by this service.
        mapping.sync_status = "disabled"
        mapping.is_enabled = False
        mapping.last_error = None
        self.repository.save_mapping(mapping)
        try:
            result = self.client.delete_person(
                group_uuid=mapping.group_uuid,
                face_uuid=mapping.face_uuid,
                delete_picture=True,
                system_time=self.clock(),
            )
        except CameraApiError as exc:
            mapping.last_error = "device_delete_failed"
            self.repository.save_mapping(mapping)
            logger.warning(
                "Device member deletion failed: device_id=%s error_type=%s "
                "message=%s retryable=%s",
                self.client.device_id,
                type(exc).__name__,
                str(exc),
                exc.retryable,
            )
            raise MemberDeviceSyncError("device member deletion failed") from exc

        # "revoked" means the API accepted the removal. The vendor documents
        # IsDeletePicture=true as moving the image to an unbound group; it does
        # not assert physical erasure.
        mapping.sync_status = "revoked"
        mapping.last_sync_at = self.clock()
        self.repository.save_mapping(mapping)
        return result
