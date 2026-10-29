# -*- coding: utf-8 -*-
r"""
SAM3.1 Stage 5.1.1 - Demo Latency Finalization
=============================================

解决当前招商 Demo 里真正需要处理的 3 个问题：

1) 实时延迟：
   - RTSP/AI 采样独立线程持续运行
   - 有界 Ring Buffer
   - SAM 不再“推理完以后再等下一批帧”
   - Window 从 32 缩到 12，避免 5 FPS 下天然 6.2 秒的窗口跨度
   - 每次优先消费最新采样帧，允许丢弃已经过时的 AI 样本
   - propagation 输出按帧即时落 TrackObservation

2) Global Track 连续率：
   - 保留 Text Re-Grounding + overlap geometry association
   - 如果上一 Window overlap 有 N 个旧 Global Track，
     新 Session 第一次 Grounding 只找到 < N 个，
     自动在 overlap 的其它帧补 Grounding
   - 不引入 ReID / DeepSORT / Point Handoff

3) 空场景 Grounding 浪费：
   - 连续 2 个“确认没有旧 Track 且 Grounding 无人”的 Window 后进入 empty_scene
   - empty_scene 每个 Window 只做 1 次 person Grounding
   - 一旦重新发现 person，立即恢复正常

默认只跑 5 分钟，适合招商 Demo 验证。

当前依赖你已经验证过的本地 SAM3.1 Windows 环境：
- RTX 5070 Ti 16GB
- BF16
- cuDNN SDPA（decoder.py 已按你当前环境修复）
- batch=2
- CPU output offload=True
- trim=False
- compile=False

运行：
    conda activate sam3
    cd D:\GJJ\work\sam3
    python sam31_stage5_1_demo_runtime.py

输出目录：
    D:\GJJ\work\sam3\sam31_stage5_1_demo_report

核心输出：
    stage5_1_report.json
    window_metrics.csv
    association_metrics.csv
    track_observations.csv
"""

import os
import sys

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

# The launcher lives beside the checked-out SAM repository, so direct script
# execution would otherwise resolve that repository directory as a namespace
# package instead of loading its inner sam3 Python package.
SAM3_SOURCE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sam3")
if SAM3_SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SAM3_SOURCE_ROOT)

import csv
import gc
import json
import math
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from rv_handoff_paths import CODE_ROOT, AGENTS_PYTHON, RUNTIME_OUTPUT, project_config
from typing import Dict, List, Optional

import cv2
import numpy as np
import torch

from sam3.model_builder import build_sam3_predictor


# =============================================================================
# 1. 用户配置
# =============================================================================

RTSP_URL = os.environ.get("VIDEO_SOURCE", "")

ROOT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = RUNTIME_OUTPUT / "sam31_stage5_1_1_demo_report"
WINDOW_DIR = OUTPUT_DIR / "_active_window"

# Stage 6B live Behavior seam. The SAM environment stays unchanged; a small
# RetailVision worker runs in its existing Python environment and consumes one
# complete Global Track batch per fresh AI frame.
RETAILVISION_ROOT = CODE_ROOT
BEHAVIOR_WORKER_PYTHON = AGENTS_PYTHON
BEHAVIOR_CONFIG = project_config("behavior_cameras.cam02.stage6a.json")
BEHAVIOR_LOG_OUTPUT = (
    RETAILVISION_ROOT / "outputs" / "stage6b1_cam02_track_lost_live_test.jsonl"
)
BEHAVIOR_CAMERA_ID = "cam02"
BEHAVIOR_ENABLED = True

RUN_MINUTES = 5.0

# -----------------------------------------------------------------------------
# Demo 实时参数
# -----------------------------------------------------------------------------
#
# 之前 Window=32 @ 5FPS：
#   一个 Window 本身覆盖 31/5 = 6.2 秒。
# 即使采集完全异步，只要要等完整 32 帧后再开始 Session，
# 结果天然就是“几秒前的画面”。
#
# Stage 5.1 为 Demo 缩到 12 帧：
#   11/5 = 2.2 秒 source span
# 再通过 latest-buffer + 按帧输出，把 fresh observation 延迟压低。
#
TARGET_AI_FPS = 5.0
AI_WIDTH = 1280
AI_HEIGHT = 720

WINDOW_FRAMES = 12
OVERLAP_FRAMES = 4
FIXED_BATCH = 2

# Ring Buffer 必须有界，防止 SAM 忙时内存无限增长。
RING_BUFFER_MAX_FRAMES = 64

# 第一次 Window 需要完整 WINDOW_FRAMES。
FIRST_WINDOW_TIMEOUT_SEC = 20.0
NEXT_WINDOW_TIMEOUT_SEC = 8.0

# H.265 接入时先丢一点 GOP。
RTSP_WARMUP_SECONDS = 1.0

# -----------------------------------------------------------------------------
# SAM 已验证配置
# -----------------------------------------------------------------------------

ENABLE_BF16 = True
ENABLE_COMPILE = False
ENABLE_CPU_OUTPUT_OFFLOAD = True
ENABLE_TRIM_OLD_OUTPUTS = False

MAX_NUM_OBJECTS = 16
MULTIPLEX_COUNT = 16

USE_FA3 = False
USE_ROPE_REAL = False

TEXT_PROMPT = "person"
OUTPUT_PROB_THRESH = 0.35

# -----------------------------------------------------------------------------
# Association-aware Re-Grounding
# -----------------------------------------------------------------------------

# 有旧 Track 时，只在 overlap 内补 Grounding。
# 这样新旧 Session 使用的是完全相同的图片，可继续做高可信几何 Association。
MAX_ASSOC_REGROUND_FRAMES = OVERLAP_FRAMES

# 没有旧 Track、非 empty_scene 时，正常 discovery 最多尝试这些 frame。
NORMAL_DISCOVERY_FRAMES = [0, 2, 4, 6]

# 连续多少个“真正空场景 Window”后开始降频。
EMPTY_SCENE_ENTER_AFTER_WINDOWS = 2

# empty_scene 下每个 Window 只尝试一次。
EMPTY_SCENE_DISCOVERY_FRAMES = [0]

# empty_scene 下最多每 2 秒才真正做一次 Text Grounding。
# Window 可以继续快速滚动，但如果距离上次 empty poll 不足 2 秒，
# 这一 Window 直接跳过 Grounding，避免空场景高频消耗 GPU。
EMPTY_SCENE_POLL_INTERVAL_SEC = 2.0

# -----------------------------------------------------------------------------
# Global Association - 沿用上一轮已经表现很好的几何匹配
# -----------------------------------------------------------------------------

ASSOC_SCORE_THRESHOLD = 0.35
ASSOC_MIN_MASK_IOU = 0.15
ASSOC_MIN_BBOX_IOU = 0.20
ASSOC_MAX_CENTROID_DISTANCE_NORM = 0.12
ASSOC_AMBIGUITY_MARGIN = 0.05

ASSOC_W_MASK_IOU = 0.65
ASSOC_W_BBOX_IOU = 0.25
ASSOC_W_CENTROID = 0.10

# 招商 Demo 目标，不追生产级 95%/99%。
TARGET_ASSOC_CONTINUITY_RATE = 0.85
MIN_ASSOC_ELIGIBLE_TRACKS = 10

# -----------------------------------------------------------------------------
# Demo 验收阈值
# -----------------------------------------------------------------------------

SAFE_MAX_WINDOW_PEAK_GB = 13.5
SAFE_MAX_MEMORY_SLOPE_GB_PER_WINDOW = 0.02

# 这里测的是“新鲜帧 observation 真正 emit 时”的延迟，
# 不把为了跨 Session 匹配而重复处理的 overlap 帧算进去。
TARGET_FRESH_OBSERVATION_LAG_AVG_SEC = 3.0
TARGET_FRESH_OBSERVATION_LAG_P95_SEC = 4.0

VRAM_SAFETY_ALLOCATED_GB = 14.5
VRAM_SAFETY_FREE_GB = 0.50

KEEP_WINDOW_JPEGS = False


# =============================================================================
# 2. 数据结构
# =============================================================================

@dataclass
class SampledFrame:
    sample_id: int
    source_seq: int
    wall_time: float
    frame: np.ndarray


@dataclass
class GlobalTrack:
    global_id: int
    created_window: int
    last_window: int
    last_source_seq: int
    last_wall_time: float


# =============================================================================
# 3. 通用工具
# =============================================================================

def validate_config():
    if (
        not RTSP_URL.startswith("rtsp://")
        or "用户名" in RTSP_URL
        or "路径" in RTSP_URL
    ):
        raise RuntimeError("请先修改脚本顶部 RTSP_URL。")

    if OVERLAP_FRAMES <= 0 or OVERLAP_FRAMES >= WINDOW_FRAMES:
        raise RuntimeError("要求 0 < OVERLAP_FRAMES < WINDOW_FRAMES。")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def configure_torch():
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用。")

    torch.set_float32_matmul_precision("high")
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    torch.backends.cudnn.benchmark = True

    print("=" * 108)
    print("SAM3.1 Stage 5.1.1 - Demo Latency Finalization")
    print("=" * 108)
    print("PyTorch              :", torch.__version__)
    print("CUDA                 :", torch.version.cuda)
    print("GPU                  :", torch.cuda.get_device_name(0))
    print("Compute capability   :", torch.cuda.get_device_capability(0))
    print("AI FPS               :", TARGET_AI_FPS)
    print("AI resolution        :", f"{AI_WIDTH}x{AI_HEIGHT}")
    print("Window / overlap     :", WINDOW_FRAMES, "/", OVERLAP_FRAMES)
    print("Batch                :", FIXED_BATCH)
    print("BF16                 :", ENABLE_BF16)
    print("Compile              :", ENABLE_COMPILE)
    print("CPU output offload   :", ENABLE_CPU_OUTPUT_OFFLOAD)
    print("Trim old outputs     :", ENABLE_TRIM_OLD_OUTPUTS)
    print("Demo duration        :", RUN_MINUTES, "minutes")
    print("Association target   :", f"{TARGET_ASSOC_CONTINUITY_RATE:.0%}")
    print("=" * 108)


def autocast_context():
    return torch.autocast(
        device_type="cuda",
        dtype=torch.bfloat16,
        enabled=ENABLE_BF16,
    )


def gb(value):
    return float(value) / (1024.0 ** 3)


def gpu_mem():
    free_b, total_b = torch.cuda.mem_get_info()
    return {
        "allocated_gb": gb(torch.cuda.memory_allocated()),
        "reserved_gb": gb(torch.cuda.memory_reserved()),
        "peak_allocated_gb": gb(torch.cuda.max_memory_allocated()),
        "peak_reserved_gb": gb(torch.cuda.max_memory_reserved()),
        "driver_free_gb": gb(free_b),
        "driver_total_gb": gb(total_b),
    }


def reset_peak_only():
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()


def linear_slope(values):
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=np.float64)
    y = np.asarray(values, dtype=np.float64)
    return float(np.polyfit(x, y, 1)[0])


def percentile(values, p):
    if not values:
        return None
    return float(np.percentile(np.asarray(values, dtype=np.float64), p))


def prepare_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def rewrite_csv(path: Path, rows: List[dict]):
    if not rows:
        path.write_text("", encoding="utf-8")
        return

    fields = []
    seen = set()

    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fields.append(key)

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def resize_ai(frame):
    if frame.shape[1] == AI_WIDTH and frame.shape[0] == AI_HEIGHT:
        return frame
    return cv2.resize(
        frame,
        (AI_WIDTH, AI_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


class BehaviorLivePublisher:
    """Publish one complete fresh AI-frame batch to RetailVision."""

    def __init__(self):
        runtime_instance_id = time.strftime(
            "sam31-stage6b-%Y%m%d-%H%M%S",
            time.localtime(),
        )
        command = [
            str(BEHAVIOR_WORKER_PYTHON),
            "-m",
            "scripts.run_sam31_behavior_live_worker",
            "--behavior-config",
            str(BEHAVIOR_CONFIG),
            "--camera-id",
            BEHAVIOR_CAMERA_ID,
            "--runtime-instance-id",
            runtime_instance_id,
            "--frame-width",
            str(AI_WIDTH),
            "--frame-height",
            str(AI_HEIGHT),
            "--log-output",
            str(BEHAVIOR_LOG_OUTPUT),
        ]
        self.runtime_instance_id = runtime_instance_id
        self.process = subprocess.Popen(
            command,
            cwd=str(RETAILVISION_ROOT),
            stdin=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        if self.process.stdin is None:
            raise RuntimeError("Behavior worker stdin is unavailable")
        print()
        print("Stage 6B Behavior worker started")
        print("Runtime instance ID  :", runtime_instance_id)
        print("Behavior config      :", BEHAVIOR_CONFIG)
        print("Behavior JSONL       :", BEHAVIOR_LOG_OUTPUT)

    def publish(self, item: SampledFrame, tracks: List[dict]):
        if self.process.poll() is not None:
            raise RuntimeError(
                f"Behavior worker exited with code {self.process.returncode}"
            )
        payload = {
            "observed_at": item.wall_time,
            "source_seq": item.source_seq,
            "frame_width": AI_WIDTH,
            "frame_height": AI_HEIGHT,
            "tracks": tracks,
        }
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        )
        self.process.stdin.flush()

    def close(self):
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            return_code = self.process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            return_code = self.process.wait(timeout=5.0)
        if return_code != 0:
            raise RuntimeError(
                f"Behavior worker exited with code {return_code}"
            )




def save_jpeg(path: Path, frame):
    cv2.imwrite(
        str(path),
        frame,
        [int(cv2.IMWRITE_JPEG_QUALITY), 90],
    )


# =============================================================================
# 4. Async RTSP + AI Sampler + bounded Ring Buffer
# =============================================================================

class AsyncAISampler:
    """
    一个线程持续 decode RTSP，并独立按 TARGET_AI_FPS 向有界 deque 写 AI frame。

    关键点：
    - SAM Worker 忙的时候，采样仍然继续。
    - Ring Buffer 有 maxlen，不会形成无限 backlog。
    - Worker 下一轮取“最新可用样本”，过时样本允许丢弃。
    """

    def __init__(self):
        self._cond = threading.Condition()
        self._frames = deque(maxlen=RING_BUFFER_MAX_FRAMES)
        self._stop = threading.Event()
        self._thread = None
        self._error = None

        self.source_fps = 0.0
        self.source_width = 0
        self.source_height = 0

        self.total_decoded = 0
        self.total_sampled = 0
        self.total_ring_dropped = 0

    def start(self):
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="sam31-stage5.1-ai-sampler",
        )
        self._thread.start()

        deadline = time.time() + 15.0
        while time.time() < deadline:
            with self._cond:
                if self._error is not None:
                    raise RuntimeError(f"RTSP sampler failed: {self._error!r}")
                if len(self._frames) > 0:
                    return self
            time.sleep(0.02)

        raise RuntimeError("15 秒内未获得 RTSP AI sample。")

    def _open_capture(self):
        cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError("RTSP 打开失败。")

        fps = float(cap.get(cv2.CAP_PROP_FPS))
        if fps <= 1 or fps > 120:
            fps = 25.0

        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        return cap, fps, width, height

    def _run(self):
        cap = None
        try:
            cap, fps, width, height = self._open_capture()

            self.source_fps = fps
            self.source_width = width
            self.source_height = height

            warm_start = time.perf_counter()
            while time.perf_counter() - warm_start < RTSP_WARMUP_SECONDS:
                cap.read()

            source_seq = 0
            sample_id = 0

            interval = 1.0 / TARGET_AI_FPS
            next_sample_time = time.perf_counter()

            while not self._stop.is_set():
                ok, frame = cap.read()

                if not ok or frame is None:
                    continue

                wall_time = time.time()
                now_perf = time.perf_counter()

                self.total_decoded += 1
                source_seq += 1

                if now_perf < next_sample_time:
                    continue

                ai_frame = resize_ai(frame)

                item = SampledFrame(
                    sample_id=sample_id,
                    source_seq=source_seq,
                    wall_time=wall_time,
                    frame=ai_frame,
                )

                with self._cond:
                    before = len(self._frames)
                    maxlen = self._frames.maxlen
                    self._frames.append(item)

                    if maxlen is not None and before == maxlen:
                        self.total_ring_dropped += 1

                    self.total_sampled += 1
                    self._cond.notify_all()

                sample_id += 1
                next_sample_time += interval

                # 如果 decode/系统调度曾经落后，不“补采样”，直接追到当前时间。
                while next_sample_time < now_perf - interval:
                    next_sample_time += interval

        except Exception as exc:
            with self._cond:
                self._error = exc
                self._cond.notify_all()

        finally:
            if cap is not None:
                cap.release()

    def stop(self):
        self._stop.set()
        with self._cond:
            self._cond.notify_all()

        if self._thread is not None:
            self._thread.join(timeout=3.0)

    def _copy_items(self, items):
        return [
            SampledFrame(
                sample_id=x.sample_id,
                source_seq=x.source_seq,
                wall_time=x.wall_time,
                frame=x.frame.copy(),
            )
            for x in items
        ]

    def wait_first_window(self):
        deadline = time.monotonic() + FIRST_WINDOW_TIMEOUT_SEC

        with self._cond:
            while True:
                if self._error is not None:
                    raise RuntimeError(f"Sampler error: {self._error!r}")

                if len(self._frames) >= WINDOW_FRAMES:
                    items = list(self._frames)[-WINDOW_FRAMES:]
                    return self._copy_items(items)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待第一组 AI Window 超时。")

                self._cond.wait(timeout=min(0.2, remaining))

    def wait_latest_new_frames(self, after_sample_id: int, count: int):
        """
        等到至少有 count 张 sample_id > after_sample_id 的新帧。

        如果 SAM 忙时已经积累超过 count 张：
            只取最新 count 张；
            中间过时样本丢弃。

        这正是 Latest-Frame / bounded-lag 策略。
        """
        deadline = time.monotonic() + NEXT_WINDOW_TIMEOUT_SEC

        with self._cond:
            while True:
                if self._error is not None:
                    raise RuntimeError(f"Sampler error: {self._error!r}")

                available = [
                    x for x in self._frames
                    if x.sample_id > after_sample_id
                ]

                if len(available) >= count:
                    selected = available[-count:]
                    skipped = max(0, len(available) - count)
                    return self._copy_items(selected), skipped, len(available)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"等待新 AI frame 超时：需要 {count}，当前 {len(available)}。"
                    )

                self._cond.wait(timeout=min(0.2, remaining))


# =============================================================================
# 5. Window 构建
# =============================================================================

def materialize_window(window_items: List[SampledFrame]):
    prepare_dir(WINDOW_DIR)

    for idx, item in enumerate(window_items):
        save_jpeg(
            WINDOW_DIR / f"{idx:05d}.jpg",
            item.frame,
        )


def build_next_window(
    sampler: AsyncAISampler,
    previous_overlap_items: List[SampledFrame],
):
    """
    后续 Window：
        前 OVERLAP_FRAMES = 上一 Session 最后几张完全相同的图片
        后 new_needed      = Ring Buffer 当前最新的新图片

    注意：
    如果 SAM 比 AI sample 慢，中间旧 sample 会被丢弃。
    这会牺牲“逐帧不漏”，换取招商 Demo 更低实时延迟。
    """
    if not previous_overlap_items:
        items = sampler.wait_first_window()
        return {
            "window_items": items,
            "new_items": items,
            "skipped_samples": 0,
            "available_new_samples": len(items),
            "bridge_gap_samples": 0,
            "is_first_window": True,
        }

    new_needed = WINDOW_FRAMES - OVERLAP_FRAMES
    prev_last_id = previous_overlap_items[-1].sample_id

    new_items, skipped, available_count = sampler.wait_latest_new_frames(
        after_sample_id=prev_last_id,
        count=new_needed,
    )

    first_new_id = new_items[0].sample_id
    bridge_gap_samples = max(
        0,
        first_new_id - prev_last_id - 1,
    )

    items = [
        SampledFrame(
            sample_id=x.sample_id,
            source_seq=x.source_seq,
            wall_time=x.wall_time,
            frame=x.frame.copy(),
        )
        for x in previous_overlap_items
    ] + new_items

    return {
        "window_items": items,
        "new_items": new_items,
        "skipped_samples": skipped,
        "available_new_samples": available_count,
        "bridge_gap_samples": bridge_gap_samples,
        "is_first_window": False,
    }


# =============================================================================
# 6. SAM3.1
# =============================================================================

def build_predictor():
    return build_sam3_predictor(
        version="sam3.1",
        max_num_objects=MAX_NUM_OBJECTS,
        multiplex_count=MULTIPLEX_COUNT,
        use_fa3=USE_FA3,
        use_rope_real=USE_ROPE_REAL,
        compile=ENABLE_COMPILE,
        warm_up=False,
        async_loading_frames=False,
    )


def tune_runtime(predictor):
    model = predictor.model

    if hasattr(model, "batched_grounding_batch_size"):
        old = model.batched_grounding_batch_size
        model.batched_grounding_batch_size = FIXED_BATCH
        print("batched_grounding_batch_size:", old, "->", FIXED_BATCH)

    if hasattr(model, "postprocess_batch_size"):
        old = model.postprocess_batch_size
        model.postprocess_batch_size = FIXED_BATCH
        print("postprocess_batch_size       :", old, "->", FIXED_BATCH)

    tracker_core = None
    if hasattr(model, "tracker") and hasattr(model.tracker, "model"):
        tracker_core = model.tracker.model

    if tracker_core is not None:
        if hasattr(tracker_core, "forward_backbone_per_frame_for_eval"):
            tracker_core.forward_backbone_per_frame_for_eval = True

        if hasattr(tracker_core, "offload_output_to_cpu_for_eval"):
            tracker_core.offload_output_to_cpu_for_eval = (
                ENABLE_CPU_OUTPUT_OFFLOAD
            )

        if hasattr(tracker_core, "trim_past_non_cond_mem_for_eval"):
            tracker_core.trim_past_non_cond_mem_for_eval = (
                ENABLE_TRIM_OLD_OUTPUTS
            )


def start_session(predictor):
    response = predictor.handle_request(
        {
            "type": "start_session",
            "resource_path": str(WINDOW_DIR),
            "offload_video_to_cpu": True,
        }
    )

    if not response or "session_id" not in response:
        raise RuntimeError(f"start_session 返回异常：{response!r}")

    return response["session_id"]


def close_session(predictor, session_id):
    if not session_id:
        return

    try:
        predictor.handle_request(
            {
                "type": "close_session",
                "session_id": session_id,
            }
        )
    except Exception as exc:
        print("close_session warning:", repr(exc))


def to_numpy(value):
    if value is None:
        return None

    if isinstance(value, torch.Tensor):
        return value.detach().cpu().numpy()

    return np.asarray(value)


def masks_from_outputs(outputs):
    if not outputs:
        return {}

    obj_ids = to_numpy(outputs.get("out_obj_ids", []))
    masks = to_numpy(outputs.get("out_binary_masks"))

    if obj_ids is None:
        obj_ids = np.array([], dtype=np.int64)

    obj_ids = np.asarray(obj_ids).reshape(-1)

    if masks is None:
        return {}

    result = {}

    for idx, oid in enumerate(obj_ids):
        if idx >= len(masks):
            break

        mask = masks[idx]

        while mask.ndim > 2:
            mask = mask[0]

        result[int(oid)] = mask.astype(bool)

    return result


def add_text_prompt(predictor, session_id, frame_index):
    return predictor.handle_request(
        {
            "type": "add_prompt",
            "session_id": session_id,
            "frame_index": int(frame_index),
            "text": TEXT_PROMPT,
            "output_prob_thresh": OUTPUT_PROB_THRESH,
        }
    )


def propagate_forward(
    predictor,
    session_id,
    start_frame_index=0,
):
    yield from predictor.handle_stream_request(
        {
            "type": "propagate_in_video",
            "session_id": session_id,
            "propagation_direction": "forward",
            "start_frame_index": int(start_frame_index),
            "output_prob_thresh": OUTPUT_PROB_THRESH,
        }
    )


# =============================================================================
# 7. Mask geometry
# =============================================================================

def mask_bbox(mask: np.ndarray):
    ys, xs = np.nonzero(mask)

    if len(xs) == 0:
        return None

    return (
        int(xs.min()),
        int(ys.min()),
        int(xs.max()),
        int(ys.max()),
    )


def bbox_iou(a, b):
    if a is None or b is None:
        return 0.0

    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    iw = max(0, ix2 - ix1 + 1)
    ih = max(0, iy2 - iy1 + 1)

    inter = iw * ih

    area_a = max(0, ax2 - ax1 + 1) * max(0, ay2 - ay1 + 1)
    area_b = max(0, bx2 - bx1 + 1) * max(0, by2 - by1 + 1)

    union = area_a + area_b - inter

    if union <= 0:
        return 0.0

    return float(inter / union)


def mask_iou(a, b):
    if a.shape != b.shape:
        b = cv2.resize(
            b.astype(np.uint8),
            (a.shape[1], a.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)

    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()

    if union <= 0:
        return 0.0

    return float(inter / union)


def mask_centroid(mask):
    ys, xs = np.nonzero(mask)

    if len(xs) == 0:
        return None

    return float(xs.mean()), float(ys.mean())


def centroid_distance_norm(a, b, width, height):
    if a is None or b is None:
        return 1.0

    dx = a[0] - b[0]
    dy = a[1] - b[1]

    diag = math.sqrt(width * width + height * height)

    if diag <= 0:
        return 1.0

    return float(math.sqrt(dx * dx + dy * dy) / diag)


def geometry_from_mask(mask):
    return {
        "bbox": mask_bbox(mask),
        "centroid": mask_centroid(mask),
        "area": int(np.count_nonzero(mask)),
    }


# =============================================================================
# 8. Global Association
# =============================================================================

def old_global_ids_from_bank(old_overlap_bank):
    return sorted(
        {
            int(global_id)
            for frame_tracks in old_overlap_bank.values()
            for global_id in frame_tracks.keys()
        }
    )


def pair_metrics_across_overlap(
    old_overlap_bank,
    new_overlap_bank,
    global_id,
    local_obj_id,
):
    per_frame = []

    for offset in range(OVERLAP_FRAMES):
        old_mask = old_overlap_bank.get(offset, {}).get(global_id)
        new_mask = new_overlap_bank.get(offset, {}).get(local_obj_id)

        if old_mask is None or new_mask is None:
            continue

        old_geo = geometry_from_mask(old_mask)
        new_geo = geometry_from_mask(new_mask)

        miou = mask_iou(old_mask, new_mask)
        biou = bbox_iou(old_geo["bbox"], new_geo["bbox"])

        dist = centroid_distance_norm(
            old_geo["centroid"],
            new_geo["centroid"],
            AI_WIDTH,
            AI_HEIGHT,
        )

        centroid_sim = max(
            0.0,
            1.0 - dist / max(ASSOC_MAX_CENTROID_DISTANCE_NORM, 1e-6),
        )

        score = (
            ASSOC_W_MASK_IOU * miou
            + ASSOC_W_BBOX_IOU * biou
            + ASSOC_W_CENTROID * centroid_sim
        )

        per_frame.append(
            {
                "offset": offset,
                "mask_iou": miou,
                "bbox_iou": biou,
                "centroid_distance_norm": dist,
                "score": score,
            }
        )

    if not per_frame:
        return None

    mean_mask_iou = float(np.mean([x["mask_iou"] for x in per_frame]))
    mean_bbox_iou = float(np.mean([x["bbox_iou"] for x in per_frame]))
    mean_dist = float(
        np.mean([x["centroid_distance_norm"] for x in per_frame])
    )
    mean_score = float(np.mean([x["score"] for x in per_frame]))

    valid_geometry = bool(
        mean_mask_iou >= ASSOC_MIN_MASK_IOU
        or mean_bbox_iou >= ASSOC_MIN_BBOX_IOU
        or mean_dist <= ASSOC_MAX_CENTROID_DISTANCE_NORM
    )

    return {
        "global_id": int(global_id),
        "local_obj_id": int(local_obj_id),
        "shared_frames": len(per_frame),
        "mean_mask_iou": mean_mask_iou,
        "mean_bbox_iou": mean_bbox_iou,
        "mean_centroid_distance_norm": mean_dist,
        "mean_score": mean_score,
        "valid_geometry": valid_geometry,
    }


def associate_global_tracks(old_overlap_bank, new_overlap_bank):
    old_global_ids = old_global_ids_from_bank(old_overlap_bank)

    new_local_ids = sorted(
        {
            int(local_obj_id)
            for frame_tracks in new_overlap_bank.values()
            for local_obj_id in frame_tracks.keys()
        }
    )

    candidates = []
    per_new_candidates = {x: [] for x in new_local_ids}

    for global_id in old_global_ids:
        for local_obj_id in new_local_ids:
            metrics = pair_metrics_across_overlap(
                old_overlap_bank,
                new_overlap_bank,
                global_id,
                local_obj_id,
            )

            if metrics is None:
                continue

            if (
                not metrics["valid_geometry"]
                or metrics["mean_score"] < ASSOC_SCORE_THRESHOLD
            ):
                continue

            candidates.append(metrics)
            per_new_candidates[local_obj_id].append(metrics)

    ambiguous_new_ids = set()

    for local_id, items in per_new_candidates.items():
        ranked = sorted(
            items,
            key=lambda x: x["mean_score"],
            reverse=True,
        )

        if len(ranked) >= 2:
            margin = ranked[0]["mean_score"] - ranked[1]["mean_score"]
            if margin < ASSOC_AMBIGUITY_MARGIN:
                ambiguous_new_ids.add(local_id)

    candidates.sort(
        key=lambda x: x["mean_score"],
        reverse=True,
    )

    matched_old = set()
    matched_new = set()
    matches = []

    for item in candidates:
        old_id = item["global_id"]
        new_id = item["local_obj_id"]

        if (
            old_id in matched_old
            or new_id in matched_new
            or new_id in ambiguous_new_ids
        ):
            continue

        matched_old.add(old_id)
        matched_new.add(new_id)
        matches.append(item)

    return {
        "old_global_ids": old_global_ids,
        "new_local_ids": new_local_ids,
        "matches": matches,
        "ambiguous_new_ids": sorted(ambiguous_new_ids),
        "unmatched_old_ids": [
            x for x in old_global_ids if x not in matched_old
        ],
        "unmatched_new_ids": [
            x for x in new_local_ids if x not in matched_new
        ],
    }


# =============================================================================
# 9. Association-aware Text Grounding
# =============================================================================

def grounding_plan(expected_old_count: int, empty_scene_streak: int):
    """
    三种情况：

    A. expected_old_count > 0
       说明上一 Window overlap 明确有人。
       优先 0；如果人数不足，继续 1/2/3。
       目的：把上一轮已知 Track 尽量找齐。

    B. expected_old_count == 0 且尚未确认长期 empty
       正常 discovery：0/2/4/6，找到人就停。

    C. empty_scene_streak >= 2
       每 Window 只试 frame0 一次，避免空场景 8 连 Grounding。
    """
    if expected_old_count > 0:
        return list(range(min(MAX_ASSOC_REGROUND_FRAMES, OVERLAP_FRAMES)))

    if empty_scene_streak >= EMPTY_SCENE_ENTER_AFTER_WINDOWS:
        return [
            x for x in EMPTY_SCENE_DISCOVERY_FRAMES
            if x < WINDOW_FRAMES
        ]

    return [
        x for x in NORMAL_DISCOVERY_FRAMES
        if x < WINDOW_FRAMES
    ]


def run_grounding(
    predictor,
    session_id,
    expected_old_count,
    empty_scene_streak,
    empty_scene_last_poll_wall_time,
):
    # 已经确认 empty_scene 时，使用真实时间做 throttle。
    # 注意：有旧 Track 时绝不 throttle，避免把一次 detection miss 放大成长期丢 Track。
    now_wall = time.time()
    empty_scene_mode = (
        expected_old_count == 0
        and empty_scene_streak >= EMPTY_SCENE_ENTER_AFTER_WINDOWS
    )

    if (
        empty_scene_mode
        and empty_scene_last_poll_wall_time is not None
        and now_wall - empty_scene_last_poll_wall_time
        < EMPTY_SCENE_POLL_INTERVAL_SEC
    ):
        return {
            "attempts": [],
            "text_calls": 0,
            "text_sec": 0.0,
            "supplemental_calls": 0,
            "overlap_local_masks": {},
            "local_ids": [],
            "found_any": False,
            "throttled": True,
            "performed_poll_wall_time": None,
        }

    frames = grounding_plan(
        expected_old_count=expected_old_count,
        empty_scene_streak=empty_scene_streak,
    )

    attempts = []
    overlap_local_masks = {}
    all_local_ids = set()

    found_any = False
    supplemental_calls = 0

    for attempt_idx, frame_idx in enumerate(frames):
        t0 = time.perf_counter()

        response = add_text_prompt(
            predictor,
            session_id,
            frame_idx,
        )

        torch.cuda.synchronize()
        sec = time.perf_counter() - t0

        masks = masks_from_outputs(
            (response or {}).get("outputs", {})
        )

        attempts.append(
            {
                "frame_idx": int(frame_idx),
                "sec": float(sec),
                "objects": len(masks),
            }
        )

        if attempt_idx > 0:
            supplemental_calls += 1

        if masks:
            found_any = True
            all_local_ids.update(int(x) for x in masks.keys())

            # Association 只需要 overlap frame。
            if 0 <= frame_idx < OVERLAP_FRAMES:
                overlap_local_masks[int(frame_idx)] = {
                    int(k): v.copy()
                    for k, v in masks.items()
                }

        # 有旧 Track：
        #   不以“找到任意 person”为结束，而是尽量达到旧 Track 数。
        if expected_old_count > 0:
            if len(all_local_ids) >= expected_old_count:
                break
        else:
            # 没旧 Track，只要 discovery 找到人就够了。
            if found_any:
                break

    return {
        "attempts": attempts,
        "text_calls": len(attempts),
        "text_sec": sum(x["sec"] for x in attempts),
        "supplemental_calls": supplemental_calls,
        "overlap_local_masks": overlap_local_masks,
        "local_ids": sorted(all_local_ids),
        "found_any": found_any,
        "throttled": False,
        "performed_poll_wall_time": time.time() if frames else None,
    }


# =============================================================================
# 10. 单 Window 推理
# =============================================================================

def run_window(
    predictor,
    window_items,
    old_overlap_bank,
    global_tracks,
    next_global_id,
    window_no,
    empty_scene_streak,
    empty_scene_last_poll_wall_time,
    observation_rows,
    association_rows,
    behavior_publisher,
):
    session_id = None

    expected_old_ids = old_global_ids_from_bank(old_overlap_bank)
    expected_old_count = len(expected_old_ids)

    session_init_sec = 0.0
    propagation_sec = 0.0
    processed_frames = 0

    first_overlap_local_masks = {}
    last_overlap_local_masks = {}

    local_to_global = {}
    fresh_emit_lags = []

    try:
        # ---------------------------------------------------------------------
        # Session
        # ---------------------------------------------------------------------
        t0 = time.perf_counter()
        session_id = start_session(predictor)
        torch.cuda.synchronize()
        session_init_sec = time.perf_counter() - t0

        # ---------------------------------------------------------------------
        # Grounding
        # ---------------------------------------------------------------------
        grounding = run_grounding(
            predictor=predictor,
            session_id=session_id,
            expected_old_count=expected_old_count,
            empty_scene_streak=empty_scene_streak,
            empty_scene_last_poll_wall_time=empty_scene_last_poll_wall_time,
        )

        first_overlap_local_masks.update(
            grounding["overlap_local_masks"]
        )

        # ---------------------------------------------------------------------
        # 在 propagation 前就完成 Global Association。
        # 后面每一帧一出来，就已经知道 global_track_id，可以立即 emit。
        # ---------------------------------------------------------------------
        association = associate_global_tracks(
            old_overlap_bank,
            first_overlap_local_masks,
        )

        for match in association["matches"]:
            local_to_global[int(match["local_obj_id"])] = int(
                match["global_id"]
            )

            association_rows.append(
                {
                    "window_no": window_no,
                    "old_global_id": int(match["global_id"]),
                    "new_local_obj_id": int(match["local_obj_id"]),
                    "matched": True,
                    "shared_frames": match["shared_frames"],
                    "mean_mask_iou": match["mean_mask_iou"],
                    "mean_bbox_iou": match["mean_bbox_iou"],
                    "mean_centroid_distance_norm": (
                        match["mean_centroid_distance_norm"]
                    ),
                    "mean_score": match["mean_score"],
                }
            )

        for old_global_id in association["unmatched_old_ids"]:
            association_rows.append(
                {
                    "window_no": window_no,
                    "old_global_id": int(old_global_id),
                    "new_local_obj_id": None,
                    "matched": False,
                    "shared_frames": 0,
                    "mean_mask_iou": None,
                    "mean_bbox_iou": None,
                    "mean_centroid_distance_norm": None,
                    "mean_score": None,
                }
            )

        # Grounding 阶段已出现的 local IDs，如果没匹配旧 Track，就是新 Track。
        for local_obj_id in grounding["local_ids"]:
            if local_obj_id in local_to_global:
                continue

            global_id = next_global_id
            next_global_id += 1

            local_to_global[local_obj_id] = global_id

            global_tracks[global_id] = GlobalTrack(
                global_id=global_id,
                created_window=window_no,
                last_window=window_no,
                last_source_seq=-1,
                last_wall_time=0.0,
            )

        # ---------------------------------------------------------------------
        # 没找到 person：
        # - 不做无意义 propagation
        # - 空场景 streak 只在“上一轮也没有旧 Track”时增加
        # ---------------------------------------------------------------------
        if not grounding["found_any"]:
            if expected_old_count == 0:
                next_empty_streak = empty_scene_streak + 1
            else:
                # 旧 overlap 明明有人但 Grounding 没找到，视为 detection miss，
                # 不要因此进入 empty throttle。
                next_empty_streak = 0

            if behavior_publisher is not None:
                empty_start = 0 if window_no == 0 else OVERLAP_FRAMES
                for item in window_items[empty_start:]:
                    behavior_publisher.publish(item, [])

            return {
                "session_id": session_id,
                "session_init_sec": session_init_sec,
                "grounding": grounding,
                "association": association,
                "local_to_global": local_to_global,
                "new_overlap_bank": {},
                "next_global_id": next_global_id,
                "next_empty_scene_streak": next_empty_streak,
                "next_empty_scene_last_poll_wall_time": (
                    grounding["performed_poll_wall_time"]
                    if grounding["performed_poll_wall_time"] is not None
                    else empty_scene_last_poll_wall_time
                ),
                "propagation_start_frame_index": None,
                "propagation_sec": 0.0,
                "processed_frames": 0,
                "fresh_emit_lags": [],
            }

        next_empty_streak = 0

        # ---------------------------------------------------------------------
        # Propagation + immediate TrackObservation
        # ---------------------------------------------------------------------
        prop_t0 = time.perf_counter()

        # Stage 5.1.1:
        # Window 0 没有重复 overlap，因此从 0 开始。
        # 后续 Window 的 0..OVERLAP_FRAMES-1 仅用于 Grounding + Association，
        # propagation 从 fresh 区域开始，避免再次跑重复的 overlap 帧。
        propagation_start_frame_index = (
            0 if window_no == 0 else OVERLAP_FRAMES
        )

        for response in propagate_forward(
            predictor,
            session_id,
            start_frame_index=propagation_start_frame_index,
        ):
            local_idx = response.get("frame_index")

            if local_idx is None:
                continue

            local_idx = int(local_idx)

            if not (0 <= local_idx < len(window_items)):
                continue

            masks = masks_from_outputs(
                response.get("outputs", {})
            )

            processed_frames += 1

            # 保存 first overlap masks（如果 propagation 给了更完整结果）
            if 0 <= local_idx < OVERLAP_FRAMES and masks:
                first_overlap_local_masks[local_idx] = {
                    int(k): v.copy()
                    for k, v in masks.items()
                }

            # 保存 last overlap masks，给下一 Window。
            last_overlap_start = len(window_items) - OVERLAP_FRAMES

            if local_idx >= last_overlap_start and masks:
                offset = local_idx - last_overlap_start

                if 0 <= offset < OVERLAP_FRAMES:
                    last_overlap_local_masks[offset] = {
                        int(k): v.copy()
                        for k, v in masks.items()
                    }

            item = window_items[local_idx]
            emit_wall_time = time.time()
            frame_behavior_tracks = []

            # 对后续 Window：前 overlap 帧只用于 continuity，不再次发布。
            is_fresh = window_no == 0 or local_idx >= OVERLAP_FRAMES
            emit_lag_sec = max(0.0, emit_wall_time - item.wall_time)
            if is_fresh:
                fresh_emit_lags.append(emit_lag_sec)

            for local_obj_id, mask in masks.items():
                local_obj_id = int(local_obj_id)

                # 如果 propagation 里第一次冒出新 local object，现场分配 Global ID。
                if local_obj_id not in local_to_global:
                    global_id = next_global_id
                    next_global_id += 1

                    local_to_global[local_obj_id] = global_id

                    global_tracks[global_id] = GlobalTrack(
                        global_id=global_id,
                        created_window=window_no,
                        last_window=window_no,
                        last_source_seq=item.source_seq,
                        last_wall_time=item.wall_time,
                    )

                global_id = local_to_global[local_obj_id]

                track = global_tracks.get(global_id)

                if track is None:
                    track = GlobalTrack(
                        global_id=global_id,
                        created_window=window_no,
                        last_window=window_no,
                        last_source_seq=item.source_seq,
                        last_wall_time=item.wall_time,
                    )
                    global_tracks[global_id] = track

                track.last_window = window_no
                track.last_source_seq = item.source_seq
                track.last_wall_time = item.wall_time

                geo = geometry_from_mask(mask)

                bbox = geo["bbox"]
                centroid = geo["centroid"]

                if bbox is not None:
                    frame_behavior_tracks.append(
                        {
                            "global_track_id": int(global_id),
                            "bbox": [float(value) for value in bbox],
                            "centroid": (
                                None
                                if centroid is None
                                else [float(value) for value in centroid]
                            ),
                        }
                    )

                observation_rows.append(
                    {
                        "window_no": window_no,
                        "local_frame_idx": local_idx,
                        "sample_id": item.sample_id,
                        "source_seq": item.source_seq,
                        "source_wall_time": item.wall_time,
                        "emit_wall_time": emit_wall_time,
                        "emit_lag_sec": emit_lag_sec,
                        "is_fresh_frame": is_fresh,
                        "local_obj_id": local_obj_id,
                        "global_track_id": global_id,
                        "bbox_x1": bbox[0] if bbox else None,
                        "bbox_y1": bbox[1] if bbox else None,
                        "bbox_x2": bbox[2] if bbox else None,
                        "bbox_y2": bbox[3] if bbox else None,
                        "centroid_x": centroid[0] if centroid else None,
                        "centroid_y": centroid[1] if centroid else None,
                        "mask_area": geo["area"],
                    }
                )

            if behavior_publisher is not None and is_fresh:
                behavior_publisher.publish(item, frame_behavior_tracks)

        torch.cuda.synchronize()
        propagation_sec = time.perf_counter() - prop_t0

        # ---------------------------------------------------------------------
        # last overlap local ID -> global ID
        # ---------------------------------------------------------------------
        new_overlap_bank = {}

        for offset, frame_masks in last_overlap_local_masks.items():
            converted = {}

            for local_obj_id, mask in frame_masks.items():
                global_id = local_to_global.get(int(local_obj_id))

                if global_id is None:
                    continue

                converted[int(global_id)] = mask.copy()

            if converted:
                new_overlap_bank[int(offset)] = converted

        return {
            "session_id": session_id,
            "session_init_sec": session_init_sec,
            "grounding": grounding,
            "association": association,
            "local_to_global": local_to_global,
            "new_overlap_bank": new_overlap_bank,
            "next_global_id": next_global_id,
            "next_empty_scene_streak": next_empty_streak,
            "next_empty_scene_last_poll_wall_time": (
                grounding["performed_poll_wall_time"]
                if grounding["performed_poll_wall_time"] is not None
                else empty_scene_last_poll_wall_time
            ),
            "propagation_start_frame_index": propagation_start_frame_index,
            "propagation_sec": propagation_sec,
            "processed_frames": processed_frames,
            "fresh_emit_lags": fresh_emit_lags,
        }

    finally:
        # caller 仍会再次调用 close_session，close 是幂等保护。
        pass


# =============================================================================
# 11. 汇总
# =============================================================================

def summarize(
    window_rows,
    association_rows,
    observation_rows,
    start_perf,
    final=False,
):
    elapsed_sec = time.perf_counter() - start_perf

    if not window_rows:
        return {
            "elapsed_sec": elapsed_sec,
            "elapsed_minutes": elapsed_sec / 60.0,
            "window_count": 0,
        }

    allocated_after = [
        x["allocated_after_close_gb"]
        for x in window_rows
    ]

    peak_values = [
        x["window_peak_allocated_gb"]
        for x in window_rows
    ]

    fresh_lags = [
        float(x["emit_lag_sec"])
        for x in observation_rows
        if x.get("is_fresh_frame")
    ]

    eligible_old = sum(
        x["assoc_eligible_old_tracks"]
        for x in window_rows
    )

    matched_old = sum(
        x["assoc_matched_tracks"]
        for x in window_rows
    )

    assoc_rate = (
        matched_old / eligible_old
        if eligible_old > 0
        else None
    )

    propagation_frames = sum(
        x["processed_frames"]
        for x in window_rows
    )

    propagation_sec = sum(
        x["propagation_sec"]
        for x in window_rows
    )

    text_sec = sum(
        x["text_sec"]
        for x in window_rows
    )

    text_calls = sum(
        x["text_calls"]
        for x in window_rows
    )

    supplemental_calls = sum(
        x["supplemental_grounding_calls"]
        for x in window_rows
    )

    skipped_samples = sum(
        x["skipped_samples"]
        for x in window_rows
    )

    summary = {
        "elapsed_sec": elapsed_sec,
        "elapsed_minutes": elapsed_sec / 60.0,
        "window_count": len(window_rows),

        "track_windows": sum(
            1
            for x in window_rows
            if x["grounding_found_any"]
        ),

        "empty_scene_windows": sum(
            1
            for x in window_rows
            if x["empty_scene_mode"]
        ),

        "total_text_calls": text_calls,
        "total_text_sec": text_sec,
        "text_fraction_of_elapsed": (
            text_sec / elapsed_sec
            if elapsed_sec > 0
            else 0.0
        ),

        "supplemental_grounding_calls": supplemental_calls,

        "propagation_fps": (
            propagation_frames / propagation_sec
            if propagation_sec > 0
            else 0.0
        ),

        "total_skipped_ai_samples": skipped_samples,

        "assoc_eligible_old_tracks": eligible_old,
        "assoc_matched_tracks": matched_old,
        "association_continuity_rate": assoc_rate,
        "association_has_enough_samples": (
            eligible_old >= MIN_ASSOC_ELIGIBLE_TRACKS
        ),
        "association_target_met": bool(
            assoc_rate is not None
            and eligible_old >= MIN_ASSOC_ELIGIBLE_TRACKS
            and assoc_rate >= TARGET_ASSOC_CONTINUITY_RATE
        ),

        "fresh_observation_count": len(fresh_lags),
        "fresh_observation_lag_avg_sec": (
            float(np.mean(fresh_lags))
            if fresh_lags
            else None
        ),
        "fresh_observation_lag_p50_sec": percentile(fresh_lags, 50),
        "fresh_observation_lag_p95_sec": percentile(fresh_lags, 95),
        "fresh_observation_lag_max_sec": (
            max(fresh_lags)
            if fresh_lags
            else None
        ),

        "fresh_latency_target_met": bool(
            fresh_lags
            and float(np.mean(fresh_lags))
            <= TARGET_FRESH_OBSERVATION_LAG_AVG_SEC
            and percentile(fresh_lags, 95)
            <= TARGET_FRESH_OBSERVATION_LAG_P95_SEC
        ),

        "max_window_peak_allocated_gb": max(peak_values),
        "first_allocated_after_close_gb": allocated_after[0],
        "last_allocated_after_close_gb": allocated_after[-1],
        "allocated_after_close_slope_gb_per_window": linear_slope(
            allocated_after
        ),

        "memory_safe": bool(
            max(peak_values) <= SAFE_MAX_WINDOW_PEAK_GB
            and abs(linear_slope(allocated_after))
            <= SAFE_MAX_MEMORY_SLOPE_GB_PER_WINDOW
        ),
    }

    if final:
        summary["demo_acceptance"] = {
            "memory_safe": summary["memory_safe"],
            "association_target_met": summary["association_target_met"],
            "fresh_latency_target_met": summary["fresh_latency_target_met"],
        }

    return summary


# =============================================================================
# 12. Main
# =============================================================================

def main():
    validate_config()
    configure_torch()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for filename in [
        "stage5_1_report.json",
        "window_metrics.csv",
        "association_metrics.csv",
        "track_observations.csv",
    ]:
        path = OUTPUT_DIR / filename
        if path.exists():
            path.unlink()

    sampler = AsyncAISampler().start()

    print()
    print(
        f"RTSP source: "
        f"{sampler.source_width}x{sampler.source_height} "
        f"@ {sampler.source_fps:.2f} FPS"
    )

    predictor = None
    behavior_publisher = None

    window_rows = []
    association_rows = []
    observation_rows = []

    global_tracks: Dict[int, GlobalTrack] = {}

    next_global_id = 1
    old_overlap_bank = {}
    previous_overlap_items: List[SampledFrame] = []

    empty_scene_streak = 0
    empty_scene_last_poll_wall_time = None

    status = "PASS"
    stop_reason = "completed"
    error_text = ""

    final_mem = None
    model_mem = None
    start_perf = None

    try:
        if BEHAVIOR_ENABLED:
            behavior_publisher = BehaviorLivePublisher()

        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

        with (
            torch.inference_mode(),
            autocast_context(),
        ):
            predictor = build_predictor()
            tune_runtime(predictor)

            torch.cuda.synchronize()
            model_mem = gpu_mem()

            print()
            print(
                f"Model loaded: "
                f"alloc={model_mem['allocated_gb']:.2f}GB | "
                f"reserved={model_mem['reserved_gb']:.2f}GB | "
                f"free={model_mem['driver_free_gb']:.2f}GB"
            )

            start_perf = time.perf_counter()
            end_perf = start_perf + RUN_MINUTES * 60.0

            window_no = 0

            while time.perf_counter() < end_perf:
                before_mem = gpu_mem()

                if (
                    before_mem["allocated_gb"] > VRAM_SAFETY_ALLOCATED_GB
                    or before_mem["driver_free_gb"] < VRAM_SAFETY_FREE_GB
                ):
                    status = "SAFETY_STOP"
                    stop_reason = "vram_safety_before_window"
                    break

                cycle_t0 = time.perf_counter()

                # -----------------------------------------------------------------
                # 不再“SAM 完成后开始慢慢采 8/28 张”。
                # Sampler 一直在后台采；这里直接拿已经准备好的最新样本。
                # -----------------------------------------------------------------
                try:
                    window_build = build_next_window(
                        sampler=sampler,
                        previous_overlap_items=previous_overlap_items,
                    )
                except Exception as exc:
                    status = "ERROR"
                    stop_reason = "window_build_error"
                    error_text = repr(exc)
                    break

                window_items = window_build["window_items"]
                new_items = window_build["new_items"]

                # 下一 Window 要复用本 Window 最后 overlap 张。
                previous_overlap_items = [
                    SampledFrame(
                        sample_id=x.sample_id,
                        source_seq=x.source_seq,
                        wall_time=x.wall_time,
                        frame=x.frame.copy(),
                    )
                    for x in window_items[-OVERLAP_FRAMES:]
                ]

                materialize_window(window_items)

                latest_input_age_before_session = max(
                    0.0,
                    time.time() - window_items[-1].wall_time,
                )

                reset_peak_only()

                session_id = None

                try:
                    result = run_window(
                        predictor=predictor,
                        window_items=window_items,
                        old_overlap_bank=old_overlap_bank,
                        global_tracks=global_tracks,
                        next_global_id=next_global_id,
                        window_no=window_no,
                        empty_scene_streak=empty_scene_streak,
                        empty_scene_last_poll_wall_time=(
                            empty_scene_last_poll_wall_time
                        ),
                        observation_rows=observation_rows,
                        association_rows=association_rows,
                        behavior_publisher=behavior_publisher,
                    )

                    session_id = result["session_id"]

                    next_global_id = result["next_global_id"]
                    empty_scene_streak = result[
                        "next_empty_scene_streak"
                    ]
                    empty_scene_last_poll_wall_time = result[
                        "next_empty_scene_last_poll_wall_time"
                    ]

                    old_overlap_bank = result["new_overlap_bank"]

                except torch.cuda.OutOfMemoryError as exc:
                    status = "OOM"
                    stop_reason = "cuda_oom"
                    error_text = repr(exc)
                    break

                except Exception as exc:
                    status = "ERROR"
                    stop_reason = "runtime_error"
                    error_text = repr(exc)
                    break

                finally:
                    close_session(
                        predictor,
                        session_id,
                    )

                gc.collect()
                torch.cuda.synchronize()

                after_mem = gpu_mem()

                cycle_sec = time.perf_counter() - cycle_t0

                grounding = result["grounding"]
                association = result["association"]

                expected_old_count = len(
                    association["old_global_ids"]
                )

                matched_count = len(
                    association["matches"]
                )

                fresh_lags = result["fresh_emit_lags"]

                row = {
                    "window_no": window_no,
                    "elapsed_minutes": (
                        (time.perf_counter() - start_perf) / 60.0
                    ),

                    "is_first_window": window_build["is_first_window"],
                    "window_frames": len(window_items),
                    "new_frames": len(new_items),

                    "latest_input_age_before_session_sec": (
                        latest_input_age_before_session
                    ),

                    "skipped_samples": window_build["skipped_samples"],
                    "available_new_samples": (
                        window_build["available_new_samples"]
                    ),
                    "bridge_gap_samples": (
                        window_build["bridge_gap_samples"]
                    ),

                    "expected_old_tracks": expected_old_count,

                    "text_calls": grounding["text_calls"],
                    "text_sec": grounding["text_sec"],
                    "supplemental_grounding_calls": (
                        grounding["supplemental_calls"]
                    ),
                    "grounding_throttled": grounding["throttled"],
                    "propagation_start_frame_index": result[
                        "propagation_start_frame_index"
                    ],
                    "grounding_local_objects": len(
                        grounding["local_ids"]
                    ),
                    "grounding_found_any": grounding["found_any"],

                    "empty_scene_streak_after": empty_scene_streak,
                    "empty_scene_mode": bool(
                        expected_old_count == 0
                        and empty_scene_streak
                        >= EMPTY_SCENE_ENTER_AFTER_WINDOWS
                    ),

                    "assoc_eligible_old_tracks": expected_old_count,
                    "assoc_matched_tracks": matched_count,
                    "assoc_match_rate": (
                        matched_count / expected_old_count
                        if expected_old_count > 0
                        else None
                    ),
                    "assoc_unmatched_old_tracks": len(
                        association["unmatched_old_ids"]
                    ),
                    "assoc_ambiguous_new_tracks": len(
                        association["ambiguous_new_ids"]
                    ),

                    "active_global_track_count_total": len(global_tracks),

                    "session_init_sec": result["session_init_sec"],
                    "propagation_sec": result["propagation_sec"],
                    "processed_frames": result["processed_frames"],
                    "propagation_fps": (
                        result["processed_frames"]
                        / result["propagation_sec"]
                        if result["propagation_sec"] > 0
                        else 0.0
                    ),

                    "fresh_emit_lag_avg_sec": (
                        float(np.mean(fresh_lags))
                        if fresh_lags
                        else None
                    ),
                    "fresh_emit_lag_p95_sec": percentile(
                        fresh_lags,
                        95,
                    ),

                    "cycle_sec": cycle_sec,

                    "window_peak_allocated_gb": (
                        after_mem["peak_allocated_gb"]
                    ),
                    "window_peak_reserved_gb": (
                        after_mem["peak_reserved_gb"]
                    ),
                    "allocated_after_close_gb": (
                        after_mem["allocated_gb"]
                    ),
                    "reserved_after_close_gb": (
                        after_mem["reserved_gb"]
                    ),
                    "driver_free_after_close_gb": (
                        after_mem["driver_free_gb"]
                    ),
                }

                window_rows.append(row)

                print(
                    f"[w={window_no:04d}] "
                    f"obj={len(grounding['local_ids']):02d} | "
                    f"text={grounding['text_sec']:4.2f}s/"
                    f"{grounding['text_calls']} "
                    f"(supp={grounding['supplemental_calls']}) | "
                    f"assoc={matched_count}/{expected_old_count} | "
                    f"prop={row['propagation_fps']:4.2f}FPS | "
                    f"freshLag="
                    f"{(row['fresh_emit_lag_avg_sec'] or 0):4.2f}s | "
                    f"skip={row['skipped_samples']:02d} | "
                    f"gap={row['bridge_gap_samples']:02d} | "
                    f"empty={empty_scene_streak:02d} | "
                    f"throttle={int(grounding['throttled'])} | "
                    f"start={result['propagation_start_frame_index']} | "
                    f"peak={row['window_peak_allocated_gb']:4.2f}GB | "
                    f"after={row['allocated_after_close_gb']:4.2f}GB"
                )

                if (
                    after_mem["allocated_gb"] > VRAM_SAFETY_ALLOCATED_GB
                    or after_mem["driver_free_gb"] < VRAM_SAFETY_FREE_GB
                ):
                    status = "SAFETY_STOP"
                    stop_reason = "vram_safety_after_window"
                    break

                if not KEEP_WINDOW_JPEGS and WINDOW_DIR.exists():
                    shutil.rmtree(
                        WINDOW_DIR,
                        ignore_errors=True,
                    )

                window_no += 1

            final_mem = gpu_mem()

    finally:
        sampler.stop()

        if behavior_publisher is not None:
            behavior_publisher.close()

        if predictor is not None:
            try:
                del predictor
            except Exception:
                pass

        if not KEEP_WINDOW_JPEGS and WINDOW_DIR.exists():
            shutil.rmtree(
                WINDOW_DIR,
                ignore_errors=True,
            )

    if start_perf is None:
        start_perf = time.perf_counter()

    report = summarize(
        window_rows=window_rows,
        association_rows=association_rows,
        observation_rows=observation_rows,
        start_perf=start_perf,
        final=True,
    )

    report.update(
        {
            "stage": "5.1.1_demo_latency_finalization",
            "status": status,
            "stop_reason": stop_reason,
            "error": error_text,

            "config": {
                "target_ai_fps": TARGET_AI_FPS,
                "ai_resolution": f"{AI_WIDTH}x{AI_HEIGHT}",
                "window_frames": WINDOW_FRAMES,
                "overlap_frames": OVERLAP_FRAMES,
                "batch": FIXED_BATCH,

                "async_ai_sampler": True,
                "bounded_ring_buffer": RING_BUFFER_MAX_FRAMES,
                "latest_sample_drop_policy": True,

                "association_aware_regrounding": True,
                "association_target": TARGET_ASSOC_CONTINUITY_RATE,

                "empty_scene_throttle": True,
                "empty_scene_enter_after_windows": (
                    EMPTY_SCENE_ENTER_AFTER_WINDOWS
                ),
                "empty_scene_poll_interval_sec": (
                    EMPTY_SCENE_POLL_INTERVAL_SEC
                ),
                "skip_overlap_propagation": True,
                "fresh_propagation_start_frame_index": OVERLAP_FRAMES,

                "bf16": ENABLE_BF16,
                "compile": ENABLE_COMPILE,
                "cpu_output_offload": ENABLE_CPU_OUTPUT_OFFLOAD,
                "trim_old_outputs": ENABLE_TRIM_OLD_OUTPUTS,

                "point_handoff": False,
                "multi_frame_point_handoff": False,
                "reid": False,
            },

            "global_track_count_total": len(global_tracks),
            "sampler_total_decoded": sampler.total_decoded,
            "sampler_total_sampled": sampler.total_sampled,
            "sampler_ring_dropped": sampler.total_ring_dropped,
        }
    )

    if model_mem is not None:
        report["model_loaded_allocated_gb"] = model_mem["allocated_gb"]
        report["model_loaded_reserved_gb"] = model_mem["reserved_gb"]

    if final_mem is not None:
        report.update(
            {
                "final_allocated_gb": final_mem["allocated_gb"],
                "final_reserved_gb": final_mem["reserved_gb"],
                "final_peak_allocated_gb": final_mem[
                    "peak_allocated_gb"
                ],
                "final_driver_free_gb": final_mem["driver_free_gb"],
                "final_driver_total_gb": final_mem["driver_total_gb"],
            }
        )

    report_path = OUTPUT_DIR / "stage5_1_report.json"
    window_csv = OUTPUT_DIR / "window_metrics.csv"
    association_csv = OUTPUT_DIR / "association_metrics.csv"
    observation_csv = OUTPUT_DIR / "track_observations.csv"

    write_json(report_path, report)
    rewrite_csv(window_csv, window_rows)
    rewrite_csv(association_csv, association_rows)
    rewrite_csv(observation_csv, observation_rows)

    print()
    print("=" * 108)
    print("FINAL REPORT")
    print("=" * 108)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("=" * 108)
    print()
    print("输出：")
    print(report_path)
    print(window_csv)
    print(association_csv)
    print(observation_csv)


if __name__ == "__main__":
    main()
