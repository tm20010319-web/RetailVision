"""Two realtime camera views sharing one VLX model; one winning view per target.

View scores are transparent image/tracking heuristics, not VLX probabilities
and not proof of cross-camera physical-instance identity.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback

import cv2
import numpy as np
from PIL import Image, ImageDraw
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.probe_vlx_seek import load_quantized_worker
from scripts.run_toy_guitar_live import (LatestCamera, atomic_json, camera_password, verify_camera,
                                         make_source, input_color_issue, resolve_path, replace_snapshot)
from scripts.run_toy_guitar_vlx_live import preview_font, NAMES, COLORS
from scripts.vlx_target_controls import TargetSelection, TargetWindow
from scripts.vlx_live_tracking import LiveTracks
from scripts.vlx_dual_support import CachedOfficialProposals, ViewArbiter, view_quality, recognize
from scripts.vlx_camera_recovery import connect_verified_view, retry_missing_cameras


def render_views(views, winners):
    panels = []
    font = preview_font()
    for view in views:
        sample = view['sample'] if view['fresh'] else None
        frame = sample[3] if sample is not None else np.zeros((448, 800, 3), dtype=np.uint8)
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image)
        for winner in winners.values():
            if winner['camera_id'] != view['id']:
                continue
            item = winner['detection']
            x1, y1, x2, y2 = item['bbox_xyxy']
            color = COLORS[item['class_id']]
            draw.rectangle((x1, y1, x2, y2), outline=color, width=2)
            text = f"{item['label']} 视角评分 {winner['quality']['score']:.0f}"
            if item.get('recognition_support') == 'held_after_miss':
                text += ' 跟踪中'
            draw.text((min(x1, max(0, image.width-290)), max(0, y1-24)), text,
                      font=font, fill=color, stroke_width=1, stroke_fill='black')
        image = image.resize((800, 448))
        panel = Image.new('RGB', (800, 512), '#111827')
        panel.paste(image, (0, 64))
        draw = ImageDraw.Draw(panel)
        clock = datetime.fromisoformat(sample[2]).astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S') if sample else '等待新画面'
        draw.text((10, 3), f"{view['name']} | {clock} | {view['fps']:.1f} FPS", font=font, fill='white')
        interval = view.get('interval')
        info = f"VLX 耗时 {view['seconds']:.2f} 秒"
        if interval is not None:
            info += f' | 复核间隔 {interval:.2f} 秒'
        if view.get('ambiguous'):
            info += ' | ' + '、'.join(NAMES[k] for k in view['ambiguous']) + '候选不唯一，暂不采用'
        draw.text((10, 32), info, font=font, fill='#cbd5e1')
        panels.append(panel)
    canvas = Image.new('RGB', (800*len(panels), 548), '#111827')
    for index, panel in enumerate(panels):
        canvas.paste(panel, (index*800, 0))
    text = '  '.join(f"{NAMES[k]} → {winner['camera_name']}" for k, winner in sorted(winners.items())) or '当前暂无可采用的目标框'
    ImageDraw.Draw(canvas).text((10, 518), text+'  | 评分是视角质量，不是识别概率', font=font, fill='white')
    return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'config/toy_guitar_vlx_dual.yaml')
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--target', default='')
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--duration', type=float, default=0)
    parser.add_argument('--api-control', type=Path, help='Authenticated API local control file; implies headless')
    parser.add_argument('--api-state', type=Path, help='Atomic frame/annotation IPC output')
    args = parser.parse_args()
    if args.api_control:
        from scripts.vlx_api_bridge import FileSelection, publish_api_frame
        selection = FileSelection(args.api_control)
        args.headless = True
        if not args.api_state:
            parser.error('--api-control requires --api-state')
    else:
        selection = TargetSelection(args.target)
    config = yaml.safe_load(args.config.read_text(encoding='utf-8'))
    vlx = yaml.safe_load(resolve_path(config['vlx_config']).read_text(encoding='utf-8'))
    runtime = args.runtime_dir or resolve_path(config['runtime_root'])/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    runtime.mkdir(parents=True, exist_ok=True)
    atomic_json(resolve_path(config['runtime_root'])/'current.json', {'runtime': str(runtime.resolve()), 'pid': os.getpid()})
    log = (runtime/'live.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log
    os.environ['HF_HUB_OFFLINE'] = '1'
    sys.path.insert(0, vlx['external_repository'])
    cv2.setNumThreads(2)
    stop, ready = threading.Event(), threading.Event()
    errors, views = [], []
    window, thread, recovery_thread = None, None, None
    started_wall = time.time()
    state = {'status': 'starting', 'pid': os.getpid(), 'engine': 'shared VLX-Seek + cached official WeDetect',
             'custom_yolo_used': False, 'model_path': vlx['model_path'], 'model_revision': vlx['model_revision'],
             'processed_frames': 0, 'cameras': {}, 'mean_confirmation_target_seconds': config['mean_confirmation_target_seconds']}

    def status(update):
        state.update(update, updated_at_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(runtime/'status.json', state)

    def infer():
        try:
            import torch
            torch.set_num_threads(4)
            status({'status': 'loading_vlx'})
            start = time.monotonic()
            worker = load_quantized_worker(vlx['model_path'])
            proposer = CachedOfficialProposals(vlx['external_repository'], vlx['detector_checkpoint'])
            status({'status': 'ready', 'load_seconds': time.monotonic()-start})
            ready.set()
            last_start, last_done, last_sequence, seen_targets = {}, {}, {}, {}
            with (runtime/'results.jsonl').open('a', encoding='utf-8', buffering=1) as journal:
                while not stop.is_set():
                    revision, targets = selection.snapshot()
                    if not targets:
                        if state['status'] != 'waiting_for_target' or state.get('selection_revision') != revision:
                            status({'status': 'waiting_for_target', 'selected_targets': [], 'selection_revision': revision})
                        stop.wait(.05)
                        continue
                    worked = False
                    for view in views:
                        if stop.is_set() or not selection.accepts(revision):
                            break
                        key = view['id']
                        context = selection.context(revision) if args.api_control else None
                        if args.api_control and (context is None or key not in context['camera_ids']):
                            continue
                        if view['source'] is None:
                            continue
                        now = time.monotonic()
                        if now-last_start.get((key, revision), -1e10) < config['mean_confirmation_target_seconds']:
                            continue
                        sample = view['source'].get()
                        if sample is None or now-sample[1] > config['max_input_age_seconds'] or sample[0] == last_sequence.get(key):
                            continue
                        if input_color_issue(sample[3], .5):
                            continue
                        worked = True
                        last_start[(key, revision)] = now
                        last_sequence[key] = sample[0]
                        try:
                            row = recognize(worker, proposer, sample[3], targets, vlx['max_new_tokens'],
                                            target_definitions=context['targets'] if context is not None else None,
                                            retry_targets=seen_targets.get((key,revision),()))
                            row['query_mode'] = 'distinct_instrument_appearance'
                            completed = time.monotonic()
                            if not selection.accepts(revision):
                                continue
                            seen_targets.setdefault((key,revision),set()).update(d['class_id'] for d in row['current_detections'])
                            previous = last_done.get((key, revision))
                            interval = completed-previous if previous is not None else None
                            last_done[(key, revision)] = completed
                            row.update(camera_id=key, camera_name=view['name'], source_frame_sequence=sample[0],
                                       frame_received_at_utc=sample[2], frame_size=[sample[3].shape[1], sample[3].shape[0]],
                                       confirmation_interval_seconds=interval, selection_revision=revision,
                                       selected_targets=list(targets), updated_at_utc=datetime.now(timezone.utc).isoformat())
                            journal.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
                            folder = runtime/key
                            cv2.imwrite(str(folder/'source.tmp.png'), sample[3])
                            replace_snapshot(folder/'source.tmp.png', folder/'source.png')
                            public = {k: v for k, v in row.items() if k != 'raw_output'}
                            atomic_json(folder/'recognition.json', public)
                            state['cameras'][key] = public
                            status({'status': 'running', 'selection_revision': revision, 'selected_targets': list(targets),
                                    'processed_frames': state['processed_frames']+1})
                            try:
                                view['results'].put_nowait((sample, row, revision))
                            except queue.Full:
                                try:
                                    view['results'].get_nowait()
                                except queue.Empty:
                                    pass
                                view['results'].put_nowait((sample, row, revision))
                        except Exception as error:
                            state['cameras'][key] = {'status': 'inference_failed', 'error_type': type(error).__name__}
                            status({})
                            traceback.print_exc()
                    if not worked:
                        stop.wait(.01)
        except Exception as error:
            errors.append(type(error).__name__)
            status({'status': 'failed', 'error_type': type(error).__name__})
            traceback.print_exc()
        finally:
            ready.set()

    try:
        status({})
        for cc in config['cameras']:
            folder = runtime/cc['id']
            folder.mkdir()
            view = {'id': cc['id'], 'name': cc['name'], 'source': None, 'camera_config': cc, 'results': queue.Queue(maxsize=1),
                          'tracker': LiveTracks(config['tracking_history_seconds'], config['tracking_ttl_seconds']),
                          'sample': None, 'fresh': False, 'sequence': 0, 'count': 0, 'times': deque(maxlen=90),
                          'fps': 0., 'seconds': 0., 'interval': None, 'ambiguous': []}
            if not connect_verified_view(view, folder):
                state['cameras'][cc['id']] = {'status': 'identity_preflight_failed', 'error_type': view['preflight_error']}
            views.append(view)
        if not args.api_control and not any(v['source'] is not None for v in views):
            raise RuntimeError('NoVerifiedCamera')
        recovery_thread = threading.Thread(target=retry_missing_cameras,args=(stop,views,runtime),daemon=True,name='camera-preflight-retry')
        recovery_thread.start()
        thread = threading.Thread(target=infer, name='dual-vlx')
        thread.start()
        if not args.headless:
            window = TargetWindow(selection, stop, config['window_width'], args.target)
            window.root.title('VLX 双路监控 — 只在择优一路标注')
        arbiter = ViewArbiter(config['switch_margin'], config['switch_hold_seconds'])
        revision = selection.snapshot()[0]
        ready_at, last_render, last_snapshot, last_api = None, 0., 0., 0.
        while thread.is_alive():
            if window:
                window.pump()
            if stop.is_set():
                break
            now = time.monotonic()
            current_revision, targets = selection.snapshot()
            if current_revision != revision:
                for view in views:
                    view['tracker'].clear()
                    view['sequence'] = 0
                    view['ambiguous'] = []
                    view['checked_generation'] = None
                arbiter.clear()
                revision = current_revision
                last_snapshot = 0
            if ready.is_set() and ready_at is None:
                ready_at = now
            stop_file = runtime/'stop.request'
            if (stop_file.exists() and stop_file.stat().st_mtime >= started_wall) or (
                ready_at is not None and args.duration > 0 and now-ready_at >= args.duration):
                stop.set()
                break
            changed = False
            for view in views:
                sample = view['source'].get() if view['source'] is not None else None
                fresh = sample is not None and now-sample[1] <= config['max_input_age_seconds']
                if fresh and input_color_issue(sample[3], .5):
                    fresh = False
                view['fresh'] = fresh
                view['sample'] = sample
                if not fresh:
                    view['tracker'].clear()
                    continue
                if sample[0] != view['sequence']:
                    view['tracker'].advance(sample[0], sample[1], cv2.cvtColor(sample[3], cv2.COLOR_BGR2GRAY))
                    view['sequence'] = sample[0]
                    view['count'] += 1
                    view['times'].append(now)
                    times = view['times']
                    view['fps'] = (len(times)-1)/(times[-1]-times[0]) if len(times)>1 else 0.
                    changed = True
                try:
                    recognized_sample, row, result_revision = view['results'].get_nowait()
                    if selection.accepts(result_revision):
                        view['tracker'].apply(recognized_sample, row['current_detections'],
                                              candidates=row.get('candidate_detections'),
                                              ambiguous_classes=row['ambiguous_classes'],
                                              confirmed_at_utc=row['updated_at_utc'])
                        view['seconds'] = row['inference_seconds']
                        view['interval'] = row['confirmation_interval_seconds']
                        matched = view['tracker'].last_diagnostics.get('matched_ambiguous', [])
                        view['ambiguous'] = [k for k in row['ambiguous_classes'] if k not in matched]
                        view['checked_generation'] = result_revision
                        view['checked_at_monotonic'] = recognized_sample[1]
                        view['model_frame_id'] = recognized_sample[0]
                        view['confirmed_at'] = row['updated_at_utc']
                        changed = True
                except queue.Empty:
                    pass
            candidates = {key: [] for key in targets}
            for view in views:
                if not view['fresh']:
                    continue
                for detection in view['tracker'].outputs():
                    key = detection['class_id']
                    if key not in targets:
                        continue
                    quality = view_quality(view['sample'][3], detection, now-detection['confirmed_at'],
                                           max_confirmation_age=config['tracking_ttl_seconds'])
                    if quality is not None:
                        candidates[key].append({'camera_id': view['id'], 'camera_name': view['name'],
                                                'detection': detection, 'quality': quality})
            winners = arbiter.choose(candidates, now)
            if args.api_control:
                if now-last_api >= .09:
                    publish_api_frame(args.api_state, selection, revision, views, winners, now)
                    last_api = now
                stop.wait(.005)
                continue
            if changed or now-last_render >= .5:
                composed = render_views(views, winners)
                if window:
                    window.show(composed)
                last_render = now
                if now-last_snapshot >= .5:
                    cv2.imwrite(str(runtime/'latest.tmp.jpg'), composed)
                    replace_snapshot(runtime/'latest.tmp.jpg', runtime/'latest.jpg')
                    atomic_json(runtime/'preview_status.json', {'mode': 'dual_realtime_single_winner_per_target',
                        'selected_targets': list(targets), 'selection_revision': revision,
                        'score_meaning': 'view_quality_not_recognition_probability',
                        'winners': {str(key): winner for key, winner in winners.items()},
                        'cameras': {v['id']: {'fresh': v['fresh'], 'display_frames': v['count'], 'display_fps': v['fps'],
                            'source_frame_sequence': v['sequence'], 'frame_age_seconds': now-v['sample'][1] if v['fresh'] else None,
                            'vlx_seconds': v['seconds'], 'confirmation_interval_seconds': v['interval'],
                            'current_tracks': v['tracker'].outputs(), 'ambiguous_classes': v['ambiguous'],
                            'tracking_reconciliation': v['tracker'].last_diagnostics} for v in views},
                        'updated_at_utc': datetime.now(timezone.utc).isoformat()})
                    last_snapshot = now
            stop.wait(.005)
        stop.set()
        thread.join()
        if errors or not any(v['count'] for v in views):
            status({'status': 'failed', 'error_type': errors[0] if errors else 'NoFreshVideo'})
            return 1
        status({'status': 'stopped'})
        return 0
    except Exception as error:
        status({'status': 'failed', 'error_type': type(error).__name__})
        traceback.print_exc()
        return 1
    finally:
        stop.set()
        if thread:
            thread.join()
        if recovery_thread:
            recovery_thread.join(timeout=15)
        for view in views:
            if view['source'] is not None:
                view['source'].close()
        if window:
            window.destroy()


if __name__ == '__main__':
    raise SystemExit(main())
