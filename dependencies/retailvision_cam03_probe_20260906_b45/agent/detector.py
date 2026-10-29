"""
视频检测器：YOLO目标检测 + 简单追踪
修复：移除对ByteTrack的硬依赖，实现内置追踪器
"""

import os

os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")

import cv2
import numpy as np
import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from ultralytics import YOLO

from agent.config import settings

logger = logging.getLogger(__name__)

try:
    cv2.setLogLevel(0)
except AttributeError:
    pass


def open_video_capture(video_source: Any) -> cv2.VideoCapture:
    if isinstance(video_source, int):
        for backend in (cv2.CAP_DSHOW, cv2.CAP_MSMF):
            cap = cv2.VideoCapture(video_source, backend)
            if cap.isOpened():
                return cap
            cap.release()
        return cv2.VideoCapture()
    return cv2.VideoCapture(video_source)


@dataclass
class Detection:
    """单次检测结果"""
    bbox: List[int]  # [x1, y1, x2, y2]
    confidence: float
    class_id: int
    class_name: str
    track_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """将 Detection 转为 dict，方便序列化和传递。"""
        return asdict(self)


@dataclass
class TrackedObject:
    """追踪对象"""
    track_id: int
    class_id: int
    class_name: str
    positions: List[Tuple[int, int]] = field(default_factory=list)
    last_bbox: Optional[List[int]] = None
    frames_since_last_seen: int = 0
    is_active: bool = True


class SimpleTracker:
    """
    简单目标追踪器（基于IoU匹配）
    当ByteTrack不可用时作为备选
    """
    
    def __init__(self, iou_threshold: float = 0.3, max_lost_frames: int = 10):
        self.iou_threshold = iou_threshold
        self.max_lost_frames = max_lost_frames
        self.next_id = 1
        self.tracks: Dict[int, TrackedObject] = {}
        self.frame_count = 0
    
    def update(self, detections: List[Detection]) -> List[Detection]:
        """更新追踪，返回带track_id的检测结果"""
        self.frame_count += 1
        
        # 1. 更新已有追踪（标记为未匹配）
        for track in self.tracks.values():
            track.frames_since_last_seen += 1
        
        # 2. 匹配检测到已有追踪（IoU匹配）
        matched_track_ids = set()
        unmatched_detections = []
        
        for det in detections:
            best_iou = 0
            best_track_id = None
            
            for track_id, track in self.tracks.items():
                if not track.is_active:
                    continue
                if track.last_bbox is None:
                    continue
                iou = self._compute_iou(det.bbox, track.last_bbox)
                if iou > best_iou and iou > self.iou_threshold:
                    best_iou = iou
                    best_track_id = track_id
            
            if best_track_id is not None:
                # 匹配到已有追踪
                track = self.tracks[best_track_id]
                track.last_bbox = det.bbox
                track.positions.append(self._get_center(det.bbox))
                track.frames_since_last_seen = 0
                det.track_id = best_track_id
                matched_track_ids.add(best_track_id)
            else:
                unmatched_detections.append(det)
        
        # 3. 为新检测创建新追踪
        for det in unmatched_detections:
            track = TrackedObject(
                track_id=self.next_id,
                class_id=det.class_id,
                class_name=det.class_name,
                last_bbox=det.bbox,
                positions=[self._get_center(det.bbox)],
            )
            self.tracks[self.next_id] = track
            det.track_id = self.next_id
            self.next_id += 1
        
        # 4. 清理丢失的追踪
        to_remove = [
            tid for tid, track in self.tracks.items()
            if track.frames_since_last_seen > self.max_lost_frames
        ]
        for tid in to_remove:
            self.tracks[tid].is_active = False
        
        return detections
    
    def _compute_iou(self, bbox1: List[int], bbox2: List[int]) -> float:
        """计算两个边界框的IoU"""
        x1 = max(bbox1[0], bbox2[0])
        y1 = max(bbox1[1], bbox2[1])
        x2 = min(bbox1[2], bbox2[2])
        y2 = min(bbox1[3], bbox2[3])
        
        if x2 <= x1 or y2 <= y1:
            return 0.0
        
        intersection = (x2 - x1) * (y2 - y1)
        area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
        area2 = (bbox2[2] - bbox2[0]) * (bbox2[3] - bbox2[1])
        union = area1 + area2 - intersection
        
        return intersection / union if union > 0 else 0.0
    
    def _get_center(self, bbox: List[int]) -> Tuple[int, int]:
        return ((bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2)


class VisionDetector:
    """
    视觉检测器：YOLO + 追踪
    支持视频文件分析和实时流处理
    """
    
    # COCO类别映射（仅保留人相关）
    PERSON_CLASSES = {0: "person"}
    
    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "cpu",
        conf_threshold: float = 0.5,
        iou_threshold: float = 0.45,
    ):
        self.model_path = model_path or getattr(settings, "YOLO_MODEL_PATH", "yolov8n.pt")
        self.device = device
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold
        
        self.model = None
        self.tracker = SimpleTracker(iou_threshold=0.3)
        self._initialized = False
    
    def initialize(self):
        """延迟加载模型（避免启动时加载）"""
        if self._initialized:
            return
        try:
            self.model = YOLO(self.model_path)
            self._initialized = True
            logger.info(f"YOLO model loaded: {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to load YOLO model: {e}")
            raise
    
    def process_frame(self, frame: np.ndarray) -> List[Detection]:
        """
        处理单帧图像，返回检测和追踪结果
        """
        if not self._initialized:
            self.initialize()
        
        if frame is None or frame.size == 0:
            return []
        
        detections = []
        try:
            # YOLO推理
            results = self.model(frame, conf=self.conf_threshold, device=self.device, verbose=False)
            
            if results and len(results) > 0 and results[0].boxes is not None:
                boxes = results[0].boxes
                for box in boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
                    conf = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    
                    # 只检测人（类ID 0）
                    if cls_id != 0:
                        continue
                    
                    detections.append(Detection(
                        bbox=[x1, y1, x2, y2],
                        confidence=conf,
                        class_id=cls_id,
                        class_name="person",
                    ))
            
            # 追踪
            detections = self.tracker.update(detections)
            
        except Exception as e:
            logger.error(f"Frame processing error: {e}")
        
        return detections
    
    def analyze_video(
        self,

        video_source: Any,
        zone_definitions: Optional[Dict[str, List[Tuple[int, int]]]] = None,
        process_interval: int = 3,  # 每N帧处理一次（帧模式，默认保留兼容）
        process_every_seconds: Optional[float] = None,  # 每N秒处理一次（时间模式，实时流推荐）
        max_frames: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        分析视频文件或流

        支持两种采样模式：
        - **帧模式**（process_every_seconds=None）：每 process_interval 帧处理一次，用于历史视频文件分析。
        - **时间模式**（process_every_seconds 有值）：按实际时间间隔采样，用于实时摄像头流。

        Args:
            video_source: 视频文件路径或摄像头ID（如0）
            zone_definitions: 区域定义 {"zone_name": [(x1,y1), (x2,y2), ...]}
            process_interval: 处理间隔（帧），帧模式生效
            process_every_seconds: 处理间隔（秒），时间模式生效；None 则使用帧模式
            max_frames: 最大处理帧数

        Returns:
            {
                "total_frames": int,
                "detections": List[Detection],
                "events": List[Dict],
                "zone_visits": Dict[str, int]
            }
        """
        if not self._initialized:
            self.initialize()

        cap = open_video_capture(video_source)
        if not cap.isOpened():
            logger.error(f"Cannot open video source: {video_source}")
            return {"error": "Cannot open video source"}

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if isinstance(video_source, str) else 0

        all_detections = []
        events = []
        zone_visits = defaultdict(int)
        frame_count = 0
        processed_count = 0

        _use_time_mode = process_every_seconds is not None
        _last_process_time: Optional[float] = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_count += 1

                if _use_time_mode:
                    # 时间模式：按实际时间间隔采样
                    now = time.time()
                    if _last_process_time is not None and (now - _last_process_time) < process_every_seconds:
                        continue
                    _last_process_time = now
                else:
                    # 帧模式：按帧间隔采样（保留兼容）
                    if frame_count % process_interval != 0:
                        continue

                # 限制最大帧数
                if max_frames and processed_count >= max_frames:
                    break

                # 处理帧
                detections = self.process_frame(frame)
                all_detections.extend(detections)

                # 区域分析
                if zone_definitions:
                    for det in detections:
                        center = self.tracker._get_center(det.bbox)
                        for zone_name, zone_points in zone_definitions.items():
                            if self._point_in_polygon(center, zone_points):
                                zone_visits[zone_name] += 1
                                events.append({
                                    "type": "zone_visit",
                                    "track_id": det.track_id,
                                    "zone": zone_name,
                                    "frame": frame_count,
                                    "timestamp": frame_count / fps if fps > 0 else frame_count,
                                })

                processed_count += 1

                # 日志进度（每100帧）
                if processed_count % 100 == 0:
                    logger.info(f"Processed {processed_count} frames")

        except Exception as e:
            logger.error(f"Video analysis error: {e}")

        finally:
            cap.release()

        logger.info(f"Video analysis complete: {processed_count} frames processed, {len(all_detections)} detections")

        return {
            "total_frames": total_frames,
            "processed_frames": processed_count,
            "fps": fps,
            "detections": all_detections,
            "events": events,
            "zone_visits": dict(zone_visits),
            "unique_tracks": len(self.tracker.tracks),
        }
    
    def _point_in_polygon(self, point: Tuple[int, int], polygon: List[Tuple[int, int]]) -> bool:
        """射线法判断点是否在多边形内"""
        x, y = point
        n = len(polygon)
        inside = False
        p1x, p1y = polygon[0]
        for i in range(1, n + 1):
            p2x, p2y = polygon[i % n]
            if y > min(p1y, p2y):
                if y <= max(p1y, p2y):
                    if x <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside
    
    def reset(self):
        """重置追踪器"""
        self.tracker = SimpleTracker(iou_threshold=0.3)
        logger.info("Tracker reset")
