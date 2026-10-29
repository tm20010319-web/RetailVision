"""
推荐引擎：基于会员历史和当前场景生成推荐，并发放优惠券。
"""

import logging
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

from agent.models import (
    Member,
    Coupon,
    RecommendationLog,
    PurchaseRecord,
    MemberPreference,
)

logger = logging.getLogger(__name__)


class RecommendationEngine:
    """推荐引擎"""
    
    def __init__(self, db_session: Session):
        self.db = db_session
    
    def generate_recommendations(
        self,
        member_id: str,
        current_zone: Optional[str] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        根据会员偏好和当前区域生成推荐商品列表
        
        Returns:
            推荐商品列表 [{"product_name": str, "category": str, "reason": str}, ...]
        """
        recommendations = []
        
        # 1. 获取会员偏好
        pref = self.db.query(MemberPreference).filter(
            MemberPreference.member_id == member_id
        ).first()
        
        # 2. 获取会员历史消费
        recent_purchases = (
            self.db.query(PurchaseRecord)
            .filter(
                PurchaseRecord.member_id == member_id,
                PurchaseRecord.created_at >= datetime.now() - timedelta(days=30),
            )
            .order_by(PurchaseRecord.created_at.desc())
            .limit(10)
            .all()
        )
        
        # 3. 基于偏好生成推荐
        favorite_categories = pref.favorite_categories if pref and pref.favorite_categories else []
        favorite_zones = pref.favorite_zones if pref and pref.favorite_zones else []
        
        # 如果当前区域在偏好中，优先推荐该区域商品
        if current_zone and current_zone in favorite_zones:
            recommendations.append({
                "product_name": f"{current_zone}区域畅销商品",
                "category": "热销",
                "reason": f"您常逛{current_zone}区域，为您推荐",
            })
        
        # 基于品类偏好推荐
        for category in favorite_categories[:3]:
            recommendations.append({
                "product_name": f"{category}新品推荐",
                "category": category,
                "reason": "根据您的购买偏好推荐",
            })
        
        # 4. 如果推荐不足，补充通用推荐
        if len(recommendations) < 3:
            general_products = ["当季热销商品", "会员专享商品", "限时特惠商品"]
            for product in general_products[:limit - len(recommendations)]:
                recommendations.append({
                    "product_name": product,
                    "category": "通用",
                    "reason": "为您推荐的热门商品",
                })
        
        return recommendations[:limit]
    
    def issue_coupon(
        self,
        member_id: str,
        coupon_type: str,
        description: Optional[str] = None,
        discount_value: float = 0,
        min_spend: float = 0,
        expires_days: int = 30,
    ) -> Optional[Dict[str, Any]]:
        """
        发放优惠券
        
        Args:
            member_id: 会员ID
            coupon_type: 优惠券类型
            description: 描述
            discount_value: 优惠金额或折扣
            min_spend: 最低消费金额
            expires_days: 有效期（天）
        
        Returns:
            优惠券信息或None
        """
        try:
            coupon = Coupon(
                member_id=member_id,
                coupon_type=coupon_type,
                description=description or f"{coupon_type}优惠券",
                discount_value=discount_value,
                min_spend=min_spend,
                issued_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=expires_days),
                used=False,
            )
            self.db.add(coupon)
            self.db.commit()
            
            logger.info(f"Coupon {coupon_type} issued to {member_id}")
            return {
                "id": coupon.id,
                "type": coupon.coupon_type,
                "description": coupon.description,
                "discount_value": float(coupon.discount_value),
                "min_spend": float(coupon.min_spend),
                "expires_at": coupon.expires_at.isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to issue coupon: {e}")
            self.db.rollback()
            return None
    
    def process_member_entry(
        self,
        member_id: str,
        zone: str,
        confidence: float = 0.0,
    ) -> Dict[str, Any]:
        """
        当会员进店时执行完整流程：
        1. 记录到店
        2. 生成推荐
        3. 根据条件发放优惠券
        """
        result = {
            "member_id": member_id,
            "zone": zone,
            "recommendations": [],
            "coupons": [],
            "message": "",
        }
        
        try:
            # 1. 获取会员信息
            member = self.db.query(Member).filter(Member.id == member_id).first()
            if not member:
                result["message"] = "会员不存在"
                return result
            
            # 2. 生成推荐
            recommendations = self.generate_recommendations(member_id, zone)
            result["recommendations"] = recommendations
            
            # 3. 根据会员等级发放优惠券
            coupons_issued = []
            
            # 金卡会员：发放8折券
            if member.tier == "gold":
                coupon = self.issue_coupon(
                    member_id=member_id,
                    coupon_type="gold_discount_20%",
                    description="金卡会员专属8折券",
                    discount_value=20,
                    min_spend=100,
                    expires_days=30,
                )
                if coupon:
                    coupons_issued.append(coupon)
            
            # 银卡会员：发放9折券
            elif member.tier == "silver":
                coupon = self.issue_coupon(
                    member_id=member_id,
                    coupon_type="silver_discount_10%",
                    description="银卡会员专属9折券",
                    discount_value=10,
                    min_spend=50,
                    expires_days=15,
                )
                if coupon:
                    coupons_issued.append(coupon)
            
            # 首次到店会员（或长时间未到店）：发放欢迎券
            if member.visit_count is None or member.visit_count < 3:
                coupon = self.issue_coupon(
                    member_id=member_id,
                    coupon_type="welcome_coupon",
                    description="新会员欢迎券",
                    discount_value=10,
                    min_spend=20,
                    expires_days=7,
                )
                if coupon:
                    coupons_issued.append(coupon)
            
            result["coupons"] = coupons_issued
            
            # 4. 记录推荐日志
            log = RecommendationLog(
                member_id=member_id,
                zone=zone,
                recommendations=[r["product_name"] for r in recommendations],
                coupons_given=[c["type"] for c in coupons_issued],
            )
            self.db.add(log)
            self.db.commit()
            
            result["message"] = f"欢迎会员 {member.name}，已生成推荐和优惠券"
            
        except Exception as e:
            logger.error(f"Process member entry error: {e}")
            result["message"] = f"处理失败: {str(e)}"
            self.db.rollback()
        
        return result
    
    def get_available_coupons(self, member_id: str) -> List[Dict[str, Any]]:
        """获取会员可用的优惠券"""
        now = datetime.now()
        coupons = (
            self.db.query(Coupon)
            .filter(
                Coupon.member_id == member_id,
                Coupon.used == False,
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
    
    def use_coupon(self, coupon_id: str) -> bool:
        """使用优惠券"""
        try:
            coupon = self.db.query(Coupon).filter(Coupon.id == coupon_id).first()
            if not coupon or coupon.used:
                return False
            coupon.used = True
            coupon.used_at = datetime.now()
            self.db.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to use coupon: {e}")
            self.db.rollback()
            return False