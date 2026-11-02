#!/usr/bin/env python
"""
实时进店监测模拟测试。

用视频文件模拟摄像头实时帧流，用图片模拟会员人脸注册，
逐帧调用 RealtimeEntryPipeline.process_frame() 进行实时检测。

用法：
    # 使用视频和图片模拟实时监测
    python test/test_realtime_simulation.py --video path/to/video.mp4 --image path/to/face.jpg

    # 仅使用视频（纯检测模式，无会员识别）
    python test/test_realtime_simulation.py --video path/to/video.mp4

    # 使用合成数据（无需真实视频文件）
    python test/test_realtime_simulation.py --synthetic

    # 运行单元测试
    python -m unittest test.test_realtime_simulation -v

功能：
    1. 使用视频文件模拟摄像头实时帧流
    2. 使用图片注册模拟会员人脸（基于 OpenCV 直方图对比）
    3. 逐帧调用 process_frame() 进行 YOLO 检测 + 追踪 + 会员识别
    4. 实时输出每帧检测结果、确认进店事件、会员/匿名判断
    5. 保存标注帧和 JSON 报告到输出目录
    6. 支持 YOLO 模型不可用时自动降级为合成检测器
"""

from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ImageFaceRecognizer: 基于参考图片的模拟人脸识别器
# ---------------------------------------------------------------------------

class ImageFaceRecognizer:
    """基于参考图片的模拟人脸识别器。

    使用 OpenCV HSV 直方图对比来判断裁剪区域是否与注册图片相似。
    用于演示和测试，不依赖 face_recognition 库。

    流程：
        1. 初始化时加载参考图片并计算颜色直方图
        2. recognize_face() 被调用时，将裁剪区域与参考图片对比
        3. 相似度超过阈值则返回会员信息，否则返回 None
    """

    def __init__(
        self,
        reference_image_path: str,
        member_id: str = "M_SIM_001",
        member_name: str = "模拟会员",
        match_threshold: float = 0.3,
        match_confidence: float = 0.88,
    ):
        self.reference_image = cv2.imread(reference_image_path)
        if self.reference_image is None:
            raise ValueError(f"无法读取参考图片: {reference_image_path}")

        self.member_id = member_id
        self.member_name = member_name
        self.match_threshold = match_threshold
        self.match_confidence = match_confidence
        self.call_count = 0
        self.match_count = 0

        self._ref_hist = self._compute_histogram(self.reference_image)
        ref_h, ref_w = self.reference_image.shape[:2]
        logger.info(
            "参考图片已加载: %s (%dx%d), member_id=%s",
            reference_image_path, ref_w, ref_h, member_id,
        )

    @staticmethod
    def _compute_histogram(image: np.ndarray) -> np.ndarray:
        """计算 HSV 颜色直方图作为图像特征。"""
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def recognize_face(
        self,
        image: np.ndarray,
        allow_fallback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """模拟人脸识别：通过直方图相似度判断。

        Args:
            image: 裁剪后的 BGR 图像区域。
            allow_fallback: 是否允许降级结果（本模拟器忽略此参数）。

        Returns:
            匹配成功返回会员信息 dict，失败返回 None。
        """
        self.call_count += 1

        if image is None or image.size == 0:
            return None

        try:
            ref_h, ref_w = self.reference_image.shape[:2]
            crop_resized = cv2.resize(image, (ref_w, ref_h), interpolation=cv2.INTER_AREA)
            crop_hist = self._compute_histogram(crop_resized)
            similarity = float(cv2.compareHist(self._ref_hist, crop_hist, cv2.HISTCMP_CORREL))

            logger.debug(
                "recognize_face: call=%d, similarity=%.4f, threshold=%.2f",
                self.call_count, similarity, self.match_threshold,
            )

            if similarity >= self.match_threshold:
                self.match_count += 1
                return {
                    "member_id": self.member_id,
                    "name": self.member_name,
                    "tier": "gold",
                    "points": 1000,
                    "confidence": self.match_confidence,
                    "similarity_distance": 1.0 - similarity,
                }

            return None
        except Exception as e:
            logger.error("人脸识别错误: %s", e)
            return None


# ---------------------------------------------------------------------------
# SyntheticDetector: 无 YOLO 模型时的合成检测器
# ---------------------------------------------------------------------------

class SyntheticDetector:
    """合成检测器：生成模拟的 person 检测结果。

    当 YOLO 模型不可用时使用，生成移动的 person 检测框来模拟视频中的行人。
    每个模拟行人有固定的 track_id，在画面内移动并边界反弹。
    """

    def __init__(self, num_persons: int = 2, frame_size: tuple = (640, 480)):
        from agent.legacy.detector import Detection

        self.Detection = Detection
        self.num_persons = num_persons
        self.frame_w, self.frame_h = frame_size
        self._frame_count = 0
        self._persons: List[Dict[str, Any]] = []
        self._init_persons()
        self.reset_called = False

    def _init_persons(self):
        """初始化模拟行人。"""
        self._persons = []
        for i in range(self.num_persons):
            self._persons.append({
                "x": 50 + i * 200,
                "y": 100 + i * 50,
                "vx": 3 + i * 2,
                "vy": 1,
                "w": 80 + i * 20,
                "h": 200,
                "id": i + 1,
            })

    def process_frame(self, frame: np.ndarray) -> List:
        """生成移动的 person 检测。"""
        self._frame_count += 1
        detections = []

        for p in self._persons:
            p["x"] += p["vx"]
            p["y"] += p["vy"]

            if p["x"] < 0 or p["x"] + p["w"] > self.frame_w:
                p["vx"] = -p["vx"]
                p["x"] = max(0, min(p["x"], self.frame_w - p["w"]))
            if p["y"] < 0 or p["y"] + p["h"] > self.frame_h:
                p["vy"] = -p["vy"]
                p["y"] = max(0, min(p["y"], self.frame_h - p["h"]))

            conf = 0.70 + 0.15 * np.sin(self._frame_count * 0.1 + p["id"])
            conf = float(np.clip(conf, 0.50, 0.95))

            det = self.Detection(
                bbox=[int(p["x"]), int(p["y"]),
                      int(p["x"] + p["w"]), int(p["y"] + p["h"])],
                confidence=conf,
                class_id=0,
                class_name="person",
                track_id=p["id"],
            )
            detections.append(det)

        return detections

    def initialize(self):
        pass

    def reset(self):
        self.reset_called = True
        self._frame_count = 0
        self._init_persons()


# ---------------------------------------------------------------------------
# 可视化工具
# ---------------------------------------------------------------------------

# 颜色常量 (BGR)
COLOR_DETECT = (0, 255, 0)      # 绿色 - 检测框
COLOR_MEMBER = (255, 0, 0)      # 蓝色 - 会员
COLOR_ANONYMOUS = (0, 165, 255)  # 橙色 - 匿名
COLOR_INFO = (255, 255, 255)    # 白色 - 信息文字


def draw_annotations(frame: np.ndarray, result: dict) -> np.ndarray:
    """在帧上绘制检测框和标注信息。

    Args:
        frame: 原始 BGR 帧。
        result: process_frame() 返回的结构化 dict。

    Returns:
        标注后的帧（副本）。
    """
    annotated = frame.copy()

    # 绘制所有检测
    for det in result.get("detections", []):
        bbox = det.get("bbox", [])
        if len(bbox) == 4:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            track_id = det.get("track_id", "?")
            conf = det.get("confidence", 0)

            cv2.rectangle(annotated, (x1, y1), (x2, y2), COLOR_DETECT, 2)
            label = f"#{track_id} ({conf:.2f})"
            cv2.putText(annotated, label, (x1, max(y1 - 8, 15)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_DETECT, 2)

    # 绘制确认的进店事件
    for entry in result.get("confirmed_entries", []):
        det = entry.get("detection", {})
        bbox = det.get("bbox", [])
        if len(bbox) == 4:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            track_id = entry.get("track_id", "?")
            entry_type = entry.get("entry_type", "unknown")

            if entry_type == "member":
                cv2.rectangle(annotated, (x1, y1), (x2, y2), COLOR_MEMBER, 3)
                sess = entry.get("session", {})
                label = f"MEMBER #{track_id} {sess.get('member_id', '?')}"
                cv2.putText(annotated, label, (x1, y2 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_MEMBER, 2)
            else:
                cv2.rectangle(annotated, (x1, y1), (x2, y2), COLOR_ANONYMOUS, 3)
                reason = entry.get("reason", "?")
                label = f"ANON #{track_id} ({reason})"
                cv2.putText(annotated, label, (x1, y2 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_ANONYMOUS, 2)

    # 统计信息
    info_lines = [
        f"Active tracks: {result.get('active_tracks', 0)}",
        f"Confirmed: {len(result.get('confirmed_entries', []))}",
    ]
    for i, line in enumerate(info_lines):
        cv2.putText(annotated, line, (10, 25 + i * 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_INFO, 2)

    return annotated


# ---------------------------------------------------------------------------
# 视频模拟主逻辑
# ---------------------------------------------------------------------------

def run_video_simulation(
    video_path: str,
    image_path: Optional[str] = None,
    output_dir: str = "outputs/realtime_simulation",
    stability_threshold: int = 3,
    process_every_seconds: float = 0.0,
    max_frames: int = 0,
    save_annotated: bool = True,
    save_interval: int = 10,
    member_match_threshold: float = 0.75,
    allow_fallback_match: bool = False,
) -> dict:
    """使用视频文件模拟实时进店监测。

    Args:
        video_path: 视频文件路径。
        image_path: 会员人脸图片路径（可选，不提供则纯检测模式）。
        output_dir: 输出目录。
        stability_threshold: track 稳定性阈值（连续多少帧确认）。
        process_every_seconds: 帧处理间隔（秒），0 表示每帧都处理。
        max_frames: 最大处理帧数，0 表示全部。
        save_annotated: 是否保存标注帧。
        save_interval: 每隔多少帧保存一次标注帧。
        member_match_threshold: 会员匹配置信度阈值。
        allow_fallback_match: 是否允许降级匹配。

    Returns:
        运行结果摘要 dict。
    """
    from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 初始化检测器 ---
    detector = None
    use_synthetic = False

    try:
        from agent.legacy.detector import VisionDetector
        model_path = str(PROJECT_ROOT / "data/models/yolov8n.pt")
        detector = VisionDetector(model_path=model_path, device="cpu", conf_threshold=0.4)
        detector.initialize()
        print(f"[检测器] YOLO 模型加载成功: {model_path}")
    except Exception as e:
        print(f"[检测器] YOLO 模型加载失败: {e}")
        print(f"[检测器] 自动切换到合成检测器模式")
        use_synthetic = True
        detector = SyntheticDetector(num_persons=3, frame_size=(640, 480))

    # --- 初始化人脸识别器 ---
    face_recognizer = None
    if image_path and Path(image_path).exists():
        try:
            face_recognizer = ImageFaceRecognizer(
                reference_image_path=image_path,
                member_id="M_SIM_001",
                member_name="模拟会员",
                match_threshold=0.3,
                match_confidence=0.88,
            )
            print(f"[人脸识别] 参考图片加载成功: {image_path}")
        except Exception as e:
            print(f"[人脸识别] 参考图片加载失败: {e}")
            face_recognizer = None
    elif image_path:
        print(f"[人脸识别] 图片不存在: {image_path}，将使用纯检测模式")
    else:
        print(f"[人脸识别] 未提供图片，将使用纯检测模式（所有进店均为匿名）")

    # --- 回调收集器 ---
    member_entries: List[dict] = []
    anonymous_visits: List[dict] = []
    new_customers: List[int] = []

    def on_member_entry(session_dict):
        member_entries.append(session_dict)
        print(f"\n  *** [会员进店] Track ID: {session_dict['track_id']}, "
              f"会员: {session_dict['member_id']}, "
              f"置信度: {session_dict['confidence']:.2f}, "
              f"Session: {session_dict['session_id']}\n")

    def on_anonymous_visit(track_id, detection, reason):
        anonymous_visits.append({"track_id": track_id, "reason": reason})
        print(f"\n  --- [匿名访客] Track ID: {track_id}, 原因: {reason}\n")

    def on_new_customer(track_id, detection, frame):
        new_customers.append(track_id)
        print(f"\n  +++ [新顾客] Track ID: {track_id}, "
              f"置信度: {detection.get('confidence', 0):.2f}\n")

    # --- 创建 Pipeline ---
    pipeline = RealtimeEntryPipeline(
        detector=detector,
        face_recognizer=face_recognizer,
        stability_threshold=stability_threshold,
        member_match_threshold=member_match_threshold,
        process_every_seconds=process_every_seconds,
        allow_fallback_match=allow_fallback_match,
        camera_id="sim_camera_01",
        on_new_customer=on_new_customer,
        on_member_entry=on_member_entry,
        on_anonymous_visit=on_anonymous_visit,
    )

    # --- 打开视频 ---
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"[错误] 无法打开视频: {video_path}")
        return {"error": f"Cannot open video: {video_path}"}

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    print(f"\n{'=' * 70}")
    print(f"  视频信息: {video_path}")
    print(f"  分辨率: {frame_w}x{frame_h}, FPS: {fps:.1f}, 总帧数: {total_frames}")
    print(f"  配置: stability={stability_threshold}, "
          f"match_threshold={member_match_threshold}, "
          f"fallback={allow_fallback_match}")
    print(f"  检测器: {'YOLO' if not use_synthetic else '合成'}, "
          f"人脸识别: {'启用' if face_recognizer else '未启用'}")
    print(f"{'=' * 70}")
    print(f"\n开始模拟实时监测...\n")

    frame_count = 0
    processed_count = 0
    last_process_time: Optional[float] = None

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1
            if max_frames > 0 and frame_count > max_frames:
                print(f"\n[停止] 已达到最大帧数限制: {max_frames}")
                break

            # 时间模式采样
            now = time.time()
            if process_every_seconds > 0 and last_process_time is not None:
                if now - last_process_time < process_every_seconds:
                    continue
            last_process_time = now

            # 处理单帧
            result = pipeline.process_frame(frame)
            processed_count += 1

            # 实时输出
            detections = result.get("detections", [])
            confirmed = result.get("confirmed_entries", [])
            active = result.get("active_tracks", 0)

            det_summary = ", ".join(
                f"#{d.get('track_id', '?')}({d.get('confidence', 0):.2f})"
                for d in detections
            ) if detections else "无"

            frame_label = f"{frame_count:>5d}"
            if total_frames > 0:
                frame_label += f"/{total_frames:<5d}"
            else:
                frame_label += "      "

            print(
                f"  帧 {frame_label} | "
                f"检测: [{det_summary}] | "
                f"活跃: {active} | "
                f"确认: {len(confirmed)}"
            )

            # 保存标注帧
            if save_annotated and (processed_count % save_interval == 0 or confirmed):
                annotated = draw_annotations(frame, result)
                frame_path = out_dir / f"frame_{frame_count:06d}.jpg"
                cv2.imwrite(str(frame_path), annotated)

    except KeyboardInterrupt:
        print(f"\n\n[中断] 用户手动停止")
    finally:
        cap.release()

    # --- 汇总结果 ---
    total_members = len(member_entries)
    total_anonymous = len(anonymous_visits)
    total_new = len(new_customers)

    summary = {
        "video_path": video_path,
        "image_path": image_path or "N/A",
        "detector_mode": "synthetic" if use_synthetic else "yolo",
        "face_recognizer_enabled": face_recognizer is not None,
        "camera_id": "sim_camera_01",
        "stability_threshold": stability_threshold,
        "member_match_threshold": member_match_threshold,
        "allow_fallback_match": allow_fallback_match,
        "total_frames_read": frame_count,
        "total_frames_processed": processed_count,
        "total_members": total_members,
        "total_anonymous": total_anonymous,
        "total_new_customers": total_new,
        "member_entries": member_entries,
        "anonymous_visits": anonymous_visits,
        "new_customers": new_customers,
        "fps": fps,
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n{'=' * 70}")
    print(f"  模拟监测完成")
    print(f"{'=' * 70}")

    print(f"\n  [统计摘要]")
    print(f"    总读取帧数:   {frame_count}")
    print(f"    总处理帧数:   {processed_count}")
    print(f"    会员进店:     {total_members}")
    print(f"    匿名访客:     {total_anonymous}")
    print(f"    新顾客(无FR): {total_new}")

    if face_recognizer:
        print(f"    人脸识别调用: {face_recognizer.call_count}")
        print(f"    人脸匹配成功: {face_recognizer.match_count}")

    if member_entries:
        print(f"\n  [会员进店详情]")
        for entry in member_entries:
            print(f"    - Track ID: {entry['track_id']}, "
                  f"会员: {entry['member_id']}, "
                  f"置信度: {entry['confidence']:.2f}, "
                  f"Session: {entry['session_id']}")

    if anonymous_visits:
        print(f"\n  [匿名访客详情]")
        for visit in anonymous_visits:
            print(f"    - Track ID: {visit['track_id']}, 原因: {visit['reason']}")

    # 保存 JSON 报告
    json_path = out_dir / f"simulation_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  [输出] JSON 报告: {json_path}")
    print(f"  [输出] 标注帧目录: {out_dir}")

    return summary


def run_synthetic_simulation(
    num_persons: int = 3,
    num_frames: int = 30,
    image_path: Optional[str] = None,
    output_dir: str = "outputs/realtime_simulation",
    stability_threshold: int = 3,
    member_match_threshold: float = 0.75,
) -> dict:
    """使用合成数据模拟实时监测（无需真实视频文件）。

    Args:
        num_persons: 模拟行人数。
        num_frames: 模拟帧数。
        image_path: 会员人脸图片路径（可选）。
        output_dir: 输出目录。
        stability_threshold: track 稳定性阈值。
        member_match_threshold: 会员匹配置信度阈值。

    Returns:
        运行结果摘要 dict。
    """
    from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frame_w, frame_h = 640, 480
    detector = SyntheticDetector(num_persons=num_persons, frame_size=(frame_w, frame_h))

    # 人脸识别器
    face_recognizer = None
    if image_path and Path(image_path).exists():
        try:
            face_recognizer = ImageFaceRecognizer(
                reference_image_path=image_path,
                match_threshold=0.7,
                match_confidence=0.90,
            )
            print(f"[人脸识别] 参考图片加载成功: {image_path}")
        except Exception as e:
            print(f"[人脸识别] 加载失败: {e}")

    # 回调
    member_entries: List[dict] = []
    anonymous_visits: List[dict] = []

    pipeline = RealtimeEntryPipeline(
        detector=detector,
        face_recognizer=face_recognizer,
        stability_threshold=stability_threshold,
        member_match_threshold=member_match_threshold,
        camera_id="synth_camera_01",
        on_member_entry=lambda s: member_entries.append(s),
        on_anonymous_visit=lambda tid, d, r: anonymous_visits.append({"track_id": tid, "reason": r}),
    )

    print(f"\n{'=' * 70}")
    print(f"  合成模拟: {num_persons} 人, {num_frames} 帧, "
          f"stability={stability_threshold}")
    print(f"  人脸识别: {'启用' if face_recognizer else '未启用'}")
    print(f"{'=' * 70}\n")

    for i in range(num_frames):
        frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
        result = pipeline.process_frame(frame)

        detections = result.get("detections", [])
        confirmed = result.get("confirmed_entries", [])
        active = result.get("active_tracks", 0)

        det_summary = ", ".join(
            f"#{d.get('track_id', '?')}({d.get('confidence', 0):.2f})"
            for d in detections
        ) if detections else "无"

        print(
            f"  帧 {i + 1:>3d}/{num_frames:<3d} | "
            f"检测: [{det_summary}] | "
            f"活跃: {active} | "
            f"确认: {len(confirmed)}"
        )

        if confirmed:
            annotated = draw_annotations(frame, result)
            cv2.imwrite(str(out_dir / f"synth_frame_{i + 1:04d}.jpg"), annotated)

    total_members = len(member_entries)
    total_anonymous = len(anonymous_visits)

    summary = {
        "mode": "synthetic",
        "num_persons": num_persons,
        "num_frames": num_frames,
        "detector_mode": "synthetic",
        "face_recognizer_enabled": face_recognizer is not None,
        "stability_threshold": stability_threshold,
        "member_match_threshold": member_match_threshold,
        "total_members": total_members,
        "total_anonymous": total_anonymous,
        "member_entries": member_entries,
        "anonymous_visits": anonymous_visits,
        "timestamp": datetime.now().isoformat(),
    }

    print(f"\n{'=' * 70}")
    print(f"  合成模拟完成")
    print(f"{'=' * 70}")
    print(f"    会员进店: {total_members}")
    print(f"    匿名访客: {total_anonymous}")
    if face_recognizer:
        print(f"    人脸识别调用: {face_recognizer.call_count}")
        print(f"    人脸匹配成功: {face_recognizer.match_count}")

    json_path = out_dir / f"synth_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  [输出] JSON 报告: {json_path}")

    return summary


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="实时进店监测模拟测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 使用视频和图片模拟实时监测
  python test/test_realtime_simulation.py --video video.mp4 --image face.jpg

  # 仅使用视频（纯检测模式）
  python test/test_realtime_simulation.py --video video.mp4

  # 使用合成数据（无需真实视频文件）
  python test/test_realtime_simulation.py --synthetic --image face.jpg

  # 限制处理帧数
  python test/test_realtime_simulation.py --video video.mp4 --max-frames 100

  # 运行单元测试
  python -m unittest test.test_realtime_simulation -v
        """,
    )
    parser.add_argument("--video", "-v", default=None,
                        help="视频文件路径（模拟摄像头输入）")
    parser.add_argument("--image", "-i", default=None,
                        help="会员人脸图片路径（用于模拟人脸识别）")
    parser.add_argument("--output-dir", "-o",
                        default="outputs/realtime_simulation",
                        help="输出目录（默认: outputs/realtime_simulation）")
    parser.add_argument("--stability-threshold", "-s", type=int, default=3,
                        help="track 稳定性阈值（默认: 3）")
    parser.add_argument("--process-every-seconds", type=float, default=0.0,
                        help="帧处理间隔秒数（默认: 0=每帧都处理）")
    parser.add_argument("--max-frames", type=int, default=0,
                        help="最大处理帧数（默认: 0=全部）")
    parser.add_argument("--no-save-frames", action="store_true",
                        help="不保存标注帧")
    parser.add_argument("--save-interval", type=int, default=10,
                        help="标注帧保存间隔（默认: 每10帧）")
    parser.add_argument("--match-threshold", type=float, default=0.75,
                        help="会员匹配置信度阈值（默认: 0.75）")
    parser.add_argument("--allow-fallback", action="store_true",
                        help="允许降级 mock 结果视为真实会员")
    parser.add_argument("--synthetic", action="store_true",
                        help="使用合成数据模拟（无需真实视频文件）")
    parser.add_argument("--synth-persons", type=int, default=3,
                        help="合成模式行人数（默认: 3）")
    parser.add_argument("--synth-frames", type=int, default=30,
                        help="合成模式帧数（默认: 30）")

    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 合成模式
    if args.synthetic:
        print("[启动] 合成数据模拟模式")
        run_synthetic_simulation(
            num_persons=args.synth_persons,
            num_frames=args.synth_frames,
            image_path=args.image,
            output_dir=args.output_dir,
            stability_threshold=args.stability_threshold,
            member_match_threshold=args.match_threshold,
        )
        return

    # 视频模式
    video_path = args.video

    # 查找默认视频文件
    if video_path is None:
        for name in os.listdir(str(PROJECT_ROOT)):
            if name.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
                video_path = str(PROJECT_ROOT / name)
                print(f"[提示] 自动发现视频文件: {video_path}")
                break

    if video_path is None or not Path(video_path).exists():
        print(f"[错误] 未找到视频文件")
        print(f"  请通过 --video 参数指定视频路径")
        print(f"  或使用 --synthetic 参数使用合成数据")
        print(f"  项目根目录: {PROJECT_ROOT}")
        parser.print_help()
        sys.exit(1)

    # 查找默认图片
    image_path = args.image
    if image_path is None:
        default_image = PROJECT_ROOT / "test_customer_profile_input.jpg"
        if default_image.exists():
            image_path = str(default_image)
            print(f"[提示] 自动发现图片文件: {image_path}")

    print(f"\n[启动] 实时进店监测模拟测试")
    print(f"  视频: {video_path}")
    print(f"  图片: {image_path or '未提供'}")
    print()

    run_video_simulation(
        video_path=video_path,
        image_path=image_path,
        output_dir=args.output_dir,
        stability_threshold=args.stability_threshold,
        process_every_seconds=args.process_every_seconds,
        max_frames=args.max_frames,
        save_annotated=not args.no_save_frames,
        save_interval=args.save_interval,
        member_match_threshold=args.match_threshold,
        allow_fallback_match=args.allow_fallback,
    )


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------

import unittest


class RealtimeSimulationTests(unittest.TestCase):
    """实时监测模拟的单元测试。"""

    def setUp(self):
        from _test_bootstrap import install_runtime_stubs
        install_runtime_stubs()

    def test_image_face_recognizer_loads_real_image(self):
        """ImageFaceRecognizer 能正确加载真实图片。"""
        image_path = PROJECT_ROOT / "test_customer_profile_input.jpg"
        if not image_path.exists():
            self.skipTest(f"测试图片不存在: {image_path}")

        recognizer = ImageFaceRecognizer(
            reference_image_path=str(image_path),
            member_id="TEST_M001",
        )
        self.assertEqual(recognizer.member_id, "TEST_M001")
        self.assertIsNotNone(recognizer.reference_image)
        self.assertEqual(recognizer.call_count, 0)

    def test_image_face_recognizer_matches_same_image(self):
        """ImageFaceRecognizer 对相同图片返回匹配。"""
        image_path = PROJECT_ROOT / "test_customer_profile_input.jpg"
        if not image_path.exists():
            self.skipTest(f"测试图片不存在: {image_path}")

        recognizer = ImageFaceRecognizer(
            reference_image_path=str(image_path),
            match_threshold=0.0,
        )
        result = recognizer.recognize_face(recognizer.reference_image)

        self.assertIsNotNone(result)
        self.assertEqual(result["member_id"], "M_SIM_001")
        self.assertGreater(result["confidence"], 0.5)
        self.assertEqual(recognizer.call_count, 1)
        self.assertEqual(recognizer.match_count, 1)

    def test_image_face_recognizer_returns_none_for_empty(self):
        """ImageFaceRecognizer 对空输入返回 None。"""
        image_path = PROJECT_ROOT / "test_customer_profile_input.jpg"
        if not image_path.exists():
            self.skipTest(f"测试图片不存在: {image_path}")

        recognizer = ImageFaceRecognizer(reference_image_path=str(image_path))
        self.assertIsNone(recognizer.recognize_face(None))
        self.assertIsNone(recognizer.recognize_face(np.array([])))

    def test_synthetic_detector_generates_detections(self):
        """SyntheticDetector 能生成 person 检测。"""
        detector = SyntheticDetector(num_persons=2, frame_size=(640, 480))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        detections = detector.process_frame(frame)

        self.assertEqual(len(detections), 2)
        for det in detections:
            self.assertEqual(det.class_name, "person")
            self.assertIsNotNone(det.track_id)
            self.assertGreater(det.confidence, 0)

    def test_synthetic_detector_track_ids_stable(self):
        """SyntheticDetector 的 track_id 在多帧间保持稳定。"""
        detector = SyntheticDetector(num_persons=2, frame_size=(640, 480))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        dets1 = detector.process_frame(frame)
        dets2 = detector.process_frame(frame)

        ids1 = {d.track_id for d in dets1}
        ids2 = {d.track_id for d in dets2}
        self.assertEqual(ids1, ids2)

    def test_synthetic_detector_reset(self):
        """SyntheticDetector reset 清除状态。"""
        detector = SyntheticDetector(num_persons=1, frame_size=(640, 480))
        frame = np.zeros((480, 640, 3), dtype=np.uint8)

        detector.process_frame(frame)
        detector.process_frame(frame)
        detector.reset()

        self.assertTrue(detector.reset_called)
        self.assertEqual(detector._frame_count, 0)

    def test_draw_annotations_returns_annotated_frame(self):
        """draw_annotations 返回与原帧同尺寸的标注帧。"""
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = {
            "detections": [
                {"bbox": [10, 10, 100, 200], "track_id": 1, "confidence": 0.85}
            ],
            "confirmed_entries": [
                {
                    "track_id": 1,
                    "detection": {"bbox": [10, 10, 100, 200]},
                    "entry_type": "member",
                    "session": {"member_id": "M001", "confidence": 0.9},
                    "reason": None,
                }
            ],
            "active_tracks": 1,
        }

        annotated = draw_annotations(frame, result)
        self.assertEqual(annotated.shape, frame.shape)
        self.assertFalse(np.array_equal(annotated, frame))

    def test_pipeline_with_synthetic_detector_and_image_fr(self):
        """端到端：合成检测器 + 图片人脸识别器 + process_frame。"""
        from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline

        image_path = PROJECT_ROOT / "test_customer_profile_input.jpg"
        if not image_path.exists():
            self.skipTest(f"测试图片不存在: {image_path}")

        detector = SyntheticDetector(num_persons=1, frame_size=(640, 480))
        recognizer = ImageFaceRecognizer(
            reference_image_path=str(image_path),
            match_threshold=0.0,
            match_confidence=0.90,
        )

        member_entries = []
        pipeline = RealtimeEntryPipeline(
            detector=detector,
            face_recognizer=recognizer,
            stability_threshold=1,
            member_match_threshold=0.75,
            on_member_entry=lambda s: member_entries.append(s),
        )

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = pipeline.process_frame(frame)

        self.assertIn("confirmed_entries", result)
        self.assertIn("detections", result)
        self.assertIn("active_tracks", result)

        if result["confirmed_entries"]:
            entry = result["confirmed_entries"][0]
            self.assertEqual(entry["entry_type"], "member")
            self.assertEqual(len(member_entries), 1)

    def test_pipeline_synthetic_anonymous_mode(self):
        """端到端：合成检测器 + 无人脸识别 → 匿名模式。"""
        from pipelines.legacy.realtime_entry_pipeline import RealtimeEntryPipeline

        detector = SyntheticDetector(num_persons=2, frame_size=(640, 480))
        new_customers = []

        pipeline = RealtimeEntryPipeline(
            detector=detector,
            face_recognizer=None,
            stability_threshold=1,
            on_new_customer=lambda tid, d, f: new_customers.append(tid),
        )

        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = pipeline.process_frame(frame)

        self.assertEqual(len(result["confirmed_entries"]), 2)
        for entry in result["confirmed_entries"]:
            self.assertEqual(entry["entry_type"], "anonymous")
            self.assertEqual(entry["reason"], "face_recognizer_unavailable")
        self.assertEqual(len(new_customers), 2)

    def test_synthetic_simulation_produces_summary(self):
        """run_synthetic_simulation 返回有效摘要。"""
        summary = run_synthetic_simulation(
            num_persons=2,
            num_frames=5,
            output_dir="outputs/test_synth",
            stability_threshold=1,
        )

        self.assertIn("total_members", summary)
        self.assertIn("total_anonymous", summary)
        self.assertIn("member_entries", summary)
        self.assertIn("anonymous_visits", summary)
        self.assertEqual(summary["num_frames"], 5)
        self.assertEqual(summary["num_persons"], 2)

    def test_no_llm_imports_in_simulation(self):
        """AST 检查：本文件不导入 LLM 相关模块。"""
        source_path = Path(__file__)
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)

        llm_patterns = ["openai", "anthropic", "langchain", "langgraph", "ollama", "gpt", "claude"]
        for imp in imports:
            for pattern in llm_patterns:
                self.assertNotIn(
                    pattern, imp.lower(),
                    f"LLM-related import found: {imp}",
                )


if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        # unittest 模式: python test_realtime_simulation.py TestClass.test_method
        unittest.main()
    elif len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        main()
    elif len(sys.argv) == 1:
        # 无参数：运行 unittest
        unittest.main()
    else:
        # 有参数：CLI 模式
        main()
