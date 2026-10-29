"""Cached official proposals and transparent (non-probabilistic) view ranking."""
import importlib
import math
from pathlib import Path
import sys
import time

import cv2
import numpy as np


class CachedOfficialProposals:
    """Retain the official WeDetect model instead of reloading it per frame.

    Constructor, checkpoint remapping and top-100 selection mirror the pinned
    VLX-Seek inference.load_wedetect_proposals implementation. Vendor files and
    weights are unchanged; only model lifetime differs.
    """
    def __init__(self, repository, checkpoint):
        import torch
        sys.path.insert(0, str(Path(repository) / 'detect_tools' / 'WeDetect'))
        detector = importlib.import_module('generate_proposal').SimpleYOLOWorldDetector
        self.model = detector(backbone_size='base', prompt_dim=768, num_prompts=256, num_proposals=100)
        state = torch.load(checkpoint, map_location='cpu')
        state = state.get('state_dict', state)
        for key in list(state):
            if 'backbone' in key:
                state[key.replace('backbone.image_model.model.', 'backbone.')] = state.pop(key)
        for key in list(state):
            if 'bbox_head' in key:
                name = key.replace('bbox_head.head_module.', 'bbox_head.')
                for old, new in [('0.2.', '0.6.'), ('1.2.', '1.6.'), ('2.2.', '2.6.'),
                                 ('1.bn', '4'), ('1.conv', '3'), ('0.bn', '1'), ('0.conv', '0')]:
                    name = name.replace(old, new)
                state[name] = state.pop(key)
        self.model.load_state_dict(state, strict=False)
        self.model = self.model.to('cuda:0').eval()

    def __call__(self, image):
        import torch
        with torch.inference_mode():
            return self.model([image])[0]['bboxes'].float().cpu().tolist()


def dual_query(targets):
    from scripts.vlx_target_controls import query_for
    return query_for(targets)


def recognize(worker, proposer, frame, targets, max_tokens=128, target_definitions=None, retry_targets=()):
    import torch
    from PIL import Image
    from scripts.run_toy_guitar_vlx_live import detections_from_output
    from scripts.vlx_target_controls import query_for
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    start = time.monotonic()
    proposals = proposer(image)
    proposal_seconds = time.monotonic() - start
    query = query_for(targets) if target_definitions is None else '; '.join(t['phrase'] for t in target_definitions)
    aliases = None if target_definitions is None else {t['phrase'].strip().casefold(): t['class_id'] for t in target_definitions}
    names = None if target_definitions is None else {t['class_id']: t['label'] for t in target_definitions}
    lang = 'zh' if any('\u4e00' <= c <= '\u9fff' for c in query) else 'en'
    with torch.inference_mode():
        output = worker.run_task(image, 'detection', query, lang=lang, bbox_list=proposals,
                                 temperature=0., max_new_tokens=max_tokens)
        detections = [d for d in detections_from_output(output, image.size, aliases, names) if d['class_id'] in targets]
        rechecked, retry_output = [], None
        missing = sorted((set(retry_targets) & set(targets)) - {d['class_id'] for d in detections})
        if missing:
            key = missing[0]
            retry_query = query_for((key,)) if target_definitions is None else next(t['phrase'] for t in target_definitions if t['class_id']==key)
            retry_lang = 'zh' if any('\u4e00' <= c <= '\u9fff' for c in retry_query) else 'en'
            retry_output = worker.run_task(image,'detection',retry_query,lang=retry_lang,bbox_list=proposals,
                                          temperature=0.,max_new_tokens=max_tokens)
            detections.extend(d for d in detections_from_output(retry_output,image.size,aliases,names) if d['class_id']==key)
            rechecked.append(key)
    selected, ambiguous = distinct_targets(detections)
    torch.cuda.synchronize()
    return {'inference_seconds': time.monotonic()-start, 'proposal_seconds': proposal_seconds,
            'proposal_count': len(proposals), 'current_detections': selected, 'ambiguous_classes': ambiguous,
            'candidate_detections': detections, 'raw_output': output, 'query': query,
            'rechecked_classes': rechecked, 'retry_output': retry_output}


def unique_targets(detections):
    """Suppress a camera's ambiguous class instead of selecting a different item.

    This detects multiple disjoint semantic regions, not physical-instance
    identity. Cross-camera instance identity is not established by this helper.
    """
    from scripts.probe_vlx_seek import iou
    def same_region(a, b):
        if iou(a, b) >= .7:
            return True
        area_a = (a[2]-a[0])*(a[3]-a[1])
        area_b = (b[2]-b[0])*(b[3]-b[1])
        smaller, larger = min(area_a, area_b), max(area_a, area_b)
        intersection = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
        # Body/whole-object proposals need containment, not just IoU. Reject
        # extreme area ratios so a large box cannot absorb unrelated objects.
        return smaller > 0 and smaller/larger >= .25 and intersection/smaller >= .8

    selected, ambiguous = [], []
    for key in sorted({d['class_id'] for d in detections}):
        rows = sorted((d for d in detections if d['class_id'] == key),
                      key=lambda d: (d['bbox_xyxy'][2]-d['bbox_xyxy'][0])*(d['bbox_xyxy'][3]-d['bbox_xyxy'][1]), reverse=True)
        anchor = rows[0]['bbox_xyxy']
        if any(not same_region(anchor, d['bbox_xyxy']) for d in rows[1:]):
            ambiguous.append(key)
        else:
            merged = [min(d['bbox_xyxy'][0] for d in rows), min(d['bbox_xyxy'][1] for d in rows),
                      max(d['bbox_xyxy'][2] for d in rows), max(d['bbox_xyxy'][3] for d in rows)]
            selected.append({**rows[0], 'bbox_xyxy': merged, 'merged_proposals': len(rows)})
    return selected, ambiguous


def distinct_targets(detections):
    """Keep guitar identities separate; reject essentially the same region
    labeled as both instruments. Never assign identity by box size or camera.
    """
    from scripts.probe_vlx_seek import iou
    selected, ambiguous = unique_targets(detections)
    guitars = {d['class_id']: d for d in selected if d['class_id'] in (1, 2)}
    if len(guitars) == 2 and iou(guitars[1]['bbox_xyxy'], guitars[2]['bbox_xyxy']) >= .7:
        selected = [d for d in selected if d['class_id'] not in (1, 2)]
        ambiguous = sorted(set(ambiguous) | {1, 2})
    return selected, ambiguous


def view_quality(frame, detection, confirmation_age, max_confirmation_age=8):
    """A 0..100 view-quality index; NOT recognition probability or confidence."""
    x1, y1, x2, y2 = detection['bbox_xyxy']
    h, w = frame.shape[:2]
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2, confirmation_age)):
        return None
    if not math.isfinite(max_confirmation_age) or max_confirmation_age <= 0:
        return None
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h) or confirmation_age < 0 or confirmation_age > max_confirmation_age:
        return None
    crop = frame[int(y1):math.ceil(y2), int(x1):math.ceil(x2)]
    if crop.size == 0 or min(crop.shape[:2]) < 4:
        return None
    # Pixel support reflects available detail; normalize sharpness to crop size
    # so a high-resolution stream does not get a second automatic size bonus.
    area = (x2-x1)*(y2-y1)
    detail = min(1., math.log1p(area) / math.log1p(10000))
    gray = cv2.cvtColor(cv2.resize(crop, (64, 64)), cv2.COLOR_BGR2GRAY)
    variance = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    sharpness = variance / (variance + 200.)
    appearance = max(0., min(1., float(detection.get('tracking_correlation', 1.))))
    support = max(0., min(1., float(detection.get('tracking_support', 1.))))
    freshness = max(0., 1-confirmation_age/max_confirmation_age)
    score = 100*(.30*detail + .25*sharpness + .20*appearance + .10*support + .15*freshness)
    return {'score': round(score, 2), 'detail': detail, 'sharpness': sharpness,
            'tracking_appearance': appearance, 'tracking_support': support, 'confirmation_age': confirmation_age,
            'meaning': 'view_quality_not_recognition_probability'}


class ViewArbiter:
    def __init__(self, switch_margin=8., hold_seconds=.8):
        self.margin = switch_margin
        self.hold = hold_seconds
        self.winners = {}
        self.pending = {}

    def choose(self, candidates, now):
        """One camera per target; missing/expired candidates can never win."""
        output = {}
        for key, options in candidates.items():
            if not options:
                continue
            best = max(options, key=lambda c: (c['quality']['score'], c['camera_id']))
            old = next((c for c in options if c['camera_id'] == self.winners.get(key)), None)
            if old is not None and best['camera_id'] != old['camera_id']:
                if best['quality']['score'] <= old['quality']['score'] + self.margin:
                    best = old
                    self.pending.pop(key, None)
                else:
                    challenger, since = self.pending.get(key, (None, now))
                    if challenger != best['camera_id']:
                        since = now
                        self.pending[key] = (best['camera_id'], since)
                    if now - since < self.hold:
                        best = old
            else:
                self.pending.pop(key, None)
            output[key] = best
        self.winners = {key: c['camera_id'] for key, c in output.items()}
        self.pending = {key: value for key, value in self.pending.items() if key in output}
        return output

    def clear(self):
        self.winners.clear()
        self.pending.clear()
