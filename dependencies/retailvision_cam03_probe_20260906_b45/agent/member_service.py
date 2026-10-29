"""
会员数据服务：查询消费记录、偏好、积分等。
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from agent.models import (
    Member,
    PurchaseRecord,
    RechargeRecord,
    MemberPreference,
    Visit,
)

logger = logging.getLogger(__name__)


class MemberService:
    """会员数据服务"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def get_member_by_id(self, member_id: str) -> Optional[Dict[str, Any]]:
        """根据ID获取会员信息"""
        member = self.db.query(Member).filter(Member.id == member_id).first()
        if not member:
            return None
        return {
            "id": member.id,
            "name": member.name,
            "phone": member.phone,
            "tier": member.tier,
            "points": member.points,
            "total_spent": float(member.total_spent) if member.total_spent else 0,
            "total_recharge": float(member.total_recharge) if member.total_recharge else 0,
            "join_date": member.join_date.isoformat() if member.join_date else None,
            "last_visit": member.last_visit.isoformat() if member.last_visit else None,
            "visit_count": member.visit_count or 0,
            "is_active": member.is_active,
            "profile": member.profile or {},
        }
    
    def get_member_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        """根据手机号获取会员信息"""
        member = self.db.query(Member).filter(Member.phone == phone).first()
        if not member:
            return None
        return self.get_member_by_id(member.id)
    
    def get_purchase_history(
        self,
        member_id: str,
        days: int = 30,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """获取会员消费记录"""
        since = datetime.now() - timedelta(days=days)
        records = (
            self.db.query(PurchaseRecord)
            .filter(
                PurchaseRecord.member_id == member_id,
                PurchaseRecord.created_at >= since,
            )
            .order_by(PurchaseRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "product_name": r.product_name,
                "category": r.category,
                "quantity": r.quantity,
                "unit_price": float(r.unit_price),
                "total_price": float(r.total_price),
                "discount": float(r.discount) if r.discount else 0,
                "zone": r.zone,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    
    def get_recharge_history(
        self,
        member_id: str,
        days: int = 90,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """获取会员充值记录"""
        since = datetime.now() - timedelta(days=days)
        records = (
            self.db.query(RechargeRecord)
            .filter(
                RechargeRecord.member_id == member_id,
                RechargeRecord.created_at >= since,
                RechargeRecord.status == "success",
            )
            .order_by(RechargeRecord.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "amount": float(r.amount),
                "method": r.method,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ]
    
    def get_preferences(self, member_id: str) -> Dict[str, Any]:
        """获取会员偏好"""
        pref = self.db.query(MemberPreference).filter(
            MemberPreference.member_id == member_id
        ).first()
        if not pref:
            return {}
        return {
            "favorite_zones": pref.favorite_zones or [],
            "favorite_categories": pref.favorite_categories or [],
            "avg_basket_amount": float(pref.avg_basket_amount) if pref.avg_basket_amount else 0,
            "avg_visit_duration": pref.avg_visit_duration,
            "preferred_visit_hours": pref.preferred_visit_hours or [],
            "last_updated": pref.last_updated.isoformat() if pref.last_updated else None,
        }
    
    def update_preferences(
        self,
        member_id: str,
        favorite_zones: Optional[List[str]] = None,
        favorite_categories: Optional[List[str]] = None,
        avg_basket_amount: Optional[float] = None,
        avg_visit_duration: Optional[int] = None,
        preferred_visit_hours: Optional[List[int]] = None,
    ) -> bool:
        """更新会员偏好"""
        try:
            pref = self.db.query(MemberPreference).filter(
                MemberPreference.member_id == member_id
            ).first()
            
            if not pref:
                pref = MemberPreference(member_id=member_id)
                self.db.add(pref)
            
            if favorite_zones is not None:
                pref.favorite_zones = favorite_zones
            if favorite_categories is not None:
                pref.favorite_categories = favorite_categories
            if avg_basket_amount is not None:
                pref.avg_basket_amount = avg_basket_amount
            if avg_visit_duration is not None:
                pref.avg_visit_duration = avg_visit_duration
            if preferred_visit_hours is not None:
                pref.preferred_visit_hours = preferred_visit_hours
            
            pref.last_updated = datetime.now()
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update preferences: {e}")
            self.db.rollback()
            return False
    
    def add_points(self, member_id: str, points: int, reason: str = "") -> bool:
        """增加会员积分"""
        try:
            member = self.db.query(Member).filter(Member.id == member_id).first()
            if not member:
                return False
            member.points = (member.points or 0) + points
            self.db.commit()
            logger.info(f"Added {points} points to {member_id}: {reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to add points: {e}")
            self.db.rollback()
            return False
    
    def record_visit(
        self, member_id: str, zone: str, confidence: float = 0.0, *,
        device_event_id: Optional[str] = None,
        entry_time: Optional[datetime] = None,
        camera_id: Optional[str] = None,
        track_id: Optional[int] = None,
        detection_bbox: Optional[List[int]] = None,
        identity_time: Optional[datetime] = None,
        commit: bool = True,
    ) -> Optional[str]:
        """Record one member visit, optionally within a caller-owned transaction."""
        if device_event_id is not None:
            existing = (
                self.db.query(Visit)
                .filter(Visit.device_event_id == device_event_id)
                .first()
            )
            if existing is not None:
                return existing.id

        visit_time = entry_time or datetime.now()

        def create_visit() -> str:
            visit = Visit(
                member_id=member_id,
                device_event_id=device_event_id,
                camera_id=camera_id,
                track_id=track_id,
                detection_bbox=detection_bbox,
                identity_time=identity_time,
                recognized_confidence=confidence,
                entry_time=visit_time,
                zones_visited=[zone],
                is_member=True,
                visitor_type="member",
                identity_status="identified_member",
                identity_source="legacy",
                identity_confidence=confidence,
                identified_at=identity_time,
                visit_source="legacy",
                verification_status="legacy",
                member_visit_counted_at=identity_time or visit_time,
            )
            self.db.add(visit)

            member = self.db.query(Member).filter(Member.id == member_id).first()
            if member:
                member.visit_count = (member.visit_count or 0) + 1
                if member.last_visit is None or member.last_visit < visit_time:
                    member.last_visit = visit_time

            if commit:
                self.db.commit()
            else:
                self.db.flush()
            return visit.id

        if not commit:
            return create_visit()

        try:
            return create_visit()
        except Exception as e:
            logger.error(f"Failed to record visit: {e}")
            self.db.rollback()
            return None
    
    def get_visit_history(self, member_id: str, days: int = 30) -> List[Dict[str, Any]]:
        """获取会员到店历史"""
        since = datetime.now() - timedelta(days=days)
        visits = (
            self.db.query(Visit)
            .filter(
                Visit.member_id == member_id,
                Visit.entry_time >= since,
            )
            .order_by(Visit.entry_time.desc())
            .limit(50)
            .all()
        )
        return [
            {
                "id": v.id,
                "entry_time": v.entry_time.isoformat() if v.entry_time else None,
                "exit_time": v.exit_time.isoformat() if v.exit_time else None,
                "total_duration_seconds": v.total_duration_seconds,
                "zones_visited": v.zones_visited or [],
                "confidence": float(v.recognized_confidence) if v.recognized_confidence else 0,
            }
            for v in visits
        ]
