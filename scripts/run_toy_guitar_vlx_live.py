"""Repeated full-frame WeDetect -> VLX recognition, without custom YOLO or SAM.

Live video and CPU tracking run independently of VLX recognition. Delayed
VLX results are propagated through buffered frames before updating live boxes.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_vlx_seek import load_quantized_worker, iou
from scripts.vlx_live_tracking import LiveTracks
from scripts.vlx_target_controls import TargetSelection, TargetWindow, query_for
from scripts.vlx_dual_support import distinct_targets
from scripts.run_toy_guitar_live import (
    LatestCamera, atomic_json, camera_password, input_color_issue, make_source,
    replace_snapshot, resolve_path, verify_camera,
)

TITLE = 'VLX-Seek | Bee & Guitars | Esc to close'
NAMES = {0: '蜜蜂玩偶', 1: '大吉他', 2: '小吉他'}
COLORS = {0: '#FFD54A', 1: '#48D7FF', 2: '#FF99DD'}
ALIASES = {'bee plush toy': 0, 'yellow bee plush toy': 0, 'plush toy': 0,
           'white electric guitar': 1, 'white double-cutaway electric guitar': 1,
           'white ukulele': 2, 'white ukulele with a round sound hole': 2}


def detections_from_output(output, image_size, aliases=None, names=None):
    """Allow independently absent targets; reject invalid/unrequested regions."""
    width, height = image_size
    aliases = ALIASES if aliases is None else aliases
    names = NAMES if names is None else names
    detections = []
    for item in output['result_bbox_list']:
        key = aliases.get(item['label'].strip().casefold())
        if key is None:
            continue
        box = [float(item[k]) for k in ('xmin', 'ymin', 'xmax', 'ymax')]
        if not all(math.isfinite(v) for v in box):
            raise ValueError('Non-finite VLX region')
        if not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
            raise ValueError('VLX region outside source frame')
        if any(d['class_id'] == key and iou(box, d['bbox_xyxy']) > .9 for d in detections):
            continue
        # Seek selects regions, but does not supply calibrated detector scores.
        detections.append({'class_id': key, 'label': names[key], 'bbox_xyxy': box})
    return detections


@lru_cache(maxsize=1)
def preview_font():
    return ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 17)


def render(frame, detections, elapsed, sampled_at, live=False, fps=0):
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(image)
    font = preview_font()
    for item in detections:
        x1, y1, x2, y2 = item['bbox_xyxy']
        draw.rectangle((x1, y1, x2, y2), outline=COLORS[item['class_id']], width=2)
        draw.text((x1, max(0, y1 - 24)), item['label'], font=font, fill=COLORS[item['class_id']], stroke_width=1, stroke_fill='black')
    canvas = Image.new('RGB', (image.width, image.height + 64), '#111827')
    canvas.paste(image, (0, 64))
    draw = ImageDraw.Draw(canvas)
    local_time = datetime.fromisoformat(sampled_at).astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    heading = (f'实时画面 {local_time} | {fps:.1f} FPS | VLX 最近耗时 {elapsed:.2f} 秒' if live else
               f'VLX 持续识别 | 采样 {local_time} | 识别耗时 {elapsed:.2f} 秒')
    draw.text((10, 4), heading, font=font, fill='white')
    counts = {k: sum(d['class_id'] == k for d in detections) for k in NAMES}
    draw.text((10, 33), '  '.join(f'{NAMES[k]}：{counts[k]}' for k in NAMES) +
              (' | 实时跟踪 / VLX 定期复核 | Esc 退出' if live else ''), font=font, fill='#cbd5e1')
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/toy_guitar_vlx_live.yaml')
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--duration', type=float, default=0)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--images', type=Path, nargs='+', help='Finite file replay instead of camera')
    parser.add_argument('--target', default='', help='Initial target text; default empty means no annotations')
    args = parser.parse_args()
    selection = TargetSelection(args.target)
    if args.images and not selection.snapshot()[1]:
        parser.error('--images requires --target (e.g. 小蜜蜂 or 白色电吉他)')
    cv2.setNumThreads(2)
    config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
    vlx = yaml.safe_load(resolve_path(config['vlx_config']).read_text(encoding='utf-8'))
    runtime = args.runtime_dir or resolve_path(config['runtime_root']) / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_json(resolve_path(config['runtime_root']) / 'current.json', {'runtime': str(runtime.resolve()), 'pid': os.getpid()})
    log = (runtime / 'live.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    os.environ['HF_HUB_OFFLINE'] = '1'
    sys.path.insert(0, vlx['external_repository'])
    state = {'status': 'starting', 'pid': os.getpid(), 'engine': 'VLX-Seek + official WeDetect proposals',
             'custom_yolo_used': False, 'sam_used': False, 'processed_frames': 0,
             'model_path': vlx['model_path'], 'model_revision': vlx['model_revision'],
             'detector_checkpoint': vlx['detector_checkpoint'], 'query': query_for(selection.snapshot()[1]),
             'precision': vlx['precision']}
    stop = threading.Event()
    ready = threading.Event()
    results = queue.Queue(maxsize=1)
    errors = []
    camera = None
    worker_thread = None
    window = None
    started_wall = time.time()

    def status(value):
        state.update(value, updated_at_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(runtime / 'status.json', state)

    def infer():
        try:
            import torch
            from inference import load_wedetect_proposals
            status({'status': 'loading_vlx'})
            start = time.monotonic()
            worker = load_quantized_worker(vlx['model_path'])
            status({'status': 'waiting_for_fresh_frame', 'load_seconds': time.monotonic() - start})
            ready.set()
            last_sequence = 0
            file_index = 0
            with (runtime / 'results.jsonl').open('a', encoding='utf-8', buffering=1) as journal:
                while not stop.is_set():
                    revision, targets = selection.snapshot()
                    if not targets:
                        if state['status'] != 'waiting_for_target' or state.get('selection_revision') != revision:
                            status({'status': 'waiting_for_target', 'current_detections': [], 'query': '',
                                    'selected_targets': [], 'selection_revision': revision})
                        stop.wait(.05)
                        continue
                    query = query_for(targets)
                    if args.images:
                        if file_index >= len(args.images):
                            break
                        path = args.images[file_index]
                        frame = cv2.imread(str(path))
                        if frame is None:
                            raise ValueError('Cannot read replay image')
                        file_index += 1
                        sample = (file_index, time.monotonic(), datetime.now(timezone.utc).isoformat(), frame)
                    else:
                        sample = camera.get()
                    if sample is None or time.monotonic() - sample[1] > config['max_input_age_seconds']:
                        if state['status'] != 'waiting_for_fresh_frame':
                            status({'status': 'waiting_for_fresh_frame', 'current_detections': []})
                        stop.wait(.05)
                        continue
                    if sample[0] == last_sequence:
                        stop.wait(.05)
                        continue
                    sequence, received, utc, frame = sample
                    last_sequence = sequence
                    if input_color_issue(frame, .5):
                        status({'status': 'invalid_camera_input', 'current_detections': []})
                        stop.wait(.1)
                        continue
                    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    start = time.monotonic()
                    proposals = load_wedetect_proposals(image, vlx['detector_checkpoint'], device='cuda:0')
                    proposal_seconds = time.monotonic() - start
                    with torch.inference_mode():
                        output = worker.run_task(image, 'detection', query, lang='en', bbox_list=proposals,
                                                 temperature=0., max_new_tokens=vlx['max_new_tokens'])
                        detections = [d for d in detections_from_output(output, image.size) if d['class_id'] in targets]
                    detections, ambiguous = distinct_targets(detections)
                    torch.cuda.synchronize()
                    if not selection.accepts(revision):
                        continue
                    elapsed = time.monotonic() - start
                    row = {'source_frame_sequence': sequence, 'frame_received_at_utc': utc,
                           'frame_size': list(image.size), 'inference_seconds': elapsed,
                           'proposal_seconds': proposal_seconds, 'proposal_count': len(proposals),
                           'current_detections': detections, 'raw_output': output, 'ambiguous_classes': ambiguous,
                           'query': query, 'selection_revision': revision, 'selected_targets': list(targets)}
                    if args.images:
                        row['source_image'] = str(path.resolve())
                    journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
                    preview = render(frame, detections, elapsed, utc)
                    if args.images:
                        cv2.imwrite(str(runtime / f'replay_{file_index:02d}.jpg'), preview)
                    if time.monotonic() - received > config['max_result_age_seconds']:
                        status({'status': 'result_expired', 'current_detections': []})
                        continue
                    cv2.imwrite(str(runtime / 'source.tmp.png'), frame)
                    replace_snapshot(runtime / 'source.tmp.png', runtime / 'source.png')
                    cv2.imwrite(str(runtime / 'recognition.tmp.jpg'), preview)
                    replace_snapshot(runtime / 'recognition.tmp.jpg', runtime / 'recognition.jpg')
                    status({**row, 'raw_output': None, 'status': 'running', 'processed_frames': state['processed_frames'] + 1,
                            'frame_age_seconds': time.monotonic() - received})
                    if results.full():
                        try:
                            results.get_nowait()
                        except queue.Empty:
                            pass
                    results.put_nowait((sample, detections, elapsed, revision))
        except Exception as error:
            errors.append(type(error).__name__)
            # Camera URLs/credentials are never included in errors or status.
            print('VLX worker failed:', type(error).__name__, flush=True)
            traceback.print_exc()
        finally:
            ready.set()

    try:
        status({})
        if not args.images:
            camera_config = yaml.safe_load(resolve_path(config['camera_config']).read_text(encoding='utf-8'))['camera']
            password = camera_password(camera_config)
            atomic_json(runtime / 'camera_identity.json', verify_camera(camera_config, password))
            camera = LatestCamera(make_source(camera_config, password))
            camera.thread.start()
        worker_thread = threading.Thread(target=infer, name='vlx-recognition')
        worker_thread.start()
        if not args.headless:
            window = TargetWindow(selection, stop, config['window_width'], args.target)
        blank = np.zeros((512, 800, 3), dtype=np.uint8)
        cv2.putText(blank, 'VLX loading / waiting for recognition...', (20, 100), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 1)
        displayed = None
        ready_at = None
        tracking = LiveTracks(history_seconds=config.get('tracking_history_seconds', 8),
                              ttl_seconds=config.get('tracking_ttl_seconds', 8))
        display_sequence = 0
        display_times = deque(maxlen=90)
        display_count = 0
        last_snapshot = 0
        recognition_elapsed = 0
        display_revision = selection.snapshot()[0]
        while worker_thread.is_alive():
            if window is not None:
                window.pump()
            if stop.is_set():
                break
            revision, targets = selection.snapshot()
            if revision != display_revision:
                tracking.clear()
                display_revision = revision
                display_sequence = 0
                last_snapshot = 0
                displayed = None
                recognition_elapsed = 0
            if ready.is_set() and ready_at is None:
                ready_at = time.monotonic()
            stop_file = runtime / 'stop.request'
            if (stop_file.exists() and stop_file.stat().st_mtime >= started_wall) or (
                ready_at is not None and args.duration > 0 and time.monotonic() - ready_at >= args.duration
            ):
                stop.set()
                break
            if not args.images:
                sample = camera.get()
                if sample is not None and time.monotonic() - sample[1] <= config['max_input_age_seconds']:
                    sequence, received, utc, frame = sample
                    changed = sequence != display_sequence
                    if changed:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        if input_color_issue(frame, .5):
                            tracking.clear()
                        else:
                            tracking.advance(sequence, received, gray)
                    try:
                        recognized_sample, detections, elapsed, result_revision = results.get_nowait()
                        if selection.accepts(result_revision):
                            recognition_elapsed = elapsed
                            tracking.apply(recognized_sample, [d for d in detections if d['class_id'] in targets])
                    except queue.Empty:
                        pass
                    if changed:
                        now = time.monotonic()
                        display_times.append(now)
                        fps = (len(display_times)-1)/(now-display_times[0]) if len(display_times)>1 else 0
                        boxes = [d for d in tracking.outputs() if d['class_id'] in targets]
                        displayed = (received, render(frame, boxes, recognition_elapsed, utc, live=True, fps=fps))
                        display_sequence = sequence
                        display_count += 1
                        if now - last_snapshot >= .5:
                            cv2.imwrite(str(runtime / 'latest.tmp.jpg'), displayed[1])
                            replace_snapshot(runtime / 'latest.tmp.jpg', runtime / 'latest.jpg')
                            atomic_json(runtime / 'preview_status.json', {
                                'mode': 'live_video_optical_flow_vlx_confirmation', 'display_frames': display_count,
                                'display_fps': fps, 'frame_age_seconds': now-received,
                                'source_frame_sequence': sequence, 'frame_received_at_utc': utc,
                                'vlx_source_sequence': tracking.applied_sequence, 'vlx_seconds': recognition_elapsed,
                                'selected_targets': list(targets), 'selection_revision': revision,
                                'current_tracks': boxes, 'updated_at_utc': datetime.now(timezone.utc).isoformat()})
                            last_snapshot = now
                else:
                    tracking.clear()
                    displayed = None
            else:
                try:
                    sample, detections, elapsed, result_revision = results.get_nowait()
                    if selection.accepts(result_revision):
                        displayed = (sample[1], render(sample[3], detections, elapsed, sample[2]))
                except queue.Empty:
                    pass
            if not args.headless:
                fresh = displayed is not None and time.monotonic() - displayed[0] <= config['max_result_age_seconds']
                window.show(displayed[1] if fresh else blank)
            stop.wait(.005)
        stop.set()
        worker_thread.join()
        if errors or (state['processed_frames'] == 0 and (args.images or display_count == 0)):
            status({'status': 'failed', 'error_type': errors[0] if errors else 'NoFreshInference'})
            return 1
        status({'status': 'stopped'})
        return 0
    except Exception as error:
        status({'status': 'failed', 'error_type': type(error).__name__})
        return 1
    finally:
        stop.set()
        if worker_thread is not None:
            worker_thread.join()
        if camera is not None:
            camera.close()
        if not args.headless:
            if window is not None:
                window.destroy()


if __name__ == '__main__':
    raise SystemExit(main())
