"""Analyze fixed objects in a local recording: VLX, anonymous users, hold times.

No API, database, membership, or live-camera side effects. Example:
  python scripts/run_vlx_interaction_video.py --video recording.mp4
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.vlx_interaction import InteractionAnalyzer, InteractionConfig, TARGETS, INTERACTION_POLICY


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    # Windows readers can briefly hold a share lock on the progress file.
    # Keep atomic replacement, but tolerate that transient lock.
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(.05 * (attempt+1))


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_config(path):
    import yaml
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("sample_fps", "recognition_interval_seconds", "tracking_ttl_seconds", "preview_fps"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be finite and positive")
    if config["tracking_ttl_seconds"] < config["recognition_interval_seconds"]:
        raise ValueError("tracking TTL must cover the recognition interval")
    if config.get("appearance_tracking", "none") not in ("none", "csrt"):
        raise ValueError("appearance_tracking must be none or csrt")
    interaction = InteractionConfig(**config.get("interaction", {}))
    config["interaction"] = asdict(interaction)
    if 1 / config["sample_fps"] > interaction.max_observation_gap_seconds:
        raise ValueError("sample interval exceeds allowed observation gap")
    for key in ("pose_confidence", "wrist_confidence"):
        if not 0 < config[key] <= 1:
            raise ValueError(f"invalid {key}")
    for key in ("max_new_tokens", "pose_image_size"):
        if not isinstance(config[key], int) or isinstance(config[key], bool) or config[key] <= 0:
            raise ValueError(f"invalid {key}")
    for key in ("pose_weights", "font_path", "runtime_root", "vlx_config"):
        config[key] = str(project_path(config[key]))
    vlx = yaml.safe_load(Path(config["vlx_config"]).read_text(encoding="utf-8"))
    for key in ("model_path", "external_repository", "detector_checkpoint"):
        vlx[key] = str(project_path(vlx[key]))
    return config, vlx


def preflight(config, vlx):
    import importlib.util
    required = {k: str(vlx[k]) for k in ("external_repository", "model_path", "detector_checkpoint")}
    required["pose_weights"] = config["pose_weights"]
    required["font_path"] = config["font_path"]
    missing = [key for key, value in required.items() if not Path(value).exists()]
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            missing.append(binary)
    for package in ("cv2", "numpy", "PIL", "yaml", "torch", "ultralytics", "transformers", "bitsandbytes"):
        if importlib.util.find_spec(package) is None:
            missing.append(package)
    if missing:
        raise RuntimeError("missing runtime dependencies: " + ", ".join(missing))
    if config.get("appearance_tracking") == "csrt":
        import cv2
        if not hasattr(cv2, "TrackerCSRT_create"):
            raise RuntimeError("CSRT requires opencv-contrib-python; use D:/project/RetailVisionDev/envs/vlx-interaction-py311/Scripts/python.exe")
    return {"status": "dependencies_available", "python": sys.executable, "paths": required,
            "targets": [asdict(t) for t in TARGETS], "model_inference_tested": False}


def probe_video(path):
    """Use per-frame presentation timestamps, including VFR. No FPS clock."""
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_streams",
               "-show_entries", "stream=width,height,duration:frame=best_effort_timestamp_time,duration_time,pkt_duration_time",
               "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True, check=True, encoding="utf-8")
    return parse_timeline(json.loads(result.stdout))


def parse_timeline(probe):
    streams = probe.get("streams", [])
    frames = probe.get("frames", [])
    if not streams or not frames:
        raise ValueError("video has no decodable video frames")
    pts = [float(row["best_effort_timestamp_time"]) for row in frames]
    if any(not math.isfinite(t) for t in pts) or any(b <= a for a, b in zip(pts, pts[1:])):
        raise ValueError("source video PTS missing or not strictly increasing")
    times = [t-pts[0] for t in pts]
    last_duration = float(frames[-1].get("duration_time", frames[-1].get("pkt_duration_time", 0)))
    duration = float(streams[0].get("duration", 0))
    if math.isfinite(last_duration) and last_duration > 0:
        duration = times[-1] + last_duration
    if not math.isfinite(duration) or duration <= times[-1]:
        raise ValueError("source end time unavailable; no inferred final frame duration")
    return {"timestamps": times, "duration_seconds": duration, "source_pts_origin_seconds": pts[0],
            "width": int(streams[0]["width"]), "height": int(streams[0]["height"]), "frame_count": len(times)}


class PreviewWriter:
    """Presentation-only CFR resampling; original PTS still drives all facts."""
    def __init__(self, output, fps, frame):
        self.output, self.fps = output, fps
        self.index, self.image = 0, frame
        self.log = (output / "preview_encode.log").open("wb")
        h, w = frame.shape[:2]
        self.process = subprocess.Popen([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264",
            "-preset", "fast", "-crf", "23", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output / "annotated.partial.mp4")],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.log)

    def push(self, t, image):
        while self.index/self.fps < t - 1e-9:
            self.process.stdin.write(self.image.tobytes())
            self.index += 1
        self.image = image

    def finish(self, duration):
        self.push(duration, self.image)
        self.process.stdin.close()
        try:
            code = self.process.wait(timeout=60)
            if code:
                raise RuntimeError("preview encoding failed; inspect preview_encode.log")
            (self.output / "annotated.partial.mp4").replace(self.output / "annotated.mp4")
        finally:
            self.log.close()

    def abort(self):
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=10)
        if self.process.stdin and not self.process.stdin.closed:
            try:
                self.process.stdin.close()
            except (OSError, BrokenPipeError):
                pass
        self.log.close()


class Overlay:
    def __init__(self, font_path):
        from PIL import ImageFont
        self.font = ImageFont.truetype(font_path, 17)
        self.action_font = ImageFont.truetype(font_path, 24)
        self.evidence_label = ""

    def render(self, frame, timestamp, objects, people, state, totals, *, report=None):
        import cv2
        import numpy as np
        from PIL import Image, ImageDraw
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        width, height = image.size
        video_width = max(720, width)
        canvas = Image.new("RGB", (video_width+480, max(744, height+202)), "#111827")
        canvas.paste(image, (0, 42))
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 9), f"VLX{self.evidence_label} | 视频 {timestamp:.2f}s | 拿起 {totals['pickup_count']} 次 / 放下 {totals['putdown_count']} 次", font=self.font, fill="white")
        for p in people:
            box = tuple(v*s + offset for v, s, offset in zip(p.bbox, (width, height, width, height), (0, 42, 0, 42)))
            draw.rectangle(box, outline="#67e8f9", width=2)
            label = f"用户{p.user_id}" if p.user_id else "用户关联不确定"
            draw.text((box[0], max(42, box[1]-22)), label, font=self.font, fill="#67e8f9", stroke_width=1, stroke_fill="black")
            for wrist in p.wrists:
                if wrist is not None:
                    x, y = wrist[0]*width, wrist[1]*height+42
                    draw.ellipse((x-3, y-3, x+3, y+3), fill="#67e8f9")
        states = {row["object_key"]: row for row in state["objects"]}
        summaries = {(row["user_id"], row["object_key"]): row
                     for row in (report or {}).get("by_user_object", [])}
        action_labels = []
        for obj in objects:
            box = tuple(v*s + offset for v, s, offset in zip(obj.bbox, (width, height, width, height), (0, 42, 0, 42)))
            row = states[obj.key]
            holding = row["state"] == "holding" and row["user_id"] is not None
            color = "#86efac" if holding else "#fcd34d"
            draw.rectangle(box, outline=color, width=2)
            if holding:
                summary = summaries.get((row["user_id"], obj.key))
                count = f" | 累计拿起 {summary['pickup_count']} 次" if summary else ""
                label = f"用户{row['user_id']} | {row['object_name']}{count} | 本次已拿起 {row['elapsed_holding_seconds']:.1f} 秒"
                font = self.action_font if width >= 1280 else self.font
                bounds = draw.textbbox((0, 0), label, font=font)
                if bounds[2]-bounds[0] > video_width-8:
                    font = self.font
                    bounds = draw.textbbox((0, 0), label, font=font)
                x = max(4, min(box[0], video_width-(bounds[2]-bounds[0])-8))
                y = max(42, box[1]-34)
                step = font.size+10
                candidates = [y] + list(range(int(y)-step, 41, -step))
                candidates += list(range(int(box[3])+4, height+42-step, step))
                for candidate in candidates:
                    y = candidate
                    text_bounds = draw.textbbox((x, y), label, font=font)
                    rect = (text_bounds[0]-4, text_bounds[1]-3, text_bounds[2]+4, text_bounds[3]+3)
                    if not any(rect[0] < r[2] and rect[2] > r[0] and rect[1] < r[3] and rect[3] > r[1]
                               for r in action_labels):
                        break
                action_labels.append(rect)
                draw.rectangle(rect, fill="#111827")
                draw.text((x, y), label, font=font, fill=color)
            else:
                draw.text((box[0], box[1]), row["object_name"], font=self.font, fill=color, stroke_width=1, stroke_fill="black")
        labels = {"unseen": "未定位", "unknown": "状态待确认", "resting": "放置 / 待确认",
                  "holding": "持有中", "holding_unobserved": "持有中（暂不可见）", "pickup_pending": "拿起待确认"}
        for i, row in enumerate(state["objects"]):
            detail = labels[row["state"]]
            if row["user_id"] is not None:
                detail += f" | 用户{row['user_id']} | {row['elapsed_holding_seconds']:.1f} 秒"
            draw.text((10, height+49+i*23), f"{row['object_name']}：{detail}", font=self.font, fill="#e2e8f0")
        self._draw_user_statistics(draw, video_width, canvas.height, timestamp, states, report or {})
        return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)

    def _draw_user_statistics(self, draw, left, height, timestamp, states, report):
        # Read only the current report; future sessions must never leak into an earlier frame.
        rows = report.get("by_user_object", [])
        latest = {(s["user_id"], s["object_key"]): s for s in report.get("sessions", [])}
        object_totals = report.get("by_object", [])
        users_top = 86+24*len(object_totals)
        rows_top = users_top+64
        row_height = 64
        per_page = max(1, (height-rows_top-16)//row_height)
        pages = max(1, math.ceil(len(rows)/per_page))
        page = int(timestamp//8) % pages
        draw.rectangle((left, 0, left+479, height), fill="#172033")
        draw.line((left, 0, left, height), fill="#334155", width=2)
        draw.text((left+16, 12), "物品总次数", font=self.action_font, fill="white")
        draw.text((left+16, 43), "本视频内所有用户合计", font=self.font, fill="#94a3b8")
        for index, row in enumerate(object_totals):
            text = f"{row['object_name']} | 拿起 {row['pickup_count']} 次 | 放下 {row['putdown_count']} 次"
            draw.text((left+16, 74+index*24), text, font=self.font,
                      fill="#86efac" if row['pickup_count'] else "#94a3b8")
        draw.line((left+16, users_top-6, left+464, users_top-6), fill="#334155")
        draw.text((left+16, users_top), "用户 / 物品统计", font=self.action_font, fill="white")
        page_text = f" | 第 {page+1}/{pages} 页" if pages > 1 else ""
        draw.text((left+16, users_top+31), "按匿名用户分别累计"+page_text, font=self.font, fill="#94a3b8")
        if not rows:
            draw.text((left+16, rows_top+12), "暂无已记录的拿起操作", font=self.font, fill="#cbd5e1")
        for index, row in enumerate(rows[page*per_page:(page+1)*per_page]):
            top = rows_top+index*row_height
            key = row["user_id"], row["object_key"]
            current = states.get(row["object_key"], {})
            session = latest.get(key, {})
            active = (current.get("user_id") == row["user_id"]
                      and current.get("state") in ("holding", "holding_unobserved"))
            color = "#86efac" if active else "#e2e8f0"
            detail = f"累计拿起 {row['pickup_count']} 次"
            if active:
                detail += f" | 本次已拿起 {current['elapsed_holding_seconds']:.1f} 秒"
            note = f"已放下 {row['putdown_count']} 次"
            if active and session.get("attribution_uncertain"):
                note += " | 归属待确认"
            elif active and current["state"] == "holding_unobserved":
                note += " | 暂不可见"
            elif not active and session.get("status") == "incomplete":
                note += " | 最近记录未完成"
            elif not active and session.get("status") == "completed":
                note += (f" | 持有 {session['holding_seconds']:.1f}s（含遮挡推定）"
                         if session.get("duration_includes_occlusion")
                         else f" | 最近持有 {session['holding_seconds']:.1f} 秒")
            if active and session.get("duration_includes_occlusion"):
                note += " | 含遮挡推定"
            draw.text((left+16, top), f"{row['user_label']} | {row['object_name']}", font=self.font, fill=color)
            draw.text((left+16, top+20), detail, font=self.font, fill=color)
            draw.text((left+16, top+40), note, font=self.font, fill="#94a3b8")
            draw.line((left+16, top+row_height-1, left+464, top+row_height-1), fill="#334155")


def run_analysis(video, output, config, vlx, *, preview=True, vision_factory=None, evidence_provenance=None):
    import cv2
    # A fresh directory makes retries explicit and preserves previous results.
    output.mkdir(parents=True, exist_ok=False)
    analyzer = InteractionAnalyzer(InteractionConfig(**config["interaction"]))
    capture = writer = None
    started = time.perf_counter()
    processed = 0
    try:
        write_json(output / "status.json", {"status": "reading_source"})
        timeline = probe_video(video)
        with video.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        manifest = {"source_video": str(video), "source_sha256": digest,
                    "timeline": {k: v for k, v in timeline.items() if k != "timestamps"},
                    "config": config, "targets": [asdict(t) for t in TARGETS],
                    "vlx_model_path": vlx["model_path"], "vlx_model_revision": vlx.get("model_revision"),
                    "time_basis": "source_video_pts_seconds", "writes_database": False,
                    "interaction_policy": INTERACTION_POLICY,
                    "evidence_provenance": evidence_provenance or {"mode": "fresh_inference"},
                    "preview_time_basis": "CFR presentation resampling; source PTS shown in overlay"}
        write_json(output / "manifest.json", manifest)
        write_json(output / "status.json", {"status": "loading_models"})
        if vision_factory is None:
            from scripts.vlx_interaction_vision import VideoVision
            vision_factory = VideoVision
        vision = vision_factory(config, vlx)
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise RuntimeError("cannot open video")
        overlay = Overlay(config["font_path"]) if preview else None
        if overlay and manifest["evidence_provenance"]["mode"] != "fresh_inference":
            overlay.evidence_label = " | 检测结果复核"
        next_sample = 0.0
        counts = {t.key: 0 for t in TARGETS}
        with (output / "observations.jsonl").open("w", encoding="utf-8", buffering=1) as trace:
            for index, timestamp in enumerate(timeline["timestamps"]):
                ok, frame = capture.read()
                if not ok:
                    raise RuntimeError(f"decode ended before probed frame {index}")
                vision.advance(frame, index+1, timestamp)
                if timestamp + 1e-9 < next_sample and index != timeline["frame_count"]-1:
                    continue
                objects, people, diagnostics = vision.observe(frame, index+1, timestamp)
                decision = analyzer.process(timestamp, objects, people)
                for obj in objects:
                    counts[obj.key] += 1
                processed += 1
                next_sample = timestamp + 1/config["sample_fps"]
                trace.write(json.dumps({"source_frame": index+1, "timestamp_seconds": timestamp,
                                        "objects": [asdict(o) for o in objects], "people": [asdict(p) for p in people],
                                        "decision": decision, "diagnostics": diagnostics}, ensure_ascii=False, allow_nan=False)+"\n")
                if preview:
                    current_report = analyzer.report()
                    image = overlay.render(frame, timestamp, objects, people, decision,
                                           current_report["total"], report=current_report)
                    if writer is None:
                        writer = PreviewWriter(output, config["preview_fps"], image)
                    writer.push(timestamp, image)
                write_json(output / "status.json", {"status": "analyzing", "processed_samples": processed,
                           "video_seconds": timestamp, "duration_seconds": timeline["duration_seconds"]})
            if capture.read()[0]:
                raise RuntimeError("decoded frame count exceeds probed timeline")
        report = analyzer.finish()
        report.update(status="completed", users=vision.users_report(), processed_samples=processed,
                      evidence_provenance=manifest["evidence_provenance"],
                      duration_seconds=timeline["duration_seconds"], source_sha256=digest,
                      object_observation_counts=counts, processing_seconds=time.perf_counter()-started,
                      qualification="implementation_output_not_accuracy_acceptance",
                      limitations=["2D wrist proximity/co-motion is an estimate of grasp and release",
                                   "occlusion, similar clothes, re-entry and object appearance changes can break association",
                                   "incomplete sessions have no fabricated putdown or completed holding duration"])
        if writer:
            writer.finish(timeline["duration_seconds"])
        write_json(output / "result.json", report)
        with (output / "events.jsonl").open("w", encoding="utf-8") as journal:
            for event in report["events"]:
                journal.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+"\n")
        write_json(output / "status.json", {"status": "completed", "processed_samples": processed,
                                          "result": str(output / "result.json")})
        return report
    except Exception as error:
        if writer:
            writer.abort()
        write_json(output / "partial_result.json", analyzer.report())
        write_json(output / "status.json", {"status": "failed", "error_type": type(error).__name__,
                                          "detail": str(error), "processed_samples": processed})
        raise
    finally:
        if capture:
            capture.release()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "config/vlx_interaction_video.yaml")
    parser.add_argument("--output-dir", type=Path, help="New result directory; existing directories are never overwritten")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--check", action="store_true", help="Check dependencies without loading models or analyzing video")
    args = parser.parse_args(argv)
    config, vlx = load_config(args.config.resolve())
    readiness = preflight(config, vlx)
    if args.check:
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 0
    if args.video is None or not args.video.is_file():
        parser.error("--video must name an existing local recording")
    output = args.output_dir or Path(config["runtime_root"]) / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output = output.resolve()
    print(f"Output: {output}", flush=True)
    result = run_analysis(args.video.resolve(), output, config, vlx, preview=not args.no_preview)
    print(json.dumps(result["total"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
