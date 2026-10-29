"""
RealtimeEntryPipeline：实时进店检测管线。

职责：
- 驱动摄像头实时帧采集。
- 调用 VisionDetector.process_frame() 进行 YOLO 检测 + 追踪。
- 基于 track_id 稳定性判断新顾客进店。
- 设备 API 模式：稳定 track 确认后产生 StableTrackEvent，不裁剪人像，
  不同步调用本地人脸识别；由 EntryEventCoordinator 与设备识别事件关联。
- legacy 模式：暂时保留原 FaceRecognizer 门控，供旧调用方兼容迁移。

约束：
- 实时路径不调用任何 LLM。
- 所有 Detection 输出前转为 dict。
- 降级 mock 结果（is_fallback=True）默认不被当作真实会员。
"""

import logging
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np

from agent.detector import VisionDetector, Detection, open_video_capture
from agent.device_identity import CustomerSession, StableTrackEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 回调类型
# ---------------------------------------------------------------------------

# 新顾客确认后、会员识别前的回调（无 FaceRecognizer 时的回退路径）
# 接收 (track_id, detection_dict, frame_bgr)
NewCustomerCallback = Callable[[int, Dict[str, Any], Any], None]

# 会员匹配成功回调：接收 (customer_session_dict)
MemberEntryCallback = Callable[[Dict[str, Any]], None]

# 匿名访客/匹配失败回调：接收 (track_id, detection_dict, reason)
AnonymousVisitCallback = Callable[[int, Dict[str, Any], str], None]
StableTrackCallback = Callable[[StableTrackEvent], None]
StableTrackFrameCallback = Callable[[StableTrackEvent, np.ndarray], None]
IdentityUnknownCallback = Callable[[Any, str], None]


# ---------------------------------------------------------------------------
# TrackState
# ---------------------------------------------------------------------------

class _TrackState:
    """内部追踪状态"""
    __slots__ = (
        "track_id",
        "consecutive_frames",
        "total_frames",
        "first_seen_time",
        "last_seen_time",
        "is_confirmed",
        "best_detection",
        "member_checked",
    )

    def __init__(self, track_id: int, detection: Detection):
        self.track_id = track_id
        self.consecutive_frames = 1
        self.total_frames = 1
        self.first_seen_time = time.time()
        self.last_seen_time = self.first_seen_time
        self.is_confirmed = False
        self.best_detection = detection
        self.member_checked = False  # 防止同一 track 重复创建 customer_session

    def update(self, detection: Detection) -> None:
        self.consecutive_frames += 1
        self.total_frames += 1
        self.last_seen_time = time.time()
        if detection.confidence > self.best_detection.confidence:
            self.best_detection = detection

    def mark_lost(self) -> None:
        self.consecutive_frames = 0


# ---------------------------------------------------------------------------
# RealtimeEntryPipeline
# ---------------------------------------------------------------------------

class RealtimeEntryPipeline:
    """实时进店检测 Pipeline（含会员识别 Gate）。

    用法::

        detector = VisionDetector()
        face_recognizer = FaceRecognizer(db_session)

        pipeline = RealtimeEntryPipeline(
            detector=detector,
            face_recognizer=face_recognizer,
            stability_threshold=3,
            member_match_threshold=0.75,
            process_every_seconds=0.5,
        )

        # 设置会员/匿名回调
        pipeline.on_member_entry = my_member_handler
        pipeline.on_anonymous_visit = my_anonymous_handler

        # 开始实时检测
        pipeline.run(video_source=0)
    """

    def __init__(
        self,
        detector: VisionDetector,
        face_recognizer: Any = None,  # Optional[FaceRecognizer]，避免硬导入依赖
        stability_threshold: int = 3,
        member_match_threshold: float = 0.75,
        process_every_seconds: float = 0.5,
        track_lost_timeout: float = 10.0,
        allow_fallback_match: bool = False,
        camera_id: str = "",
        on_new_customer: Optional[NewCustomerCallback] = None,
        on_member_entry: Optional[MemberEntryCallback] = None,
        on_anonymous_visit: Optional[AnonymousVisitCallback] = None,
        identity_mode: str = "legacy",
        on_stable_track: Optional[StableTrackCallback] = None,
        on_stable_track_frame: Optional[StableTrackFrameCallback] = None,
        on_identity_unknown: Optional[IdentityUnknownCallback] = None,
        identity_clock: Callable[[], datetime] = datetime.now,
    ):
        """
        Args:
            detector: 已初始化的 VisionDetector 实例。
            face_recognizer: FaceRecognizer 实例（可选）。不传则退化为纯检测模式，
                             新顾客确认后直接触发 on_new_customer。
            stability_threshold: track_id 连续出现多少帧后确认为新顾客。
            member_match_threshold: 会员匹配置信度阈值（默认 0.75）。
            process_every_seconds: 处理帧的时间间隔（秒）。
            track_lost_timeout: track 丢失超过此秒数后重置状态。
            allow_fallback_match: 是否允许降级 mock 结果视为真实会员（仅测试用）。
            camera_id: 摄像头标识。
            on_new_customer: 新顾客回调（无 face_recognizer 时使用）。
            on_member_entry: 会员匹配成功回调。
            on_anonymous_visit: 匿名访客/匹配失败回调。
        """
        if identity_mode not in {"legacy", "device_api"}:
            raise ValueError("identity_mode must be 'legacy' or 'device_api'")
        self.detector = detector
        self.face_recognizer = face_recognizer
        self.identity_mode = identity_mode
        self.stability_threshold = stability_threshold
        self.member_match_threshold = member_match_threshold
        self.process_every_seconds = process_every_seconds
        self.track_lost_timeout = track_lost_timeout
        self.allow_fallback_match = allow_fallback_match
        self.camera_id = camera_id

        # 回调
        self.on_new_customer = on_new_customer
        self.on_member_entry = on_member_entry
        self.on_anonymous_visit = on_anonymous_visit
        self.on_stable_track = on_stable_track
        self.on_stable_track_frame = on_stable_track_frame
        self.on_identity_unknown = on_identity_unknown
        self.identity_clock = identity_clock

        # 内部状态
        self._track_states: Dict[int, _TrackState] = {}
        self._running = False
        self._external_shutdown_event: Optional[Any] = None
        self._session_counter = 0  # 生成 session_id
        self._frame_counter = 0    # process_frame 帧计数
        self._last_match_reason: Optional[str] = None  # 最近一次会员匹配结果原因

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def bind_shutdown_event(self, shutdown_event: Any) -> None:
        """Bind an externally owned persistent shutdown event."""
        self._external_shutdown_event = shutdown_event

    def _external_shutdown_requested(self) -> bool:
        return bool(
            self._external_shutdown_event is not None
            and self._external_shutdown_event.is_set()
        )

    def run(self, video_source: Any) -> Dict[str, Any]:
        """启动实时检测主循环。

        Args:
            video_source: 摄像头 ID（如 0）或视频文件路径。

        Returns:
            运行摘要 dict。
        """
        self._running = True
        self._track_states.clear()
        self._session_counter = 0

        if self._external_shutdown_requested():
            self._running = False
            return {
                "total_frames_read": 0,
                "total_frames_processed": 0,
                "total_new_customers": 0,
                "total_members": 0,
                "total_anonymous": 0,
                "fps": 0,
            }

        cap = open_video_capture(video_source)
        if not cap.isOpened():
            logger.error(f"Cannot open video source: {video_source}")
            self._running = False
            return {"error": f"Cannot open video source: {video_source}"}

        self.detector.initialize()

        fps = cap.get(cv2.CAP_PROP_FPS) if hasattr(cv2, "CAP_PROP_FPS") else 0

        new_customer_count = 0
        member_count = 0
        anonymous_count = 0
        frame_count = 0
        processed_count = 0
        last_process_time: Optional[float] = None

        logger.info(
            "RealtimeEntryPipeline started: source=%s, stability=%d, match_threshold=%.2f, interval=%.2fs",
            video_source,
            self.stability_threshold,
            self.member_match_threshold,
            self.process_every_seconds,
        )

        try:
            while self._running and not self._external_shutdown_requested():
                ret, frame = cap.read()
                if not ret:
                    break
                if not self._running or self._external_shutdown_requested():
                    break

                frame_count += 1

                # 时间模式采样
                now = time.time()
                if last_process_time is not None and (now - last_process_time) < self.process_every_seconds:
                    continue
                last_process_time = now

                # ---- 处理单帧（复用 process_frame 公开接口）----
                result = self.process_frame(frame)
                processed_count += 1

                # 累计统计
                for entry in result["confirmed_entries"]:
                    new_customer_count += 1
                    if entry["entry_type"] == "member":
                        member_count += 1
                    elif entry["entry_type"] == "anonymous":
                        anonymous_count += 1

                # 定期日志
                if processed_count % 200 == 0:
                    logger.info(
                        "Pipeline: frames=%d, proc=%d, active=%d, new=%d, member=%d, anon=%d",
                        frame_count,
                        processed_count,
                        result["active_tracks"],
                        new_customer_count,
                        member_count,
                        anonymous_count,
                    )

        except Exception:
            logger.exception("RealtimeEntryPipeline error")
        finally:
            cap.release()
            self._running = False

        summary = {
            "total_frames_read": frame_count,
            "total_frames_processed": processed_count,
            "total_new_customers": new_customer_count,
            "total_members": member_count,
            "total_anonymous": anonymous_count,
            "fps": fps,
        }
        logger.info("RealtimeEntryPipeline stopped: %s", summary)
        return summary

    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """处理单帧，返回结构化结果。

        这是实时进店管线的核心公开接口，供 API 服务、RTSP 服务、
        测试、CLI 复用。run() 内部也调用此方法。

        Args:
            frame: BGR 格式的视频帧。

        Returns:
            {
                "confirmed_entries": [
                    {
                        "track_id": int,
                        "detection": dict,
                        "entry_type": "member" | "anonymous",
                        "session": dict | None,
                        "reason": str | None
                    }
                ],
                "detections": [dict],
                "active_tracks": int
            }
        """
        # 1. YOLO 检测
        detections = self.detector.process_frame(frame)
        detection_dicts = [d.to_dict() for d in detections]

        # 2. 更新 track 状态
        self._frame_counter += 1
        confirmed_tracks = self._update_tracks(detections, self._frame_counter)

        # 3. 处理确认的 track
        confirmed_entries = []
        stable_track_events = []
        for track_id, det_dict, state in confirmed_tracks:
            entry = self._handle_confirmed_track(track_id, det_dict, state, frame)
            confirmed_entries.append(entry)
            if entry.get("stable_track_event") is not None:
                stable_track_events.append(entry["stable_track_event"])

        # 4. 统计 active tracks
        active_tracks = sum(
            1 for t in self._track_states.values() if t.consecutive_frames > 0
        )

        return {
            "confirmed_entries": confirmed_entries,
            "stable_track_events": stable_track_events,
            "detections": detection_dicts,
            "active_tracks": active_tracks,
        }

    def _handle_confirmed_track(
        self,
        track_id: int,
        det_dict: Dict[str, Any],
        state: _TrackState,
        frame: np.ndarray,
    ) -> Dict[str, Any]:
        """处理确认的 track，执行会员识别或匿名记录。

        Returns:
            entry dict，格式见 process_frame 返回结构。
        """
        if self.identity_mode == "device_api":
            stable_event = StableTrackEvent(
                camera_id=self.camera_id,
                track_id=track_id,
                bbox=list(det_dict.get("bbox", [])),
                detection_confidence=float(det_dict.get("confidence", 0.0)),
                confirmed_at=self.identity_clock(),
            )
            if self.on_stable_track_frame:
                try:
                    self.on_stable_track_frame(stable_event, frame)
                except Exception:
                    logger.exception(
                        "on_stable_track_frame callback failed for track_id=%d", track_id
                    )
            if self.on_stable_track:
                try:
                    self.on_stable_track(stable_event)
                except Exception:
                    logger.exception(
                        "on_stable_track callback failed for track_id=%d", track_id
                    )
            return {
                "track_id": track_id,
                "detection": det_dict,
                "entry_type": "identity_pending",
                "session": None,
                "reason": "awaiting_device_identity",
                "stable_track_event": stable_event.to_dict(),
            }

        if self.face_recognizer is not None:
            # --- Member Recognition Gate ---
            session = self._try_match_member(
                track_id=track_id,
                det_dict=det_dict,
                frame=frame,
            )
            if session is not None:
                # 会员匹配成功
                logger.info(
                    "Member matched: track_id=%d, member_id=%s, confidence=%.2f",
                    track_id,
                    session.member_id,
                    session.confidence,
                )
                if self.on_member_entry:
                    try:
                        self.on_member_entry(session.to_dict())
                    except Exception:
                        logger.exception(
                            "on_member_entry callback failed for track_id=%d", track_id
                        )
                return {
                    "track_id": track_id,
                    "detection": det_dict,
                    "entry_type": "member",
                    "session": session.to_dict(),
                    "reason": None,
                }
            else:
                # 匹配失败 → 匿名访客
                reason = self._map_match_reason(self._last_match_reason)
                logger.info(
                    "Anonymous visit: track_id=%d (reason=%s)", track_id, reason
                )
                if self.on_anonymous_visit:
                    try:
                        self.on_anonymous_visit(track_id, det_dict, reason)
                    except Exception:
                        logger.exception(
                            "on_anonymous_visit callback failed for track_id=%d", track_id
                        )
                return {
                    "track_id": track_id,
                    "detection": det_dict,
                    "entry_type": "anonymous",
                    "session": None,
                    "reason": reason,
                }
        else:
            # 无 FaceRecognizer：退化为纯检测模式（Phase 1 兼容行为）
            if self.on_new_customer:
                try:
                    self.on_new_customer(track_id, det_dict, frame)
                except Exception:
                    logger.exception(
                        "on_new_customer callback failed for track_id=%d", track_id
                    )
            return {
                "track_id": track_id,
                "detection": det_dict,
                "entry_type": "anonymous",
                "session": None,
                "reason": "face_recognizer_unavailable",
            }

    @staticmethod
    def _map_match_reason(internal_reason: Optional[str]) -> str:
        """将内部匹配原因映射为用户可见的 reason。"""
        reason_map = {
            "low_confidence": "low_confidence",
            "fallback_blocked": "face_not_matched",
            "face_not_matched": "face_not_matched",
            "face_recognizer_error": "face_recognizer_unavailable",
            "already_checked": "face_not_matched",
        }
        return reason_map.get(internal_reason, "face_not_matched")

    def stop(self) -> None:
        """优雅停止主循环。"""
        self._running = False

    def reset(self) -> None:
        """重置内部状态（同时重置 detector 追踪器）。"""
        self._track_states.clear()
        self._session_counter = 0
        self._frame_counter = 0
        self._last_match_reason = None
        self.detector.reset()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _update_tracks(
        self,
        detections: List[Detection],
        frame_count: int,
    ) -> List[tuple]:
        """更新 track 状态，返回本轮确认的新 track 列表。

        返回格式：[(track_id, detection_dict, _TrackState), ...]

        规则：
        1. 当前帧出现的 track_id → 累加 consecutive_frames。
        2. 当前帧未出现的 track_id → consecutive_frames 归零。
        3. consecutive_frames >= stability_threshold 且未确认 → 标记确认，返回。
        4. 丢失超过 track_lost_timeout → 清理。
        """
        now = time.time()
        present_ids = set()
        confirmed: List[tuple] = []

        for det in detections:
            if det.track_id is None:
                continue
            present_ids.add(det.track_id)

            state = self._track_states.get(det.track_id)
            if state is None:
                state = _TrackState(det.track_id, det)
                self._track_states[det.track_id] = state
            else:
                state.update(det)

            # 稳定性判断：首次出现或持续出现都检查
            if (
                not state.is_confirmed
                and state.consecutive_frames >= self.stability_threshold
            ):
                state.is_confirmed = True
                confirmed.append(
                    (det.track_id, state.best_detection.to_dict(), state)
                )

        # 清理过期 track
        stale_ids = []
        for tid, state in self._track_states.items():
            if tid not in present_ids:
                state.mark_lost()
                if now - state.last_seen_time > self.track_lost_timeout:
                    stale_ids.append(tid)

        for tid in stale_ids:
            del self._track_states[tid]

        return confirmed

    def _try_match_member(
        self,
        track_id: int,
        det_dict: Dict[str, Any],
        frame: np.ndarray,
    ) -> Optional[CustomerSession]:
        """Member Recognition Gate：尝试将会员与 track 匹配。

        仅当 track 尚未检查过时执行。
        匹配条件：
        1. FaceRecognizer.recognize_face 返回非空
        2. result.member_id 存在
        3. result.confidence >= member_match_threshold
        4. result.is_fallback 必须为 False（除非 allow_fallback_match=True）

        Returns:
            CustomerSession 或 None。
        """
        state = self._track_states.get(track_id)
        if state is None or state.member_checked:
            self._last_match_reason = "already_checked"
            return None

        # 标记已检查，防止重复
        state.member_checked = True

        # 1. 裁剪顾客区域
        bbox = det_dict.get("bbox", [])
        crop = self._crop_person(frame, bbox)

        # 2. 调用 FaceRecognizer
        try:
            result = self.face_recognizer.recognize_face(
                crop,
                allow_fallback=self.allow_fallback_match,
            )
        except Exception:
            self._last_match_reason = "face_recognizer_error"
            logger.exception("FaceRecognizer.recognize_face failed for track_id=%d", track_id)
            return None

        # 3. Gate 条件检查
        if result is None:
            self._last_match_reason = "face_not_matched"
            logger.debug("track_id=%d: face recognition returned None", track_id)
            return None

        if result.get("is_fallback") and not self.allow_fallback_match:
            self._last_match_reason = "fallback_blocked"
            logger.debug(
                "track_id=%d: fallback result blocked (allow_fallback_match=False)", track_id
            )
            return None

        member_id = result.get("member_id")
        if not member_id:
            self._last_match_reason = "face_not_matched"
            logger.debug("track_id=%d: result missing member_id", track_id)
            return None

        confidence = result.get("confidence", 0.0)
        if confidence < self.member_match_threshold:
            self._last_match_reason = "low_confidence"
            logger.debug(
                "track_id=%d: confidence %.2f < threshold %.2f",
                track_id,
                confidence,
                self.member_match_threshold,
            )
            return None

        # 4. 创建 CustomerSession
        self._last_match_reason = "matched"
        self._session_counter += 1
        session = CustomerSession(
            session_id=f"sess_{self.camera_id}_{track_id}_{self._session_counter}",
            track_id=track_id,
            member_id=member_id,
            member_type="member",
            camera_id=self.camera_id,
            zone_name="",  # 后续可由区域检测填充
            confidence=confidence,
            entry_time=time.time(),
            detection_bbox=bbox,
            match_result=result,
            identity_source="local_face_recognizer",
            identity_time=time.time(),
        )
        return session

    @staticmethod
    def _crop_person(frame: np.ndarray, bbox: List[int]) -> np.ndarray:
        """根据 bbox 从帧中裁剪顾客区域。

        Args:
            frame: 完整帧（BGR）。
            bbox: [x1, y1, x2, y2]。

        Returns:
            裁剪后的图像区域；如果 bbox 无效则返回原帧。
        """
        if not bbox or len(bbox) != 4:
            return frame

        h, w = frame.shape[:2]
        x1, y1, x2, y2 = bbox
        x1 = max(0, int(x1))
        y1 = max(0, int(y1))
        x2 = min(w, int(x2))
        y2 = min(h, int(y2))

        if x2 <= x1 or y2 <= y1:
            return frame

        return frame[y1:y2, x1:x2]


# ---------------------------------------------------------------------------
# 便捷工厂
# ---------------------------------------------------------------------------

def create_realtime_pipeline(
    model_path: Optional[str] = None,
    device: str = "cpu",
    conf_threshold: float = 0.5,
    face_recognizer: Any = None,
    stability_threshold: int = 3,
    member_match_threshold: float = 0.75,
    process_every_seconds: float = 0.5,
    allow_fallback_match: bool = False,
    camera_id: str = "",
    on_new_customer: Optional[NewCustomerCallback] = None,
    on_member_entry: Optional[MemberEntryCallback] = None,
    on_anonymous_visit: Optional[AnonymousVisitCallback] = None,
    identity_mode: Optional[str] = None,
    on_stable_track: Optional[StableTrackCallback] = None,
    on_stable_track_frame: Optional[StableTrackFrameCallback] = None,
    on_identity_unknown: Optional[IdentityUnknownCallback] = None,
    identity_clock: Callable[[], datetime] = datetime.now,
) -> RealtimeEntryPipeline:
    """创建 RealtimeEntryPipeline 的便捷工厂。"""
    detector = VisionDetector(
        model_path=model_path,
        device=device,
        conf_threshold=conf_threshold,
    )
    detector.initialize()

    resolved_identity_mode = identity_mode or "legacy"

    return RealtimeEntryPipeline(
        detector=detector,
        face_recognizer=face_recognizer,
        stability_threshold=stability_threshold,
        member_match_threshold=member_match_threshold,
        process_every_seconds=process_every_seconds,
        allow_fallback_match=allow_fallback_match,
        camera_id=camera_id,
        on_new_customer=on_new_customer,
        on_member_entry=on_member_entry,
        on_anonymous_visit=on_anonymous_visit,
        identity_mode=resolved_identity_mode,
        on_stable_track=on_stable_track,
        on_stable_track_frame=on_stable_track_frame,
        on_identity_unknown=on_identity_unknown,
        identity_clock=identity_clock,
    )
