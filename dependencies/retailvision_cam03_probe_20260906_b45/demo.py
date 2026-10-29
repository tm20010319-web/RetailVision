#!/usr/bin/env python
"""
门店摄像头 AI 分析 — CLI 演示入口。

用法：
    python demo.py historical <video_path> [--mock-llm] [--process-every-seconds 3.0]
    python demo.py profile <image_path>   [--mock-llm]
    python demo.py realtime <camera_id>   [--mock-llm]

输出 JSON + Markdown 报告到 outputs/ 目录。
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import cv2


def ensure_output_dir(sub_dir: str = "") -> Path:
    """创建输出目录。"""
    output_dir = Path("outputs") / sub_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_result(result: dict, output_dir: Path, prefix: str) -> None:
    """保存 JSON 和 Markdown 报告。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON
    json_path = output_dir / f"{prefix}_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"[JSON] {json_path}")

    # Markdown
    md_path = output_dir / f"{prefix}_{timestamp}.md"
    md_content = generate_markdown(result, prefix)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"[Markdown] {md_path}")


def generate_markdown(result: dict, task_type: str) -> str:
    """根据任务类型生成 Markdown 报告。"""
    lines = []
    lines.append(f"# 门店 AI 分析报告 — {task_type}\n")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    if task_type == "historical":
        report = result.get("report", {})
        summary = result.get("summary", {})
        raw = result.get("raw_stats", {})

        lines.append(f"\n## {report.get('report_title', '历史视频分析报告')}\n")
        lines.append(f"\n### 客流概述\n")
        lines.append(report.get("traffic_summary", "无数据"))

        lines.append(f"\n\n### 区域洞察\n")
        for insight in report.get("area_insights", []):
            lines.append(f"- {insight}")

        lines.append(f"\n### 运营建议\n")
        for suggestion in report.get("operation_suggestions", []):
            lines.append(f"- {suggestion}")

        lines.append(f"\n### 导购建议\n")
        for suggestion in report.get("sales_suggestions", []):
            lines.append(f"- {suggestion}")

        lines.append(f"\n### 原始统计\n")
        lines.append(f"- 总帧数：{raw.get('total_frames', 0)}")
        lines.append(f"- 处理帧数：{raw.get('processed_frames', 0)}")
        lines.append(f"- 独立访客数：{raw.get('unique_tracks', 0)}")
        lines.append(f"- 区域事件数：{raw.get('total_events', 0)}")

        heatmap = summary.get("zone_heatmap", {})
        if heatmap:
            lines.append(f"\n### 区域热力图\n")
            for zone, count in sorted(heatmap.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"- {zone}：{count} 人次")

    elif task_type == "profile":
        profile = result.get("profile", {})
        lines.append(f"\n## 顾客穿搭画像\n")
        lines.append(f"- 追踪 ID：{result.get('track_id', 'N/A')}")
        lines.append(f"- 区域：{result.get('zone_name', 'N/A')}")
        lines.append(f"- 风格标签：{', '.join(profile.get('style_tags', []))}")
        lines.append(f"- 购买意图分：{profile.get('purchase_intent_score', 0):.2f}")
        lines.append(f"- 置信度：{profile.get('confidence', 0):.2f}")
        lines.append(f"\n### 画像摘要\n{profile.get('profile_summary', '无数据')}")
        lines.append(f"\n### 导购建议\n{profile.get('sales_suggestion', '无数据')}")

        interest = profile.get("interest_categories", [])
        if interest:
            lines.append(f"\n### 感兴趣品类\n")
            for cat in interest:
                lines.append(f"- {cat}")

    elif task_type == "realtime":
        lines.append(f"\n## 实时进店检测\n")
        entries = result.get("confirmed_entries", [])
        member_count = result.get("total_members", 0)
        anonymous_count = result.get("total_anonymous", 0)
        lines.append(f"会员进店：{member_count} 人，匿名访客：{anonymous_count} 人")
        for entry in entries:
            etype = entry.get("entry_type", "unknown")
            tid = entry.get("track_id", "?")
            if etype == "member":
                sess = entry.get("session", {})
                lines.append(f"- [会员] Track ID: {tid}, 会员: {sess.get('member_id', '?')}, 置信度: {sess.get('confidence', 0):.2f}")
            else:
                lines.append(f"- [匿名] Track ID: {tid}, 原因: {entry.get('reason', '?')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 命令处理
# ---------------------------------------------------------------------------

def cmd_historical(args: argparse.Namespace) -> None:
    """历史视频经营分析。"""
    from pipelines import create_historical_pipeline

    print(f"[启动] 历史视频分析: {args.video_path}")
    print(f"[配置] MockLLM={args.mock_llm}, 采样间隔={args.process_every_seconds}s")

    pipeline = create_historical_pipeline(
        use_mock_llm=args.mock_llm,
    )

    def on_progress(processed, total):
        if total > 0:
            pct = processed / total * 100
            print(f"\r[进度] {processed}/{total} 帧 ({pct:.1f}%)", end="", flush=True)

    result = pipeline.run(
        video_source=args.video_path,
        process_every_seconds=args.process_every_seconds,
        on_progress=on_progress,
        time_range=args.time_range,
    )
    print()  # 换行

    output_dir = ensure_output_dir("historical")
    save_result(result, output_dir, "historical")

    print("\n[报告摘要]")
    print(result["report"].get("traffic_summary", "无数据"))


def cmd_profile(args: argparse.Namespace) -> None:
    """顾客穿搭画像。"""
    from agent.detector import VisionDetector
    from pipelines import create_customer_profile_pipeline

    print(f"[启动] 顾客画像分析: {args.image_path}")
    print(f"[配置] MockLLM={args.mock_llm}")

    # 读取图片
    frame = cv2.imread(args.image_path)
    if frame is None:
        print(f"[错误] 无法读取图片: {args.image_path}")
        sys.exit(1)

    # YOLO 检测最佳人物
    detector = VisionDetector()
    detector.initialize()
    detections = detector.process_frame(frame)

    if not detections:
        print("[错误] 未检测到任何人物")
        sys.exit(1)

    # 取置信度最高的检测
    best = max(detections, key=lambda d: d.confidence)
    print(f"[检测] 最佳人物: confidence={best.confidence:.2f}, bbox={best.bbox}")

    # 运行画像 Pipeline
    pipeline = create_customer_profile_pipeline(use_mock_llm=args.mock_llm)
    result = pipeline.run(
        track_id=best.track_id or 0,
        frame=frame,
        bbox=best.bbox,
        camera_id="demo",
        zone_name=args.zone or "unknown",
    )

    output_dir = ensure_output_dir("profile")
    save_result(result, output_dir, "profile")

    print(f"\n[画像摘要] {result['profile']['profile_summary']}")
    print(f"[导购建议] {result['profile']['sales_suggestion']}")


def cmd_realtime(args: argparse.Namespace) -> None:
    """实时进店检测。"""
    from pipelines import create_realtime_pipeline

    camera_id = int(args.camera_id) if args.camera_id.isdigit() else args.camera_id
    print(f"[启动] 实时进店检测: camera_id={camera_id}")
    print(f"[配置] MockLLM={args.mock_llm}")
    print("[提示] 按 Ctrl+C 停止")

    member_count = 0
    anonymous_count = 0
    all_entries = []

    def on_member_entry(session_dict):
        nonlocal member_count
        member_count += 1
        print(f"[会员进店] Track ID: {session_dict['track_id']}, 会员: {session_dict['member_id']}, 置信度: {session_dict['confidence']:.2f}")

    def on_anonymous_visit(track_id, detection, reason):
        nonlocal anonymous_count
        anonymous_count += 1
        print(f"[匿名访客] Track ID: {track_id}, 原因: {reason}")

    def on_new_customer(track_id, detection, frame):
        nonlocal anonymous_count
        anonymous_count += 1
        print(f"[新顾客] Track ID: {track_id}, 置信度: {detection['confidence']:.2f}")

    pipeline = create_realtime_pipeline(
        on_new_customer=on_new_customer,
        on_member_entry=on_member_entry,
        on_anonymous_visit=on_anonymous_visit,
        process_every_seconds=0.5,
    )

    import time
    try:
        cap = cv2.VideoCapture(camera_id)
        if not cap.isOpened():
            print(f"[错误] 无法打开摄像头: {camera_id}")
            sys.exit(1)

        while True:
            ret, frame = cap.read()
            if not ret:
                break
            result = pipeline.process_frame(frame)
            all_entries.extend(result["confirmed_entries"])
            time.sleep(0.1)

    except KeyboardInterrupt:
        print(f"\n[停止] 会员: {member_count}, 匿名: {anonymous_count}")
    finally:
        cap.release()

    result = {
        "task_type": "realtime",
        "confirmed_entries": all_entries,
        "total_members": member_count,
        "total_anonymous": anonymous_count,
    }
    output_dir = ensure_output_dir("realtime")
    save_result(result, output_dir, "realtime")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="门店摄像头 AI 分析 CLI 演示"
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # historical
    p_hist = subparsers.add_parser("historical", help="历史视频经营分析")
    p_hist.add_argument("video_path", help="视频文件路径")
    p_hist.add_argument("--mock-llm", action="store_true", default=True,
                        help="使用 Mock LLM（默认开启）")
    p_hist.add_argument("--use-real-llm", action="store_true",
                        help="使用真实 Ollama LLM（覆盖 --mock-llm）")
    p_hist.add_argument("--process-every-seconds", type=float, default=3.0,
                        help="采样间隔（秒），默认 3.0")
    p_hist.add_argument("--time-range", default=None,
                        help="时段描述（如 '2024-01-01 10:00-12:00'）")

    # profile
    p_prof = subparsers.add_parser("profile", help="顾客穿搭画像")
    p_prof.add_argument("image_path", help="顾客图片路径")
    p_prof.add_argument("--mock-llm", action="store_true", default=True,
                        help="使用 Mock LLM（默认开启）")
    p_prof.add_argument("--use-real-llm", action="store_true",
                        help="使用真实 Ollama LLM（覆盖 --mock-llm）")
    p_prof.add_argument("--zone", default=None, help="区域名称")

    # realtime
    p_rt = subparsers.add_parser("realtime", help="实时进店检测")
    p_rt.add_argument("camera_id", help="摄像头 ID（如 0）")
    p_rt.add_argument("--mock-llm", action="store_true", default=True,
                      help="使用 Mock LLM（默认开启）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 处理 --use-real-llm 覆盖
    if getattr(args, "use_real_llm", False):
        args.mock_llm = False

    if args.command == "historical":
        cmd_historical(args)
    elif args.command == "profile":
        cmd_profile(args)
    elif args.command == "realtime":
        cmd_realtime(args)


if __name__ == "__main__":
    main()
