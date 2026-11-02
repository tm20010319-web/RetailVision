"""Member management orchestration: CRUD + device face registration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agent.member.member_device_sync import MemberDeviceSyncError, MemberDeviceSyncService
from agent.member.member_priority import TIER_RANKS
from agent.member.member_service import MemberService
from agent.models import (
    BusinessJob,
    Coupon,
    DeviceMemberMapping,
    Member,
    PurchaseRecord,
    Visit,
)

logger = logging.getLogger(__name__)

VALID_GENDERS = {"male", "female", "unknown"}


class MemberManagementError(RuntimeError):
    """Raised for member management failures safe for API responses."""


class MemberManagementService:
    """Orchestrate member records and device face synchronization."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        device_sync_service: Optional[MemberDeviceSyncService] = None,
        *,
        group_uuid: Optional[str] = None,
        group_id: Optional[int] = None,
        device_id: Optional[str] = None,
    ) -> None:
        self.session_factory = session_factory
        self.device_sync = device_sync_service
        self.group_uuid = group_uuid
        self.group_id = group_id
        self.device_id = device_id

    # ------------------------------------------------------------------
    # Member CRUD
    # ------------------------------------------------------------------

    def create_member(
        self,
        *,
        member_id: str,
        name: str,
        phone: Optional[str] = None,
        tier: str = "bronze",
        department: Optional[str] = None,
        position: Optional[str] = None,
        points: int = 0,
        gender: str = "unknown",
    ) -> Dict[str, Any]:
        """Create a Member record without device face registration."""
        if not member_id or not member_id.strip():
            raise MemberManagementError("member_id is required")
        if not name or not name.strip():
            raise MemberManagementError("name is required")
        if tier not in TIER_RANKS:
            raise MemberManagementError(
                "tier must be diamond, platinum, gold, silver, or bronze"
            )
        gender = self._normalize_gender(gender)

        db = self.session_factory()
        try:
            existing = db.query(Member).filter(Member.id == member_id).first()
            if existing is not None:
                raise MemberManagementError(f"member already exists: {member_id}")

            member = Member(
                id=member_id,
                name=name,
                phone=phone,
                tier=tier,
                department=department,
                position=position,
                points=points,
                gender=gender,
                is_active=True,
            )
            db.add(member)
            db.commit()
            db.refresh(member)
            logger.info("Created member %s (%s)", member_id, name)
            return self._member_dict(member, face_sync=None)
        except MemberManagementError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            raise MemberManagementError(f"member already exists: {member_id}")
        except Exception as exc:
            db.rollback()
            raise MemberManagementError(f"failed to create member: {exc}") from exc
        finally:
            db.close()

    def get_member(self, member_id: str) -> Optional[Dict[str, Any]]:
        """Return member info plus device face sync status."""
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == member_id).first()
            if member is None:
                return None
            face_sync = self._face_sync_status(db, member_id)
            return self._member_dict(member, face_sync=face_sync)
        finally:
            db.close()

    def get_member_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """Return member info by phone number."""
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.phone == phone).first()
            if member is None:
                return None
            face_sync = self._face_sync_status(db, member.id)
            return self._member_dict(member, face_sync=face_sync)
        finally:
            db.close()

    def list_members(
        self,
        *,
        tier: Optional[str] = None,
        page: int = 1,
        size: int = 20,
    ) -> Dict[str, Any]:
        """Paginated member listing with optional tier filter."""
        if page < 1:
            page = 1
        if size < 1 or size > 100:
            size = 20

        db = self.session_factory()
        try:
            query = db.query(Member).filter(Member.is_active.is_(True))
            if tier:
                query = query.filter(Member.tier == tier)
            total = query.count()
            members = (
                query.order_by(Member.created_at.desc())
                .offset((page - 1) * size)
                .limit(size)
                .all()
            )
            return {
                "total": total,
                "page": page,
                "size": size,
                "members": [
                    self._member_dict(m, face_sync=None) for m in members
                ],
            }
        finally:
            db.close()

    def update_member(
        self,
        member_id: str,
        *,
        name: Optional[str] = None,
        phone: Optional[str] = None,
        tier: Optional[str] = None,
        department: Optional[str] = None,
        position: Optional[str] = None,
        points: Optional[int] = None,
        gender: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update editable member fields."""
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == member_id).first()
            if member is None:
                raise MemberManagementError(f"member not found: {member_id}")

            if name is not None:
                member.name = name
            if phone is not None:
                member.phone = phone
            if tier is not None:
                if tier not in TIER_RANKS:
                    raise MemberManagementError(
                        "tier must be diamond, platinum, gold, silver, or bronze"
                    )
                member.tier = tier
            if department is not None:
                member.department = department
            if position is not None:
                member.position = position
            if points is not None:
                member.points = points
            if gender is not None:
                member.gender = self._normalize_gender(gender)

            db.commit()
            db.refresh(member)
            face_sync = self._face_sync_status(db, member_id)
            return self._member_dict(member, face_sync=face_sync)
        except MemberManagementError:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise MemberManagementError(f"failed to update member: {exc}") from exc
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Device face registration
    # ------------------------------------------------------------------

    def upload_face(
        self,
        member_id: str,
        photo_bytes: bytes,
        declared_sex: str,
    ) -> Dict[str, Any]:
        """Upload member photo to the device face group (step 2)."""
        if self.device_sync is None:
            raise MemberManagementError("device face registration is not configured")
        if not self.group_uuid:
            raise MemberManagementError("CAMERA_FACE_GROUP_UUID is not configured")
        if self.group_id is None or self.group_id < 0:
            raise MemberManagementError(
                "CAMERA_FACE_GROUP_ID must be configured as a non-negative integer"
            )
        if declared_sex not in ("male", "female"):
            raise MemberManagementError("declared_sex must be 'male' or 'female'")

        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == member_id).first()
            if member is None:
                raise MemberManagementError(f"member not found: {member_id}")
        finally:
            db.close()

        try:
            mapping = self.device_sync.authorize_member(
                member_id=member_id,
                photo_bytes=photo_bytes,
                declared_sex=declared_sex,
                group_id=self.group_id,
                group_uuid=self.group_uuid,
            )
        except MemberDeviceSyncError as exc:
            raise MemberManagementError(str(exc)) from exc

        return {
            "member_id": member_id,
            "sync_status": mapping.sync_status,
            "alias": f"m-{mapping.face_uuid[:8]}" if mapping.face_uuid else None,
            "person_id": mapping.person_id,
            "last_sync_at": mapping.last_sync_at.isoformat() if mapping.last_sync_at else None,
            "last_error": mapping.last_error,
        }

    def revoke_face(self, member_id: str) -> Dict[str, Any]:
        """Revoke device face authorization for a member."""
        if self.device_sync is None:
            raise MemberManagementError("device face registration is not configured")

        try:
            result = self.device_sync.revoke_member(member_id)
        except MemberDeviceSyncError as exc:
            raise MemberManagementError(str(exc)) from exc

        return {
            "member_id": member_id,
            "sync_status": "revoked",
            "device_status_code": result.status_code,
            "device_message": result.message,
        }

    def get_face_status(self, member_id: str) -> Dict[str, Any]:
        """Return the current device sync status for a member."""
        db = self.session_factory()
        try:
            face_sync = self._face_sync_status(db, member_id)
            if face_sync is None:
                return {"member_id": member_id, "sync_status": "not_uploaded"}
            return {"member_id": member_id, **face_sync}
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Business queries (delegate to MemberService)
    # ------------------------------------------------------------------

    def get_visits(self, member_id: str, days: int = 30) -> List[Dict[str, Any]]:
        db = self.session_factory()
        try:
            return MemberService(db).get_visit_history(member_id, days=days)
        finally:
            db.close()

    def get_purchases(self, member_id: str, days: int = 30) -> List[Dict[str, Any]]:
        db = self.session_factory()
        try:
            return MemberService(db).get_purchase_history(member_id, days=days)
        finally:
            db.close()

    def get_coupons(self, member_id: str) -> List[Dict[str, Any]]:
        db = self.session_factory()
        try:
            now = datetime.now()
            coupons = (
                db.query(Coupon)
                .filter(
                    Coupon.member_id == member_id,
                    Coupon.used.is_(False),
                    Coupon.expires_at >= now,
                )
                .order_by(Coupon.expires_at)
                .all()
            )
            return [
                {
                    "id": c.id,
                    "type": c.coupon_type,
                    "description": c.description,
                    "discount_value": float(c.discount_value),
                    "min_spend": float(c.min_spend),
                    "expires_at": c.expires_at.isoformat() if c.expires_at else None,
                }
                for c in coupons
            ]
        finally:
            db.close()

    def get_profile_status(self, member_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest durable customer-profile task and stored result."""
        db = self.session_factory()
        try:
            member = db.query(Member).filter(Member.id == member_id).first()
            if member is None:
                return None

            job = (
                db.query(BusinessJob)
                .filter(
                    BusinessJob.member_id == member_id,
                    BusinessJob.task_type == "customer_profile",
                )
                .order_by(BusinessJob.created_at.desc(), BusinessJob.id.desc())
                .first()
            )
            stored = member.profile if isinstance(member.profile, dict) else {}
            profile = stored.get("latest_customer_profile")
            profile_event_id = stored.get("latest_customer_profile_event_id")

            if job is None:
                return {
                    "member_id": member_id,
                    "status": "not_started",
                    "event_id": None,
                    "attempts": 0,
                    "max_attempts": 5,
                    "last_error": None,
                    "reason": None,
                    "profile_event_id": profile_event_id,
                    "profile": profile,
                    "updated_at": None,
                }

            result = job.result if isinstance(job.result, dict) else {}
            reason = result.get("reason") if job.status == "skipped" else None
            return {
                "member_id": member_id,
                "status": job.status,
                "event_id": job.event_id,
                "attempts": job.attempts,
                "max_attempts": job.max_attempts,
                "last_error": job.last_error,
                "reason": reason,
                "profile_event_id": profile_event_id,
                "profile": profile,
                "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            }
        finally:
            db.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _member_dict(
        member: Member,
        *,
        face_sync: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        gender = MemberManagementService._normalize_gender(member.gender)
        return {
            "id": member.id,
            "name": member.name,
            "gender": gender,
            "display_name": MemberManagementService._display_name(member.name, gender),
            "phone": member.phone,
            "tier": member.tier,
            "department": member.department,
            "position": member.position,
            "points": member.points,
            "total_spent": float(member.total_spent) if member.total_spent else 0,
            "total_recharge": float(member.total_recharge) if member.total_recharge else 0,
            "join_date": member.join_date.isoformat() if member.join_date else None,
            "last_visit": member.last_visit.isoformat() if member.last_visit else None,
            "visit_count": member.visit_count or 0,
            "is_active": member.is_active,
            "face_sync": face_sync or {"sync_status": "not_uploaded"},
        }

    @staticmethod
    def _normalize_gender(gender: Optional[str]) -> str:
        value = (gender or "unknown").strip().lower()
        if value not in VALID_GENDERS:
            raise MemberManagementError(
                "gender must be 'male', 'female', or 'unknown'"
            )
        return value

    @staticmethod
    def _display_name(name: str, gender: str) -> str:
        if name.endswith(("先生", "女士")):
            return name
        suffix = {"male": "先生", "female": "女士"}.get(gender, "")
        return f"{name}{suffix}"

    def _face_sync_status(
        self, db: Session, member_id: str
    ) -> Optional[Dict[str, Any]]:
        if self.device_id is None:
            return None
        mapping = (
            db.query(DeviceMemberMapping)
            .filter(
                DeviceMemberMapping.device_id == self.device_id,
                DeviceMemberMapping.member_id == member_id,
            )
            .first()
        )
        if mapping is None:
            return None
        return {
            "sync_status": mapping.sync_status,
            "person_id": mapping.person_id,
            "face_uuid": mapping.face_uuid,
            "last_sync_at": mapping.last_sync_at.isoformat() if mapping.last_sync_at else None,
            "last_error": mapping.last_error,
        }
