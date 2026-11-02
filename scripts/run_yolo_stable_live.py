"""Continuously run YOLO stable-track detection against a live video source.

This is an upstream-only smoke runner for the device-api identity path.
It does not poll camera face recognition, finalize visits, or generate profiles.

Usage::

    python scripts/run_yolo_stable_live.py
    python scripts/run_yolo_stable_live.py --source "rtsp://admin:@192.0.2.8:554/stream1"
    python scripts/run_yolo_stable_live.py --source 0 --duration 60
    python scripts/run_yolo_stable_live.py --source "rtsp://..." --log-file outputs/logs/yolo_stable.log
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Optional, TextIO, Union

import cv2

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipelines.legacy.realtime_entry_pipeline import create_realtime_pipeline


DEFAULT_SOURCE = "rtsp://admin:@192.0.2.8:554/stream1"
logger = logging.getLogger("yolo_stable_live")


def parse_source(value: str) -> Union[int, str]:
    """Convert numeric camera indexes to integers and preserve URLs/paths."""
    try:
        return int(value)
    except ValueError:
        return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live YOLO stable-track runner (no face recognition)."
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=(
            "Video source: camera index, file path, or RTSP/HTTP URL "
            f"(default: {DEFAULT_SOURCE})"
        ),
    )
    parser.add_argument(
        "--camera-id",
        default="entrance-1",
        help="Logical camera id written into StableTrackEvent",
    )
    parser.add_argument(
        "--model-path",
        default="data/models/yolov8n.pt",
        help="YOLO weights path",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        help="Inference device, e.g. cpu or cuda:0",
    )
    parser.add_argument(
        "--conf-threshold",
        type=float,
        default=0.5,
        help="YOLO person confidence threshold",
    )
    parser.add_argument(
        "--stability-threshold",
        type=int,
        default=3,
        help="Consecutive tracked frames required before emitting stable event",
    )
    parser.add_argument(
        "--process-every-seconds",
        type=float,
        default=0.3,
        help="Minimum seconds between YOLO process_frame calls",
    )
    parser.add_argument(
        "--status-every-seconds",
        type=float,
        default=5.0,
        help="Heartbeat log interval",
    )
    parser.add_argument(
        "--reconnect-sleep-seconds",
        type=float,
        default=2.0,
        help="Sleep before RTSP reconnect attempts",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="Optional run duration in seconds; 0 means run until Ctrl+C",
    )
    parser.add_argument(
        "--log-file",
        default="",
        help="Optional log file path; logs always go to stdout as well",
    )
    return parser


def setup_logging(log_file: str) -> Optional[TextIO]:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    file_handle: Optional[TextIO] = None
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handle = path.open("a", encoding="utf-8")
        handlers.append(logging.StreamHandler(file_handle))

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )
    return file_handle


def open_capture(source: Union[int, str]):
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        return None, None
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None, None
    return cap, frame


def run(args: argparse.Namespace) -> int:
    source = parse_source(args.source)
    stop = {"requested": False}

    def request_stop(_signum=None, _frame=None) -> None:
        stop["requested"] = True
        logger.info("stop requested")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    stable_count = 0
    frames_seen = 0
    frames_with_person = 0
    last_status = 0.0
    started = time.time()

    def on_stable(event) -> None:
        nonlocal stable_count
        stable_count += 1
        logger.info(
            "STABLE #%d track_id=%s conf=%.3f bbox=%s camera=%s confirmed_at=%s",
            stable_count,
            event.track_id,
            event.detection_confidence,
            event.bbox,
            event.camera_id,
            event.confirmed_at,
        )

    logger.info("starting YOLO stable-track live runner")
    logger.info("source=%s", source)
    logger.info(
        "config camera_id=%s stability=%s process_every=%.3fs conf=%.2f device=%s",
        args.camera_id,
        args.stability_threshold,
        args.process_every_seconds,
        args.conf_threshold,
        args.device,
    )

    pipeline = create_realtime_pipeline(
        model_path=args.model_path,
        device=args.device,
        conf_threshold=args.conf_threshold,
        face_recognizer=None,
        stability_threshold=args.stability_threshold,
        process_every_seconds=args.process_every_seconds,
        camera_id=args.camera_id,
        identity_mode="device_api",
        on_stable_track=on_stable,
    )
    logger.info("pipeline ready")

    cap = None
    try:
        while not stop["requested"]:
            if args.duration > 0 and (time.time() - started) >= args.duration:
                logger.info("duration reached (%.1fs)", args.duration)
                break

            if cap is None:
                logger.info("connecting video source ...")
                cap, first_frame = open_capture(source)
                if cap is None:
                    logger.warning(
                        "open/read failed, retry in %.1fs",
                        args.reconnect_sleep_seconds,
                    )
                    time.sleep(args.reconnect_sleep_seconds)
                    continue
                logger.info("connected, frame_shape=%s", first_frame.shape)

            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("frame read failed, reconnecting")
                cap.release()
                cap = None
                time.sleep(args.reconnect_sleep_seconds)
                continue

            frames_seen += 1
            try:
                result = pipeline.process_frame(frame)
            except Exception:
                logger.error("process_frame failed:\n%s", traceback.format_exc())
                time.sleep(0.2)
                continue

            dets = result.get("detections") or []
            if dets:
                frames_with_person += 1

            now = time.time()
            if now - last_status >= args.status_every_seconds:
                last_status = now
                logger.info(
                    "STATUS elapsed=%.0fs frames=%d person_frames=%d "
                    "active_tracks=%s dets=%d stable_total=%d",
                    now - started,
                    frames_seen,
                    frames_with_person,
                    result.get("active_tracks"),
                    len(dets),
                    stable_count,
                )
    finally:
        if cap is not None:
            cap.release()
        logger.info(
            "exit summary frames=%d person_frames=%d stable_total=%d elapsed=%.1fs",
            frames_seen,
            frames_with_person,
            stable_count,
            time.time() - started,
        )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    file_handle = setup_logging(args.log_file)
    try:
        return run(args)
    finally:
        if file_handle is not None:
            file_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
