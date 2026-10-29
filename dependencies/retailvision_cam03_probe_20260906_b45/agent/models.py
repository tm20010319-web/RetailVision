"""
数据库模型：会员系统相关表
"""

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Boolean, 
    Text, JSON, LargeBinary, DECIMAL, Index, UniqueConstraint,
    ForeignKey, CheckConstraint, text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import datetime
import uuid

Base = declarative_base()


def new_uuid() -> str:
    return uuid.uuid4().hex


class Member(Base):
    """会员主表"""
    __tablename__ = "members"
    
    id = Column(String(64), primary_key=True)
    name = Column(String(128), nullable=False)
    gender = Column(String(16), nullable=False, default="unknown", server_default="unknown")
    phone = Column(String(20), unique=True, nullable=True)
    tier = Column(String(20), default="bronze")
    department = Column(String(128), nullable=True)
    position = Column(String(128), nullable=True)
    points = Column(Integer, default=0)
    total_spent = Column(DECIMAL(10, 2), default=0.00)
    total_recharge = Column(DECIMAL(10, 2), default=0.00)
    join_date = Column(DateTime, default=func.now())
    last_visit = Column(DateTime, nullable=True)
    visit_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    face_encoding = Column(LargeBinary, nullable=True)  # 人脸特征向量
    profile = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_members_phone", "phone"),
        Index("idx_members_tier", "tier"),
        Index("idx_members_last_visit", "last_visit"),
    )


class RechargeRecord(Base):
    """充值记录表"""
    __tablename__ = "recharge_records"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    method = Column(String(32))  # wechat, alipay, cash, card
    status = Column(String(20), default="success")
    remark = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_recharge_member_id", "member_id"),
        Index("idx_recharge_created_at", "created_at"),
    )


class PurchaseRecord(Base):
    """消费记录表"""
    __tablename__ = "purchase_records"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    order_id = Column(String(64), nullable=True)
    product_name = Column(String(256), nullable=True)
    category = Column(String(64), nullable=True)
    quantity = Column(Integer, default=1)
    unit_price = Column(DECIMAL(10, 2), default=0.00)
    total_price = Column(DECIMAL(10, 2), nullable=False)
    discount = Column(DECIMAL(10, 2), default=0.00)
    visit_id = Column(String(64), nullable=True)  # 关联到店记录
    zone = Column(String(64), nullable=True)  # 购买区域
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_purchase_member_id", "member_id"),
        Index("idx_purchase_created_at", "created_at"),
        Index("idx_purchase_category", "category"),
        Index("idx_purchase_zone", "zone"),
    )


class Visit(Base):
    """到店访问记录表"""
    __tablename__ = "visits"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    store_id = Column(String(64), nullable=True)
    entry_event_id = Column(
        String(64), ForeignKey("customer_entry_events.id"), nullable=True
    )
    device_event_id = Column(String(64), nullable=True)
    camera_id = Column(String(64), nullable=True)
    track_id = Column(Integer, nullable=True)
    detection_bbox = Column(JSON, nullable=True)
    identity_time = Column(DateTime, nullable=True)
    recognized_confidence = Column(DECIMAL(3, 2), nullable=True)
    entry_time = Column(DateTime, default=func.now())
    exit_time = Column(DateTime, nullable=True)
    total_duration_seconds = Column(Integer, nullable=True)
    path = Column(JSON, nullable=True)  # 移动轨迹
    zones_visited = Column(JSON, nullable=True)
    is_member = Column(Boolean, default=False)
    visitor_type = Column(String(24), nullable=True)
    identity_status = Column(String(32), nullable=True)
    identity_source = Column(String(32), nullable=True)
    identity_confidence = Column(Float, nullable=True)
    identified_at = Column(DateTime, nullable=True)
    visit_source = Column(String(32), nullable=True, default="legacy")
    verification_status = Column(String(24), nullable=True, default="legacy")
    member_visit_counted_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)
    closed_reason = Column(String(64), nullable=True)
    superseded_by_visit_id = Column(
        String(64), ForeignKey("visits.id"), nullable=True
    )
    superseded_at = Column(DateTime, nullable=True)
    superseded_reason = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_visits_member_id", "member_id"),
        Index("idx_visits_entry_time", "entry_time"),
        Index(
            "idx_visits_reusable_lookup",
            "store_id", "member_id", "superseded_by_visit_id",
            "exit_time", "last_seen_at", "entry_time",
        ),
        Index("idx_visits_store_entry_time", "store_id", "entry_time"),
        Index("idx_visits_superseded_by", "superseded_by_visit_id"),
        Index("uq_visits_device_event_id", "device_event_id", unique=True),
        Index("uq_visits_entry_event_id", "entry_event_id", unique=True),
    )


class ZoneStayEvent(Base):
    """区域停留事件表"""
    __tablename__ = "zone_stay_events"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=True)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    zone = Column(String(64), nullable=False)
    entered_at = Column(DateTime, nullable=False)
    exited_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    had_purchase = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_zone_stay_member_id", "member_id"),
        Index("idx_zone_stay_zone", "zone"),
        Index("idx_zone_stay_entered_at", "entered_at"),
    )


class CustomerEntryEvent(Base):
    """One durable inward crossing of the configured customer entrance."""

    __tablename__ = "customer_entry_events"

    id = Column(String(64), primary_key=True, default=new_uuid)
    entry_key = Column(String(192), nullable=False)
    dedup_key = Column(String(192), nullable=True)
    store_id = Column(String(64), nullable=True)
    camera_id = Column(String(64), nullable=False)
    track_id = Column(Integer, nullable=False)
    tracker_track_id = Column(Integer, nullable=True)
    track_token = Column(String(256), nullable=False)
    runtime_session_id = Column(String(64), nullable=True)
    direction = Column(String(32), nullable=True)
    occurred_at = Column(DateTime, nullable=False)
    confirmed_at = Column(DateTime, nullable=True)
    staff_status = Column(String(24), nullable=True)
    staff_source = Column(String(32), nullable=True)
    staff_confidence = Column(Float, nullable=True)
    schema_version = Column(Integer, nullable=True, default=1)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    member_tier = Column(String(20), nullable=True)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("uq_customer_entry_event_key", "entry_key", unique=True),
        Index("uq_customer_entry_dedup_key", "dedup_key", unique=True),
        Index("idx_customer_entry_time", "occurred_at"),
        Index("idx_customer_entry_camera_time", "camera_id", "occurred_at"),
        Index("idx_customer_entry_member", "member_id", "occurred_at"),
    )


# New domain name backed by the existing table during the compatibility phase.
CustomerEntryConfirmedEvent = CustomerEntryEvent


class BehaviorEvent(Base):
    """Reviewable greeting, dwell, or reach assessment for one customer track."""

    __tablename__ = "behavior_events"

    id = Column(String(64), primary_key=True, default=new_uuid)
    event_key = Column(String(192), nullable=False)
    event_type = Column(String(32), nullable=False)
    assessment = Column(String(24), nullable=False)
    camera_id = Column(String(64), nullable=False)
    customer_track_id = Column(Integer, nullable=False)
    customer_track_token = Column(String(128), nullable=False)
    track_token = Column(String(256), nullable=True)
    entry_event_id = Column(
        String(64), ForeignKey("customer_entry_events.id"), nullable=True
    )
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=True)
    store_id = Column(String(64), nullable=True)
    is_demo = Column(Boolean, nullable=False, default=False)
    staff_track_id = Column(Integer, nullable=True)
    staff_track_token = Column(String(128), nullable=True)
    # A configured demo label today; a verified staff identity source later.
    staff_name = Column(String(128), nullable=True)
    staff_attribution_source = Column(String(32), nullable=True)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    member_tier = Column(String(20), nullable=True)
    identity_status = Column(String(24), nullable=True)
    identity_source = Column(String(32), nullable=True)
    identity_confidence = Column(Float, nullable=True)
    identity_binding_level = Column(String(24), nullable=True)
    binding_id = Column(String(64), ForeignKey("track_identity_bindings.id"), nullable=True)
    backfilled_at = Column(DateTime, nullable=True)
    occurred_at = Column(DateTime, nullable=True)
    evaluation_id = Column(String(64), nullable=True)
    policy_version_id = Column(String(64), nullable=True)
    algorithm_version = Column(String(64), nullable=True)
    parameter_version = Column(String(64), nullable=True)
    area_id = Column(String(64), nullable=True)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    duration_seconds = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    evidence_clip_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("uq_behavior_event_key", "event_key", unique=True),
        Index("idx_behavior_event_type_time", "event_type", "started_at"),
        Index("idx_behavior_event_camera_time", "camera_id", "started_at"),
        Index("idx_behavior_event_member_time", "member_id", "started_at"),
    )


class GreetingCycle(Base):
    """One entry's V1-authoritative lifecycle with versioned evaluations."""

    __tablename__ = "greeting_cycles"

    id = Column(String(64), primary_key=True, default=new_uuid)
    entry_event_id = Column(
        String(64), ForeignKey("customer_entry_events.id"), nullable=False
    )
    entry_key = Column(String(192), nullable=False)
    store_id = Column(String(64), nullable=True)
    camera_id = Column(String(64), nullable=False)
    runtime_session_id = Column(String(64), nullable=True)
    customer_track_id = Column(Integer, nullable=False)
    customer_track_token = Column(String(256), nullable=False)
    cycle_created_at = Column(DateTime, nullable=False)
    waiting_started_at = Column(DateTime, nullable=True)
    settled_at = Column(DateTime, nullable=True)
    outcome = Column(String(24), nullable=True)
    cancel_reason = Column(String(128), nullable=True)
    official_behavior_event_id = Column(
        String(64), ForeignKey("behavior_events.id"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("entry_event_id", name="uq_greeting_cycle_entry_event"),
        Index("idx_greeting_cycle_camera_created", "camera_id", "cycle_created_at"),
        Index("idx_greeting_cycle_outcome", "outcome", "settled_at"),
    )


class GreetingEvaluation(Base):
    """Versioned official or Shadow assessment linked to a Greeting Cycle."""

    __tablename__ = "greeting_evaluations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    greeting_cycle_id = Column(
        String(64), ForeignKey("greeting_cycles.id"), nullable=False
    )
    evaluator_version = Column(String(64), nullable=False)
    mode = Column(String(16), nullable=False)
    candidate_path = Column(String(32), nullable=True)
    decision = Column(String(24), nullable=False)
    interaction_score = Column(Float, nullable=True)
    track_quality_score = Column(Float, nullable=True)
    raw_metrics = Column(JSON, nullable=False, default=dict)
    score_breakdown = Column(JSON, nullable=False, default=dict)
    calibration_mode = Column(String(32), nullable=True)
    calibration_consistency = Column(String(32), nullable=True)
    waiting_started_at = Column(DateTime, nullable=True)
    response_seconds = Column(Float, nullable=True)
    settled_at = Column(DateTime, nullable=False)
    reason = Column(String(128), nullable=True)
    candidate_threshold = Column(Float, nullable=True)
    confirm_threshold = Column(Float, nullable=True)
    staff_track_id = Column(Integer, nullable=True)
    staff_track_token = Column(String(256), nullable=True)
    evidence_clip_id = Column(String(64), ForeignKey("evidence_clips.id"), nullable=True)
    official_behavior_event_id = Column(
        String(64), ForeignKey("behavior_events.id"), nullable=True
    )
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "greeting_cycle_id",
            "evaluator_version",
            "mode",
            name="uq_greeting_evaluation_version_mode",
        ),
        Index("idx_greeting_evaluation_mode_settled", "mode", "settled_at"),
        Index("idx_greeting_evaluation_decision", "decision", "settled_at"),
    )


class EvidenceClip(Base):
    """Asynchronous request for a precise interval from device recordings."""

    __tablename__ = "evidence_clips"

    id = Column(String(64), primary_key=True, default=new_uuid)
    owner_type = Column(String(32), nullable=False)
    owner_id = Column(String(128), nullable=False)
    camera_id = Column(String(64), nullable=False)
    requested_start = Column(DateTime, nullable=False)
    requested_end = Column(DateTime, nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    output_path = Column(String(512), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(String(256), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_evidence_clip_owner", "owner_type", "owner_id", unique=True),
        Index("idx_evidence_clip_status_created", "status", "created_at"),
        Index("idx_evidence_clip_camera_time", "camera_id", "requested_start"),
    )


class MemberPreference(Base):
    """会员偏好表"""
    __tablename__ = "member_preferences"
    
    member_id = Column(String(64), ForeignKey("members.id"), primary_key=True)
    favorite_zones = Column(JSON, nullable=True)
    favorite_categories = Column(JSON, nullable=True)
    avg_basket_amount = Column(DECIMAL(10, 2), nullable=True)
    avg_visit_duration = Column(Integer, nullable=True)
    preferred_visit_hours = Column(JSON, nullable=True)
    last_updated = Column(DateTime, default=func.now(), onupdate=func.now())


class Coupon(Base):
    """优惠券表"""
    __tablename__ = "coupons"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    coupon_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=True)
    discount_value = Column(DECIMAL(10, 2), default=0.00)
    min_spend = Column(DECIMAL(10, 2), default=0.00)
    issued_at = Column(DateTime, default=func.now())
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    used = Column(Boolean, default=False)
    trigger_event = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_coupons_member_id", "member_id"),
        Index("idx_coupons_used", "used"),
        Index("idx_coupons_expires_at", "expires_at"),
    )


class RecommendationLog(Base):
    """推荐日志表"""
    __tablename__ = "recommendation_logs"
    
    id = Column(String(64), primary_key=True, default=new_uuid)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    zone = Column(String(64), nullable=True)
    recommendations = Column(JSON, nullable=True)
    coupons_given = Column(JSON, nullable=True)
    accepted = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_recommendation_member_id", "member_id"),
        Index("idx_recommendation_created_at", "created_at"),
    )


class DeviceMemberMapping(Base):
    """Authorized member identity maintained independently for each device."""

    __tablename__ = "device_member_mappings"

    id = Column(String(160), primary_key=True)
    device_id = Column(String(64), nullable=False)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    group_id = Column(Integer, nullable=True)
    group_uuid = Column(String(64), nullable=False)
    person_id = Column(Integer, nullable=True)
    unique_id = Column(String(64), nullable=False)
    face_uuid = Column(String(64), nullable=False)
    sync_status = Column(String(24), nullable=False, default="pending")
    is_enabled = Column(Boolean, nullable=False, default=True)
    last_sync_at = Column(DateTime, nullable=True)
    last_error = Column(String(256), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_device_member_mapping_device", "device_id"),
        Index("idx_device_member_mapping_member", "member_id"),
        Index(
            "idx_device_member_mapping_identity",
            "device_id",
            "group_id",
            "person_id",
            "unique_id",
            unique=True,
        ),
    )


class OfflineBehaviorPublication(Base):
    """Immutable Job-level fence for one Offline behavior fact batch."""

    __tablename__ = "offline_behavior_publications"

    analysis_job_id = Column(String(128), primary_key=True)
    published_analysis_run_id = Column(String(128), nullable=False)
    source_video_id = Column(String(192), nullable=False)
    event_count = Column(Integer, nullable=False)
    event_manifest_hash = Column(String(96), nullable=False)
    published_at = Column(DateTime, nullable=False)

    __table_args__ = (
        CheckConstraint("event_count >= 0", name="ck_offline_publication_event_count"),
    )


class ProductInteractionFact(Base):
    """Anonymous, offline-normalized product interaction fact for analytics."""

    __tablename__ = "product_interaction_facts"

    id = Column(String(64), primary_key=True, default=new_uuid)
    analysis_job_id = Column(String(128), nullable=False)
    analysis_run_id = Column(String(128), nullable=False)
    source_video_id = Column(String(192), nullable=True)
    store_id = Column(String(64), nullable=False)
    camera_id = Column(String(64), nullable=False)
    product_key = Column(String(128), nullable=False)
    product_name = Column(String(256), nullable=True)
    interaction_session_id = Column(String(192), nullable=False)
    event_type = Column(String(32), nullable=False)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    holding_duration_seconds = Column(Float, nullable=True)
    track_key_hash = Column(String(64), nullable=False)
    normalized_fact_ref = Column(String(96), nullable=True)
    artifact_manifest_hash = Column(String(96), nullable=True)
    projection_version = Column(String(64), nullable=False, default="product_interaction_projection_v1", server_default="product_interaction_projection_v1")
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "analysis_run_id",
            "interaction_session_id",
            "event_type",
            name="uq_product_interaction_fact_run_session_type",
        ),
        Index(
            "idx_product_interaction_fact_scope_time",
            "store_id",
            "camera_id",
            "started_at",
        ),
        Index(
            "idx_product_interaction_fact_product_time",
            "product_key",
            "started_at",
        ),
        Index(
            "idx_product_interaction_fact_fact_ref",
            "analysis_run_id",
            "normalized_fact_ref",
        ),
    )

class DeviceMemberIdentityRevision(Base):
    """Immutable Event-Time history for the Offline identity capability."""

    __tablename__ = "device_member_identity_revisions"

    id = Column(String(64), primary_key=True, default=new_uuid)
    device_id = Column(String(64), nullable=False)
    group_id = Column(Integer, nullable=True)
    person_id = Column(Integer, nullable=True)
    unique_id = Column(String(64), nullable=False, default="")
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime, nullable=True)
    mapping_enabled = Column(Boolean, nullable=False)
    mapping_sync_status = Column(String(24), nullable=False)
    member_principal_exists = Column(Boolean, nullable=False)
    member_principal_active = Column(Boolean, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index(
            "idx_device_member_revision_identity_time",
            "device_id", "group_id", "person_id", "unique_id", "effective_from",
        ),
        Index(
            "idx_device_member_revision_member_time",
            "member_id", "effective_from",
        ),
    )


class DeviceRecognitionCursor(Base):
    """Successful poll cursor; advanced only after all result pages complete."""

    __tablename__ = "device_recognition_cursors"

    device_id = Column(String(64), primary_key=True)
    last_log_id = Column(Integer, nullable=True)
    last_record_time = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)


class DeviceRecognitionInbox(Base):
    """Durable, sanitized recognition events awaiting unified processing."""

    __tablename__ = "device_recognition_inbox"

    canonical_event_id = Column(String(64), primary_key=True)
    device_id = Column(String(64), nullable=False)
    source = Column(String(16), nullable=False)
    source_event_id = Column(String(128), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    device_occurred_at_raw = Column(String(128), nullable=True)
    device_utc_offset_minutes = Column(Integer, nullable=True)
    receiver_arrived_at = Column(DateTime, nullable=True)
    future_skew_seconds = Column(Float, nullable=True)
    store_id = Column(String(64), nullable=True)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=True)
    group_id = Column(Integer, nullable=True)
    person_id = Column(Integer, nullable=True)
    unique_id = Column(String(64), nullable=False, default="")
    similarity = Column(Float, nullable=True)
    rule_id = Column(Integer, nullable=True)
    capture_path = Column(String(512), nullable=True)
    background_capture_path = Column(String(512), nullable=True)
    background_capture_sha256 = Column(String(64), nullable=True)
    background_capture_width = Column(Integer, nullable=True)
    background_capture_height = Column(Integer, nullable=True)
    face_spatial_evidence = Column(JSON, nullable=True)
    device_metadata = Column(JSON, nullable=True)
    status = Column(String(24), nullable=False, default="received")
    last_error = Column(String(512), nullable=True)
    received_at = Column(DateTime, nullable=False, default=func.now())
    processed_at = Column(DateTime, nullable=True)
    locked_at = Column(DateTime, nullable=True)
    claim_token = Column(String(36), nullable=True)
    association_status = Column(String(24), nullable=True, default="pending")
    recognition_status = Column(
        String(32), nullable=False, default="pending_member_mapping"
    )
    store_resolution_status = Column(
        String(24), nullable=False, default="missing"
    )
    track_association_status = Column(
        String(32), nullable=False, default="pending"
    )
    runtime_instance_id = Column(String(64), nullable=True)
    evaluation_id = Column(String(64), nullable=True)
    finalization_job_id = Column(String(64), nullable=True)
    binding_id = Column(String(64), nullable=True)
    track_association_reason = Column(String(256), nullable=True)
    association_outcome = Column(
        String(32), nullable=False, default="pending"
    )
    matched_entry_event_id = Column(
        String(64), ForeignKey("customer_entry_events.id"), nullable=True
    )
    matched_visit_id = Column(String(64), ForeignKey("visits.id"), nullable=True)
    matched_track_token = Column(String(256), nullable=True)
    match_reason = Column(String(128), nullable=True)
    match_confidence = Column(Float, nullable=True)
    associated_at = Column(DateTime, nullable=True)
    association_claim_token = Column(String(64), nullable=True)
    association_locked_at = Column(DateTime, nullable=True)
    association_claim_expires_at = Column(DateTime, nullable=True)
    association_attempts = Column(Integer, nullable=False, default=0)
    association_next_retry_at = Column(DateTime, nullable=True)
    association_deadline_at = Column(DateTime, nullable=True)
    association_last_error = Column(String(512), nullable=True)
    store_mapping_claim_token = Column(String(64), nullable=True)
    store_mapping_locked_at = Column(DateTime, nullable=True)
    store_mapping_claim_expires_at = Column(DateTime, nullable=True)
    store_mapping_attempts = Column(Integer, nullable=False, default=0)
    store_mapping_last_attempt_at = Column(DateTime, nullable=True)
    store_mapping_next_retry_at = Column(DateTime, nullable=True)
    store_mapping_wait_deadline_at = Column(DateTime, nullable=True)
    store_mapping_last_error = Column(String(512), nullable=True)
    realtime_effect_status = Column(String(24), nullable=True)
    realtime_effect_reason = Column(String(128), nullable=True)
    projection_pending = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("idx_device_recognition_inbox_device_time", "device_id", "occurred_at"),
        Index(
            "uq_device_recognition_inbox_source_event",
            "device_id",
            "source",
            "source_event_id",
            unique=True,
        ),
        Index("idx_device_recognition_inbox_status_received", "status", "received_at"),
        Index("idx_identity_evidence_association", "association_status", "occurred_at"),
    )


IdentityEvidence = DeviceRecognitionInbox


class DeviceHealthState(Base):
    """Independent health observations for device push and poll paths."""

    __tablename__ = "device_health_states"

    device_id = Column(String(64), primary_key=True)
    push_healthy = Column(Boolean, nullable=False, default=False)
    poll_healthy = Column(Boolean, nullable=False, default=False)
    last_heartbeat_at = Column(DateTime, nullable=True)
    last_poll_success_at = Column(DateTime, nullable=True)
    last_push_error = Column(String(512), nullable=True)
    last_poll_error = Column(String(512), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class PushCoverageObservation(Base):
    """Immutable historical observations for the Push Recognition capture path."""

    __tablename__ = "push_coverage_observations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    observation_key = Column(String(256), nullable=False, unique=True)
    device_id = Column(String(64), nullable=False)
    observation_type = Column(String(32), nullable=False)
    observed_at = Column(DateTime, nullable=False)
    device_event_at = Column(DateTime, nullable=True)
    receiver_instance_id = Column(String(64), nullable=False)
    boot_id = Column(String(64), nullable=False)
    source_event_id = Column(String(128), nullable=True)
    canonical_event_id = Column(String(64), nullable=True)
    outcome = Column(String(24), nullable=False)
    failure_class = Column(String(64), nullable=True)
    policy_version = Column(String(64), nullable=False)
    device_metadata = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index(
            "idx_push_coverage_observation_device_time",
            "device_id", "observed_at",
        ),
        Index(
            "idx_push_coverage_observation_instance_time",
            "receiver_instance_id", "observed_at",
        ),
        Index(
            "idx_push_coverage_observation_type_time",
            "device_id", "observation_type", "observed_at",
        ),
    )


class MemberEntryEvent(Base):
    __tablename__ = "member_entry_events"

    event_id = Column(String(64), primary_key=True)
    device_id = Column(String(64), nullable=False)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    occurred_at = Column(DateTime, nullable=False)
    similarity = Column(Float, nullable=False)
    track_id = Column(Integer, nullable=True)
    bbox = Column(JSON, nullable=True)
    identity_source = Column(String(32), nullable=False, default="device_api")
    identity_time = Column(DateTime, nullable=False)
    status = Column(String(24), nullable=False, default="recognized")
    visit_id = Column(String(64), nullable=True)
    snapshot_path = Column(String(512), nullable=True)
    finalize_attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_member_entry_status_time", "status", "occurred_at"),
        Index("idx_member_entry_member", "member_id", "occurred_at"),
    )


class BusinessJob(Base):
    __tablename__ = "business_jobs"

    id = Column(String(64), primary_key=True, default=new_uuid)
    event_id = Column(String(64), nullable=False)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    task_type = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    available_at = Column(DateTime, nullable=False, default=func.now())
    locked_at = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_business_job_event_type", "event_id", "task_type", unique=True),
        Index("idx_business_job_due", "task_type", "status", "available_at"),
    )


class VisitEntryEvidence(Base):
    """One Customer Entry Event belongs to one final Visit."""

    __tablename__ = "visit_entry_evidence"

    id = Column(String(64), primary_key=True, default=new_uuid)
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=False)
    entry_event_id = Column(
        String(64), ForeignKey("customer_entry_events.id"), nullable=False
    )
    evidence_role = Column(String(16), nullable=False, default="initial")
    associated_at = Column(DateTime, nullable=False, default=func.now())
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index("uq_visit_entry_evidence_event", "entry_event_id", unique=True),
        Index(
            "uq_visit_entry_evidence_pair", "visit_id", "entry_event_id", unique=True
        ),
        Index("idx_visit_entry_evidence_visit", "visit_id"),
    )


class MemberArrival(Base):
    """The at-most-once Member Arrival fact for a Visit."""

    __tablename__ = "member_arrivals"

    id = Column(String(64), primary_key=True, default=new_uuid)
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=False)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    recognition_event_id = Column(
        String(64), ForeignKey("device_recognition_inbox.canonical_event_id"),
        nullable=True,
    )
    occurred_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
    arrival_source = Column(String(32), nullable=False, default="device_recognition")
    superseded_by_arrival_id = Column(
        String(64), ForeignKey("member_arrivals.id"), nullable=True
    )
    superseded_at = Column(DateTime, nullable=True)
    superseded_reason = Column(String(128), nullable=True)

    __table_args__ = (
        Index("uq_member_arrivals_visit", "visit_id", unique=True),
        Index("uq_member_arrivals_recognition", "recognition_event_id", unique=True),
        Index("idx_member_arrivals_member_time", "member_id", "occurred_at"),
        Index("idx_member_arrivals_superseded_by", "superseded_by_arrival_id"),
    )


class MemberArrivalEffect(Base):
    """Durable intent and execution ledger for one Arrival effect."""

    __tablename__ = "member_arrival_effects"

    id = Column(String(64), primary_key=True, default=new_uuid)
    member_arrival_id = Column(
        String(64), ForeignKey("member_arrivals.id"), nullable=False
    )
    effect_type = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    deadline_at = Column(DateTime, nullable=True)
    reason = Column(String(128), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    claim_token = Column(String(64), nullable=True)
    consumer_id = Column(String(128), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())
    superseded_by_effect_id = Column(
        String(64), ForeignKey("member_arrival_effects.id"), nullable=True
    )
    superseded_at = Column(DateTime, nullable=True)
    superseded_reason = Column(String(128), nullable=True)

    __table_args__ = (
        Index(
            "uq_member_arrival_effect_active",
            "member_arrival_id", "effect_type", unique=True,
            sqlite_where=text("superseded_by_effect_id IS NULL"),
        ),
        Index("idx_member_arrival_effect_claim", "status", "next_retry_at"),
        Index("idx_member_arrival_effect_superseded", "superseded_by_effect_id"),
    )


class DeviceStoreRegistration(Base):
    __tablename__ = "device_store_registrations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    device_id = Column(String(64), nullable=False)
    store_id = Column(String(64), nullable=False)
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_device_store_registration_lookup", "device_id", "effective_from", "effective_to"),
        Index("idx_device_store_registration_store", "store_id", "effective_from"),
    )


class CameraStoreRegistration(Base):
    __tablename__ = "camera_store_registrations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    camera_id = Column(String(64), nullable=False)
    store_id = Column(String(64), nullable=False)
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_camera_store_registration_lookup", "camera_id", "effective_from", "effective_to"),
        Index("idx_camera_store_registration_store", "store_id", "effective_from"),
    )


class DeviceCameraRegistration(Base):
    __tablename__ = "device_camera_registrations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    device_id = Column(String(64), nullable=False)
    camera_id = Column(String(64), nullable=False)
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_device_camera_registration_lookup", "device_id", "effective_from", "effective_to"),
        Index("idx_device_camera_registration_camera", "camera_id", "effective_from"),
    )


class IdentityAssociationRuntimeLease(Base):
    __tablename__ = "identity_association_runtime_leases"

    store_id = Column(String(64), primary_key=True)
    mode = Column(String(16), nullable=False)
    owner_id = Column(String(128), nullable=False)
    lease_token = Column(String(128), nullable=False)
    lease_generation = Column(Integer, nullable=False, default=1)
    acquired_at = Column(DateTime, nullable=False)
    heartbeat_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_identity_runtime_lease_expiry", "expires_at"),
    )


class IdentityAssociationStoreState(Base):
    __tablename__ = "identity_association_store_states"

    store_id = Column(String(64), primary_key=True)
    schema_version = Column(String(32), nullable=False, default="v1")
    activated_mode = Column(String(16), nullable=False, default="legacy")
    new_mode_activated_at = Column(DateTime, nullable=True)
    first_new_arrival_id = Column(String(64), nullable=True)
    activated_by_runtime_id = Column(String(128), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class CameraIdentityBindingPolicy(Base):
    __tablename__ = "camera_identity_binding_policies"

    id = Column(String(64), primary_key=True, default=new_uuid)
    camera_id = Column(String(64), nullable=False)
    mode = Column(String(16), nullable=False)
    algorithm_version = Column(String(64), nullable=False)
    parameter_version = Column(String(64), nullable=False)
    effective_from = Column(DateTime, nullable=False)
    effective_to = Column(DateTime, nullable=True)
    change_reason = Column(String(256), nullable=True)
    qualification_snapshot = Column(JSON, nullable=False, default=dict)
    rollback_evaluation_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index(
            "uq_camera_identity_policy_current",
            "camera_id", unique=True,
            sqlite_where=text("effective_to IS NULL"),
        ),
        Index("idx_camera_identity_policy_effective", "camera_id", "effective_from"),
    )


class TrackAssociationEvaluation(Base):
    __tablename__ = "track_association_evaluations"

    id = Column(String(64), primary_key=True, default=new_uuid)
    recognition_event_id = Column(String(64), nullable=False)
    assessment_fingerprint = Column(String(64), nullable=True)
    store_id = Column(String(64), nullable=True)
    camera_id = Column(String(64), nullable=True)
    runtime_id = Column(String(128), nullable=True)
    runtime_owner_id = Column(String(128), nullable=True)
    runtime_instance_id = Column(String(64), nullable=True)
    runtime_key = Column(String(128), nullable=True)
    mode = Column(String(16), nullable=False, default="shadow")
    result = Column(String(32), nullable=False)
    method = Column(String(64), nullable=True)
    reason = Column(String(256), nullable=True)
    candidates = Column(JSON, nullable=False, default=list)
    excluded_candidates = Column(JSON, nullable=False, default=list)
    candidate_count = Column(Integer, nullable=False, default=0)
    effective_occurred_at = Column(DateTime, nullable=True)
    observed_at = Column(DateTime, nullable=False)
    time_delta_seconds = Column(Float, nullable=True)
    watermark_at = Column(DateTime, nullable=True)
    window_complete = Column(Boolean, nullable=False, default=True)
    algorithm_version = Column(String(64), nullable=True)
    parameter_version = Column(String(64), nullable=True)
    policy_version_id = Column(String(64), nullable=True)
    policy_snapshot = Column(JSON, nullable=False, default=dict)
    spatial_evidence = Column(JSON, nullable=True)
    applied_binding = Column(Boolean, nullable=False, default=False)
    evidence_clip_id = Column(String(64), ForeignKey("evidence_clips.id"), nullable=True)
    evidence_status = Column(String(24), nullable=False, default="pending")
    evidence_failure_reason = Column(String(256), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())

    __table_args__ = (
        Index(
            "uq_track_association_evaluation_assessment",
            "recognition_event_id", "assessment_fingerprint", unique=True,
        ),
        Index("idx_track_association_evaluation_event", "recognition_event_id", "created_at"),
        Index("idx_track_association_evaluation_runtime", "camera_id", "runtime_instance_id", "observed_at"),
        Index("idx_track_association_evaluation_policy", "policy_version_id", "result"),
    )


class TrackIdentityBinding(Base):
    """Durable Track Association application fact."""

    __tablename__ = "track_identity_bindings"

    id = Column(String(64), primary_key=True, default=new_uuid)
    recognition_event_id = Column(String(64), nullable=False)
    evaluation_id = Column(String(64), ForeignKey("track_association_evaluations.id"), nullable=False)
    store_id = Column(String(64), nullable=False)
    camera_id = Column(String(64), nullable=False)
    runtime_owner_id = Column(String(128), nullable=False)
    runtime_instance_id = Column(String(64), nullable=False)
    runtime_key = Column(String(128), nullable=True)
    track_id = Column(Integer, nullable=False)
    track_token = Column(String(256), nullable=False)
    member_id = Column(String(64), ForeignKey("members.id"), nullable=False)
    member_tier = Column(String(32), nullable=True)
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=False)
    binding_level = Column(String(16), nullable=False)
    status = Column(String(24), nullable=False, default="confirmed")
    identity_source = Column(String(64), nullable=False)
    identity_confidence = Column(Float, nullable=True)
    policy_version_id = Column(String(64), nullable=True)
    algorithm_version = Column(String(64), nullable=True)
    parameter_version = Column(String(64), nullable=True)
    valid_from = Column(DateTime, nullable=False)
    valid_to = Column(DateTime, nullable=True)
    superseded_by_binding_id = Column(String(64), ForeignKey("track_identity_bindings.id"), nullable=True)
    superseded_at = Column(DateTime, nullable=True)
    superseded_reason = Column(String(256), nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_track_identity_binding_token", "track_token", "status", "valid_to"),
        Index("idx_track_identity_binding_runtime", "store_id", "camera_id", "runtime_instance_id", "track_token"),
        Index("idx_track_identity_binding_member", "member_id", "store_id", "status"),
        Index("idx_track_identity_binding_evaluation", "evaluation_id"),
        Index("idx_track_identity_binding_recognition", "recognition_event_id"),
        Index("idx_track_identity_binding_superseded", "superseded_by_binding_id"),
    )


class TrackAssociationFinalizationJob(Base):
    """Leased workflow for applying one immutable Track Association Evaluation."""

    __tablename__ = "track_association_finalization_jobs"

    id = Column(String(64), primary_key=True, default=new_uuid)
    recognition_event_id = Column(String(64), nullable=False, unique=True)
    evaluation_id = Column(String(64), ForeignKey("track_association_evaluations.id"), nullable=False)
    visit_id = Column(String(64), ForeignKey("visits.id"), nullable=False)
    binding_id = Column(String(64), ForeignKey("track_identity_bindings.id"), nullable=True)
    track_token = Column(String(256), nullable=True)
    status = Column(String(24), nullable=False, default="pending")
    application_status = Column(
        String(24), nullable=False, default="pending", server_default=text("'pending'")
    )
    application_reason = Column(String(256), nullable=True)
    cache_projection_status = Column(String(24), nullable=False, default="pending")
    backfill_status = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    claim_token = Column(String(64), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    binding_committed_at = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_track_finalization_job_due", "status", "next_retry_at"),
        Index("idx_track_finalization_job_claim", "status", "claim_expires_at"),
        Index("idx_track_finalization_job_recognition_status", "recognition_event_id", "status"),
        Index("idx_track_finalization_job_binding", "binding_id"),
        Index("idx_track_finalization_job_visit", "visit_id"),
    )


class TrackAssociationReview(Base):
    __tablename__ = "track_association_reviews"

    id = Column(String(64), primary_key=True, default=new_uuid)
    evaluation_id = Column(String(64), ForeignKey("track_association_evaluations.id"), nullable=False)
    verdict = Column(String(16), nullable=False)
    reason = Column(String(256), nullable=True)
    reviewer = Column(String(128), nullable=False)
    reviewed_at = Column(DateTime, nullable=False)
    superseded_by_review_id = Column(String(64), ForeignKey("track_association_reviews.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())


class AssociationOutcomeRebuildJob(Base):
    __tablename__ = "association_outcome_rebuild_jobs"

    id = Column(String(64), primary_key=True, default=new_uuid)
    recognition_event_id = Column(String(64), nullable=True)
    visit_id = Column(String(64), nullable=True)
    arrival_id = Column(String(64), nullable=True)
    reason = Column(String(256), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime, nullable=True)
    claim_token = Column(String(64), nullable=True)
    locked_at = Column(DateTime, nullable=True)
    claim_expires_at = Column(DateTime, nullable=True)
    last_error = Column(String(512), nullable=True)
    projected_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_association_outcome_job_due", "status", "next_retry_at"),
        Index("idx_association_outcome_job_event", "recognition_event_id", "status"),
    )
