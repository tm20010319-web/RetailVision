"""
VisionRouter：统一任务分发路由。

根据 task_type 将请求路由到对应的 Pipeline：
- "realtime_entry"     → RealtimeEntryPipeline
- "customer_profile"   → CustomerProfilePipeline
- "historical_video"   → HistoricalVideoAnalysisPipeline

设计原则：
- Router 只做分发，不做业务逻辑。
- 各 Pipeline 独立运行，互不依赖。
- 未配置的 Pipeline 调用时返回明确错误。
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class VisionRouter:
    """统一任务分发路由。"""

    SUPPORTED_TASK_TYPES = (
        "realtime_entry",
        "customer_profile",
        "historical_video",
    )

    def __init__(
        self,
        detector: Any = None,
        realtime_pipeline: Any = None,
        profile_pipeline: Any = None,
        historical_pipeline: Any = None,
    ):
        self.detector = detector
        self.realtime_pipeline = realtime_pipeline
        self.profile_pipeline = profile_pipeline
        self.historical_pipeline = historical_pipeline

    def dispatch(self, task_type: str, **kwargs) -> dict:
        """根据 task_type 分发到对应 Pipeline。

        Args:
            task_type: 任务类型（realtime_entry / customer_profile / historical_video）。
            **kwargs: 传递给对应 Pipeline 的参数。

        Returns:
            Pipeline 的返回 dict。

        Raises:
            ValueError: 不支持的 task_type 或对应 Pipeline 未配置。
        """
        if task_type not in self.SUPPORTED_TASK_TYPES:
            raise ValueError(
                f"Unsupported task_type: '{task_type}'. "
                f"Supported: {self.SUPPORTED_TASK_TYPES}"
            )

        if task_type == "realtime_entry":
            return self._dispatch_realtime(**kwargs)
        elif task_type == "customer_profile":
            return self._dispatch_profile(**kwargs)
        elif task_type == "historical_video":
            return self._dispatch_historical(**kwargs)

        # 不可达
        raise ValueError(f"Unhandled task_type: {task_type}")

    # ------------------------------------------------------------------
    # 内部分发方法
    # ------------------------------------------------------------------

    def _dispatch_realtime(self, **kwargs) -> dict:
        if self.realtime_pipeline is None:
            raise ValueError("RealtimeEntryPipeline is not configured")

        frame = kwargs.get("frame")
        if frame is None:
            raise ValueError("'frame' is required for realtime_entry task")

        result = self.realtime_pipeline.process_frame(frame)
        return {
            "task_type": "realtime_entry",
            "confirmed_entries": result["confirmed_entries"],
            "detections": result["detections"],
            "active_tracks": result["active_tracks"],
        }

    def _dispatch_profile(self, **kwargs) -> dict:
        if self.profile_pipeline is None:
            raise ValueError("CustomerProfilePipeline is not configured")

        required = ("track_id", "frame", "bbox")
        for field in required:
            if field not in kwargs:
                raise ValueError(f"'{field}' is required for customer_profile task")

        return self.profile_pipeline.run(
            track_id=kwargs["track_id"],
            frame=kwargs["frame"],
            bbox=kwargs["bbox"],
            camera_id=kwargs.get("camera_id", ""),
            zone_name=kwargs.get("zone_name", ""),
        )

    def _dispatch_historical(self, **kwargs) -> dict:
        if self.historical_pipeline is None:
            raise ValueError("HistoricalVideoAnalysisPipeline is not configured")

        video_source = kwargs.get("video_source")
        if not video_source:
            raise ValueError("'video_source' is required for historical_video task")

        return self.historical_pipeline.run(
            video_source=video_source,
            process_every_seconds=kwargs.get("process_every_seconds", 3.0),
            on_progress=kwargs.get("on_progress"),
            time_range=kwargs.get("time_range"),
        )
