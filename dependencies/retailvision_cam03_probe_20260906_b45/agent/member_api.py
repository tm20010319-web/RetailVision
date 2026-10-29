"""FastAPI application for member management."""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from agent.behavior_events import BehaviorEventError, EvidenceClipStore, TrafficQueryService
from agent.camera_face_api import CameraFaceApiClient
from agent.config import settings
from agent.db import get_session_factory, init_db
from agent.device_capture_store import DeviceCaptureStore
from agent.device_recognition_intake import DeviceRecognitionIntake
from agent.device_event_receiver import create_device_event_router
from agent.device_health import DeviceHealthStore
from agent.device_identity import SqlAlchemyDeviceIdentityRepository
from agent.device_recordings import DeviceRecordingClient, EvidenceClipProcessor, MediaTrimmer
from agent.staff_greeting_shadow_query import StaffGreetingShadowQuery
from agent.member_device_sync import MemberDeviceSyncService
from agent.member_management_service import MemberManagementError, MemberManagementService
from agent.operations_query_service import OperationsQueryService
from agent.push_coverage import PushCoverageStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class MemberCreateRequest(BaseModel):
    member_id: str = Field(..., description="会员ID（业务系统提供）")
    name: str = Field(..., description="会员姓名")
    phone: Optional[str] = Field(None, description="手机号")
    tier: str = Field(
        "bronze",
        description="会员等级: diamond/platinum/gold/silver/bronze",
    )
    department: Optional[str] = Field(None, description="所属部门")
    position: Optional[str] = Field(None, description="职务")
    points: int = Field(0, description="初始积分")
    gender: str = Field("unknown", description="性别: male/female/unknown")


class MemberUpdateRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    tier: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    points: Optional[int] = None
    gender: Optional[str] = None


class PeakEvidenceRequest(BaseModel):
    days: int = Field(..., description="Lookback in days: 3 or 7")


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------

_service_instance: Optional[MemberManagementService] = None


@dataclass(frozen=True)
class DeviceEventServices:
    intake: DeviceRecognitionIntake
    health_store: DeviceHealthStore
    coverage_store: PushCoverageStore


def _build_service() -> MemberManagementService:
    """Build the management service from current settings."""
    init_db()
    session_factory = get_session_factory()

    device_sync = None
    device_id = None
    group_uuid = settings.CAMERA_FACE_GROUP_UUID or None
    group_id = settings.CAMERA_FACE_GROUP_ID

    if settings.CAMERA_FACE_DEVICE_ID and settings.CAMERA_FACE_BASE_URL:
        device_id = settings.CAMERA_FACE_DEVICE_ID
        client = CameraFaceApiClient.from_settings(settings)
        repository = SqlAlchemyDeviceIdentityRepository(session_factory)
        device_sync = MemberDeviceSyncService(
            client=client,
            repository=repository,
        )

    return MemberManagementService(
        session_factory=session_factory,
        device_sync_service=device_sync,
        group_uuid=group_uuid,
        group_id=group_id,
        device_id=device_id,
    )


def get_service() -> MemberManagementService:
    """Return the singleton service instance, lazily initialised."""
    global _service_instance
    if _service_instance is None:
        _service_instance = _build_service()
    return _service_instance


def reset_service() -> None:
    """Discard the cached service so the next call rebuilds it."""
    global _service_instance
    _service_instance = None


def get_operations_query_service() -> OperationsQueryService:
    return OperationsQueryService(
        get_service().session_factory,
        device_id=settings.CAMERA_FACE_DEVICE_ID or None,
        health_freshness_seconds=settings.DEVICE_HEALTH_FRESHNESS_SECONDS,
        profile_mode=settings.PROFILE_MODE,
    )


def get_behavior_query_service() -> TrafficQueryService:
    init_db()
    return TrafficQueryService(get_session_factory())


def get_evidence_clip_store() -> EvidenceClipStore:
    init_db()
    return EvidenceClipStore(get_session_factory())


def get_staff_greeting_shadow_query() -> StaffGreetingShadowQuery:
    init_db()
    return StaffGreetingShadowQuery(get_session_factory())


def get_evidence_clip_processor() -> Optional[EvidenceClipProcessor]:
    if not settings.EVIDENCE_AUTO_PROCESS:
        return None
    required = (
        settings.PEAK_ENTRY_CAMERA_ID,
        settings.CAMERA_FACE_BASE_URL,
        settings.CAMERA_FACE_USERNAME,
    )
    if not all(required):
        return None
    client = DeviceRecordingClient(
        device_id=settings.PEAK_ENTRY_CAMERA_ID,
        base_url=settings.CAMERA_FACE_BASE_URL,
        username=settings.CAMERA_FACE_USERNAME,
        password=settings.CAMERA_FACE_PASSWORD,
        connect_timeout=settings.CAMERA_FACE_CONNECT_TIMEOUT,
        read_timeout=max(settings.CAMERA_FACE_READ_TIMEOUT, 30.0),
    )
    return EvidenceClipProcessor(
        get_session_factory(),
        clients={settings.PEAK_ENTRY_CAMERA_ID: client},
        output_dir=Path(settings.EVIDENCE_CLIP_DIR),
        trimmer=MediaTrimmer(ffmpeg_path=settings.FFMPEG_PATH),
    )


def _build_device_event_services() -> DeviceEventServices:
    init_db()
    session_factory = get_session_factory()
    return DeviceEventServices(
        intake=DeviceRecognitionIntake(
            session_factory,
            capture_store=DeviceCaptureStore(
                settings.P6S_CAPTURE_DIR,
                max_bytes=settings.P6S_MAX_CAPTURE_BYTES,
                max_pixels=settings.P6S_MAX_CAPTURE_PIXELS,
            ),
            similarity_threshold=settings.P6S_SIMILARITY_THRESHOLD,
        ),
        health_store=DeviceHealthStore(session_factory),
        coverage_store=PushCoverageStore(session_factory),
    )


def get_device_event_services() -> DeviceEventServices:
    services = getattr(app.state, "device_event_services", None)
    if services is None:
        services = _build_device_event_services()
        app.state.device_event_services = services
    coverage_store = getattr(services, "coverage_store", None)
    if coverage_store is not None:
        app.state.push_coverage_store = coverage_store
    return services


def reset_device_event_services() -> None:
    for name in (
        "device_event_services",
        "device_recognition_intake",
        "device_health_store",
        "push_coverage_store",
    ):
        if hasattr(app.state, name):
            delattr(app.state, name)


def _configured_device_id(serial_number: str) -> Optional[str]:
    if not settings.P6S_DEVICE_SERIAL or serial_number != settings.P6S_DEVICE_SERIAL:
        return None
    return settings.CAMERA_FACE_DEVICE_ID or None


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(application: FastAPI):
    device_events = _build_device_event_services()
    receiver_instance_id = uuid.uuid4().hex
    boot_id = uuid.uuid4().hex
    application.state.device_event_services = device_events
    application.state.device_recognition_intake = device_events.intake
    application.state.device_health_store = device_events.health_store
    application.state.push_coverage_store = device_events.coverage_store
    application.state.push_coverage_receiver_instance_id = receiver_instance_id
    application.state.push_coverage_boot_id = boot_id
    coverage_device_id = settings.CAMERA_FACE_DEVICE_ID or ""
    if coverage_device_id:
        device_events.coverage_store.start_receiver(
            device_id=coverage_device_id,
            receiver_instance_id=receiver_instance_id,
            boot_id=boot_id,
        )
    try:
        get_service()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Service initialisation deferred: %s", exc)
    try:
        yield
    finally:
        if coverage_device_id:
            device_events.coverage_store.stop_receiver(
                device_id=coverage_device_id,
                receiver_instance_id=receiver_instance_id,
                boot_id=boot_id,
            )

app = FastAPI(
    title="门店会员管理 API",
    description="会员注册、查询、设备人脸授权管理",
    version="1.0.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.MEMBER_API_CORS_ORIGINS),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type"],
)
app.include_router(
    create_device_event_router(
        None,
        None,
        _configured_device_id,
    )
)


@app.middleware("http")
async def reject_cross_origin_writes(request: Request, call_next):
    """Keep browser access read-only while preserving non-browser API clients."""
    allowed_browser_commands = {("POST", "/api/traffic/peak/evidence")}
    if (
        request.headers.get("origin")
        and request.method not in {"GET", "OPTIONS"}
        and (request.method, request.url.path) not in allowed_browser_commands
    ):
        return JSONResponse(
            status_code=405,
            content={"detail": "cross-origin member API access is read-only"},
        )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Member CRUD
# ---------------------------------------------------------------------------

@app.post("/api/members", status_code=201)
async def create_member(req: MemberCreateRequest):
    """创建会员记录（第一步，不含照片）。"""
    service = get_service()
    try:
        return service.create_member(
            member_id=req.member_id,
            name=req.name,
            phone=req.phone,
            tier=req.tier,
            department=req.department,
            position=req.position,
            points=req.points,
            gender=req.gender,
        )
    except MemberManagementError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/members/{member_id}")
async def get_member(member_id: str):
    """查询会员详情（含设备同步状态）。"""
    service = get_service()
    result = service.get_member(member_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"member not found: {member_id}")
    return result


@app.get("/api/members")
async def list_members(
    phone: Optional[str] = Query(None, description="按手机号查询"),
    tier: Optional[str] = Query(None, description="按等级过滤"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """会员列表（分页）或按手机号查询。"""
    service = get_service()
    if phone:
        result = service.get_member_by_phone(phone)
        if result is None:
            raise HTTPException(status_code=404, detail=f"member not found by phone: {phone}")
        return result
    return service.list_members(tier=tier, page=page, size=size)


@app.put("/api/members/{member_id}")
async def update_member(member_id: str, req: MemberUpdateRequest):
    """更新会员信息。"""
    service = get_service()
    try:
        return service.update_member(
            member_id,
            name=req.name,
            phone=req.phone,
            tier=req.tier,
            department=req.department,
            position=req.position,
            points=req.points,
            gender=req.gender,
        )
    except MemberManagementError as exc:
        status_code = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))


# ---------------------------------------------------------------------------
# Device face registration
# ---------------------------------------------------------------------------

@app.post("/api/members/{member_id}/face")
async def upload_face(
    member_id: str,
    file: UploadFile = File(..., description="人脸照片文件"),
    declared_sex: str = Query(..., description="会员自报性别: male/female"),
):
    """上传人脸照片到设备脸库（第二步，触发设备注册）。"""
    service = get_service()
    photo_bytes = await file.read()
    if not photo_bytes:
        raise HTTPException(status_code=400, detail="empty file upload")
    try:
        return service.upload_face(member_id, photo_bytes, declared_sex)
    except MemberManagementError as exc:
        status_code = 404 if "not found" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))


@app.get("/api/members/{member_id}/face/status")
async def get_face_status(member_id: str):
    """查询设备人脸同步状态。"""
    service = get_service()
    return service.get_face_status(member_id)


@app.delete("/api/members/{member_id}/face")
async def revoke_face(member_id: str):
    """撤销设备人脸授权。"""
    service = get_service()
    try:
        return service.revoke_face(member_id)
    except MemberManagementError as exc:
        status_code = 404 if "not found" in str(exc) or "no mapping" in str(exc) else 400
        raise HTTPException(status_code=status_code, detail=str(exc))


# ---------------------------------------------------------------------------
# Business queries
# ---------------------------------------------------------------------------

@app.get("/api/operations/status")
async def get_operations_status():
    return get_operations_query_service().status()


@app.get("/api/recognitions/recent")
async def get_recent_recognitions(
    limit: int = Query(20, ge=1, le=100),
):
    return get_operations_query_service().recent_recognitions(limit=limit)


@app.get("/api/behaviors/recent")
async def get_recent_behaviors(limit: int = Query(50, ge=1, le=100)):
    return {"items": get_behavior_query_service().recent_behaviors(limit=limit)}


@app.get("/api/staff-greetings/summary")
async def get_staff_greeting_summary(
    scope: str = Query("official"),
):
    if scope not in {"official", "demo", "all"}:
        raise HTTPException(status_code=422, detail="scope must be official, demo, or all")
    query = get_behavior_query_service()
    try:
        items = (
            query.staff_greeting_summary()
            if scope == "official"
            else query.staff_greeting_summary(scope=scope)
        )
    except BehaviorEventError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items}


@app.get("/api/staff-greetings/demo-summary")
async def get_demo_staff_greeting_summary():
    return {
        "items": get_behavior_query_service().staff_greeting_summary(scope="demo")
    }


@app.get("/api/operations/greetings/evaluations")
async def get_greeting_v2_evaluations(
    limit: int = Query(50, ge=1, le=100),
    camera_id: Optional[str] = Query(None),
    path: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    until: Optional[datetime] = Query(None),
):
    try:
        items = get_staff_greeting_shadow_query().recent_shadow(
            limit=limit, camera_id=camera_id, path=path, decision=decision,
            since=since, until=until,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": items}


@app.get("/api/operations/greetings/evaluations/{evaluation_id}")
async def get_greeting_v2_evaluation(evaluation_id: str):
    item = get_staff_greeting_shadow_query().get_shadow(evaluation_id)
    if item is None:
        raise HTTPException(status_code=404, detail="shadow evaluation not found")
    return item

@app.get("/api/traffic/recent")
async def get_recent_customer_entries(limit: int = Query(50, ge=1, le=100)):
    return {"items": get_behavior_query_service().recent_entries(limit=limit)}


@app.get("/api/traffic/peak")
async def get_peak_entry_window(days: int = Query(3)):
    if days not in (3, 7):
        raise HTTPException(status_code=422, detail="days must be 3 or 7")
    result = get_behavior_query_service().peak_entry_window(
        days=days,
        now=datetime.now(),
        camera_id=settings.PEAK_ENTRY_CAMERA_ID or None,
    )
    return _peak_response(result)


@app.post("/api/traffic/peak/evidence", status_code=202)
async def request_peak_entry_evidence(
    request: PeakEvidenceRequest,
    background_tasks: BackgroundTasks,
):
    if request.days not in (3, 7):
        raise HTTPException(status_code=422, detail="days must be 3 or 7")
    camera_id = settings.PEAK_ENTRY_CAMERA_ID
    if not camera_id:
        raise HTTPException(status_code=503, detail="peak entry camera is not configured")
    result = get_behavior_query_service().peak_entry_window(
        days=request.days,
        now=datetime.now(),
        camera_id=camera_id,
    )
    if not result["count"]:
        raise HTTPException(status_code=409, detail="no customer entry events in lookback")
    owner_id = (
        f"{camera_id}:{result['start'].strftime('%Y%m%d%H%M%S')}:"
        f"{result['end'].strftime('%Y%m%d%H%M%S')}"
    )
    clip_store = get_evidence_clip_store()
    clip = clip_store.request(
        owner_type="peak_entry_window",
        owner_id=owner_id,
        camera_id=camera_id,
        requested_start=result["start"],
        requested_end=result["end"],
    )
    processor = get_evidence_clip_processor()
    if processor is not None and clip.status != "ready":
        background_tasks.add_task(processor.process, clip.id)
    return {
        "peak": _peak_response(result),
        "evidence": clip_store.status(clip.id),
    }


@app.get("/api/evidence/{clip_id}")
async def get_evidence_status(clip_id: str):
    result = get_evidence_clip_store().status(clip_id)
    if result is None:
        raise HTTPException(status_code=404, detail="evidence clip not found")
    return result


@app.get("/api/evidence/{clip_id}/video")
async def get_evidence_video(clip_id: str):
    path_value = get_evidence_clip_store().ready_path(clip_id)
    if path_value is None:
        raise HTTPException(status_code=404, detail="evidence video is not ready")
    root = Path(settings.EVIDENCE_CLIP_DIR).resolve()
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="evidence video is not ready") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="evidence video is not ready")
    return FileResponse(candidate, media_type="video/mp4", filename=f"{clip_id}.mp4")


@app.get("/api/members/{member_id}/visits")
async def get_visits(member_id: str, days: int = Query(30, ge=1, le=365)):
    """查询会员到店历史。"""
    service = get_service()
    if service.get_member(member_id) is None:
        raise HTTPException(status_code=404, detail=f"member not found: {member_id}")
    return {"member_id": member_id, "days": days, "visits": service.get_visits(member_id, days)}


@app.get("/api/members/{member_id}/profile")
async def get_profile(member_id: str):
    """Return the latest asynchronous customer-profile task and result."""
    result = get_service().get_profile_status(member_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"member not found: {member_id}")
    return result


@app.get("/api/members/{member_id}/purchases")
async def get_purchases(member_id: str, days: int = Query(30, ge=1, le=365)):
    """查询会员消费历史。"""
    service = get_service()
    if service.get_member(member_id) is None:
        raise HTTPException(status_code=404, detail=f"member not found: {member_id}")
    return {"member_id": member_id, "days": days, "purchases": service.get_purchases(member_id, days)}


@app.get("/api/members/{member_id}/coupons")
async def get_coupons(member_id: str):
    """查询会员可用优惠券。"""
    service = get_service()
    if service.get_member(member_id) is None:
        raise HTTPException(status_code=404, detail=f"member not found: {member_id}")
    return {"member_id": member_id, "coupons": service.get_coupons(member_id)}


def _peak_response(result: dict) -> dict:
    return {
        **result,
        "start": result["start"].isoformat() if result["start"] else None,
        "end": result["end"].isoformat() if result["end"] else None,
    }
