"""Local file IPC between the authenticated API and the existing GPU preview."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

import cv2

from scripts.run_toy_guitar_live import atomic_json


class FileSelection:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.value = {'generation': 0, 'targets': [], 'camera_ids': []}

    def snapshot(self):
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if value['generation'] >= self.value['generation']:
                    self.value = value
            except (OSError, ValueError, KeyError):
                # A missing/corrupt control fails closed, never retains boxes.
                self.value = {**self.value, 'targets': []}
            return self.value['generation'], tuple(t['class_id'] for t in self.value['targets'])

    def accepts(self, revision):
        current, targets = self.snapshot()
        return current == revision and bool(targets)

    def context(self, revision):
        self.snapshot()
        with self.lock:
            return dict(self.value) if self.value['generation'] == revision else None


def publish_api_frame(path, selection, revision, views, winners, now):
    context = selection.context(revision)
    if context is None:
        return
    targets = {t['class_id']: t for t in context['targets']}
    frames, cameras = {}, []
    for view in views:
        sample = view['sample']
        camera = {'camera_id': view['id'], 'name': view['name'],
                  'status': 'online' if view['fresh'] else ('stale' if sample else 'offline' if view.get('preflight_error') else 'connecting'),
                  'checked_generation': view.get('checked_generation'),
                  'checked_at_monotonic': view.get('checked_at_monotonic', 0),
                  'ambiguous_classes': view.get('ambiguous', [])}
        if sample:
            camera.update(width=sample[3].shape[1], height=sample[3].shape[0])
        cameras.append(camera)
        if not view['fresh'] or sample is None:
            continue
        height, width = sample[3].shape[:2]
        rows = []
        for key, winner in winners.items():
            if key not in targets or winner['camera_id'] != view['id']:
                continue
            target, detection = targets[key], winner['detection']
            box = detection['bbox_xyxy']
            rows.append({k: target[k] for k in ('target_id', 'label', 'validation_status', 'color')}
                        | {'bbox': [box[0]/width, box[1]/height, box[2]/width, box[3]/height],
                           'view_score': winner['quality']['score'], 'position_source': 'optical_flow',
                           'model_frame_id': detection.get('model_frame_id', view.get('model_frame_id', sample[0])),
                           'confirmed_at': detection.get('model_confirmed_at_utc', view.get('confirmed_at', sample[2]))})
        ok, encoded = cv2.imencode('.jpg', sample[3], [cv2.IMWRITE_JPEG_QUALITY, 75])
        if not ok:
            continue
        metrics = {'preview_fps': round(view['fps'], 2), 'inference_ms': round(view['seconds']*1000, 2)}
        if view.get('interval') is not None:
            metrics['confirmation_interval_ms'] = round(view['interval']*1000, 2)
        frames[view['id']] = {'camera_id': view['id'], 'camera_name': view['name'], 'camera_status': 'online',
            'frame_id': sample[0], 'frame_received_at': sample[2], 'received_monotonic': sample[1],
            'width': width, 'height': height, 'mime_type': 'image/jpeg',
            'jpeg_base64': base64.b64encode(encoded).decode('ascii'), 'annotations': rows, 'metrics': metrics}
    atomic_json(Path(path), {'generation': revision, 'session_id': context.get('session_id'),
        'revision': context.get('revision', 0), 'published_monotonic': now,
        'updated_at': datetime.now(timezone.utc).isoformat(), 'cameras': cameras, 'frames': frames})
