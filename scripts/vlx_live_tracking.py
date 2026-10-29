"""CPU optical-flow tracks, including replay of delayed VLX source frames."""
from collections import deque

import cv2
import numpy as np


class FlowTrack:
    def __init__(self, detection, gray, confirmed_at):
        self.detection = detection
        self.confirmed_at = confirmed_at
        x1, y1, x2, y2 = detection['bbox_xyxy']
        self.corners = np.float32([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        mask = np.zeros_like(gray)
        a, b, c, d = map(int, (x1, y1, x2, y2))
        mask[b:d, a:c] = 255
        self.template_size = (max(1, round(x2-x1)), max(1, round(y2-y1)))
        self.template = self.aligned_crop(gray)
        self.points = cv2.goodFeaturesToTrack(gray, 70, .025, 3, mask=mask)
        self.alive = self.points is not None and len(self.points) >= 5 and self.template.size > 0
        self.initial_points = len(self.points) if self.points is not None else 0
        self.correlation = 1.

    def aligned_crop(self, gray):
        width, height = self.template_size
        target = np.float32([[0, 0], [width, 0], [width, height]])
        transform = cv2.getAffineTransform(self.corners[:3].astype(np.float32), target)
        crop = cv2.warpAffine(gray, transform, (width, height))
        return cv2.GaussianBlur(crop, (3, 3), .8)

    def update(self, previous, gray):
        if not self.alive:
            return
        options = dict(winSize=(21, 21), maxLevel=3,
                       criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, .03))
        forward, valid, error = cv2.calcOpticalFlowPyrLK(previous, gray, self.points, None, **options)
        if forward is None:
            self.alive = False
            return
        backward, reverse_valid, _ = cv2.calcOpticalFlowPyrLK(gray, previous, forward, None, **options)
        if backward is None:
            self.alive = False
            return
        good = (valid.ravel() == 1) & (reverse_valid.ravel() == 1)
        good &= np.linalg.norm(backward - self.points, axis=2).ravel() < 1.5
        good &= error.ravel() < 35
        old, new = self.points[good], forward[good]
        if len(new) < 5:
            self.alive = False
            return
        affine, inliers = cv2.estimateAffinePartial2D(old, new, method=cv2.RANSAC, ransacReprojThreshold=2)
        if affine is None or inliers.sum() < 5:
            self.alive = False
            return
        scale = float(np.hypot(affine[0, 0], affine[1, 0]))
        if not .8 < scale < 1.25:
            self.alive = False
            return
        self.corners = cv2.transform(self.corners[None], affine)[0]
        self.points = new[inliers.ravel() == 1].reshape(-1, 1, 2)
        x1, y1 = self.corners.min(axis=0)
        x2, y2 = self.corners.max(axis=0)
        h, w = gray.shape
        if x1 < 0 or y1 < 0 or x2 > w or y2 > h or x2-x1 < 4 or y2-y1 < 4:
            self.alive = False
            return
        # Compare in the original object's orientation; an axis-aligned crop
        # would incorrectly reject an otherwise valid rotating guitar track.
        normalized = self.aligned_crop(gray)
        # Reject a track that has drifted onto background or a newly revealed area.
        correlation = cv2.matchTemplate(normalized, self.template, cv2.TM_CCOEFF_NORMED)[0, 0]
        self.correlation = float(correlation)
        self.alive = bool(np.isfinite(correlation) and correlation >= .35)

    def output(self):
        lo, hi = self.corners.min(axis=0), self.corners.max(axis=0)
        return {**self.detection, 'bbox_xyxy': [float(v) for v in (*lo, *hi)], 'position_source': 'optical_flow',
                'tracking_correlation': self.correlation,
                'tracking_support': len(self.points)/max(1, self.initial_points), 'confirmed_at': self.confirmed_at}


class LiveTracks:
    def __init__(self, history_seconds=8, ttl_seconds=8, max_gap_seconds=.75, track_factory=FlowTrack):
        self.history = deque(maxlen=240)
        self.history_seconds = history_seconds
        self.ttl_seconds = ttl_seconds
        self.max_gap_seconds = max_gap_seconds
        self.tracks = []
        self.applied_sequence = 0
        self.last_diagnostics = {}
        self.track_factory = track_factory

    def advance(self, sequence, received, gray):
        if self.history and sequence <= self.history[-1][0]:
            return
        if self.history:
            _, previous_time, previous_gray = self.history[-1]
            if gray.shape != previous_gray.shape or received - previous_time > self.max_gap_seconds:
                self.tracks = []
            else:
                for track in self.tracks:
                    track.update(previous_gray, gray)
        self.tracks = [t for t in self.tracks if t.alive and received - t.confirmed_at <= self.ttl_seconds]
        self.history.append((sequence, received, gray))
        while self.history and received - self.history[0][1] > self.history_seconds:
            self.history.popleft()

    def apply(self, sample, detections, candidates=None, ambiguous_classes=(), confirmed_at_utc=None):
        """Reconcile a delayed confirmation against currently verified tracks.

        Empty/ambiguous model output does not erase a strong live optical-flow
        track. Its ORIGINAL confirmation age remains bounded by ttl_seconds.
        Ambiguity can renew a track only when exactly one propagated candidate
        matches that target. Never initialize from ambiguous candidates.
        """
        from scripts.probe_vlx_seek import iou
        sequence, received, _, frame = sample
        if sequence <= self.applied_sequence:
            return False
        self.applied_sequence = sequence
        self.last_diagnostics = {}
        if not self.history or received < self.history[0][1] or self.history[-1][1] - received > self.ttl_seconds:
            return False
        now = self.history[-1][1]
        old = {t.detection['class_id']: t for t in self.tracks
               if t.alive and now-t.confirmed_at <= self.ttl_seconds and
               t.correlation >= .65 and len(t.points)/max(1,t.initial_points) >= .5}
        diagnostics = {'matched_ambiguous': [], 'held_after_miss': [], 'rejected_label_conflict': []}
        previous = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        selected_keys = {d['class_id'] for d in detections}
        ambiguous = set(ambiguous_classes)
        seeds = list(detections) + [d for d in (candidates or []) if d['class_id'] in ambiguous]
        tracks = [self.track_factory({**d, 'model_frame_id': sequence,
                             'model_confirmed_at_utc': confirmed_at_utc or sample[2]}, previous, received)
                  for d in seeds]
        previous_time = received
        for seq, when, gray in self.history:
            if seq <= sequence:
                continue
            if when - previous_time > self.max_gap_seconds or gray.shape != previous.shape:
                return False
            for track in tracks:
                track.update(previous, gray)
            previous, previous_time = gray, when
        proposals = {}
        for track in tracks:
            if track.alive:
                proposals.setdefault(track.detection['class_id'], []).append(track)
        adopted = {}
        for key, options in proposals.items():
            if key in ambiguous:
                prior = old.get(key)
                if prior is None:
                    continue
                options = [t for t in options if iou(t.output()['bbox_xyxy'], prior.output()['bbox_xyxy']) >= .5]
                if len(options) != 1:
                    continue
            elif key not in selected_keys:
                continue
            chosen = options[0]
            box = chosen.output()['bbox_xyxy']
            if any(other != key and iou(box, t.output()['bbox_xyxy']) >= .7 for other,t in old.items()):
                diagnostics['rejected_label_conflict'].append(key)
                continue
            chosen.detection['recognition_support'] = 'tracked_candidate_match' if key in ambiguous else 'direct'
            if key in ambiguous:
                diagnostics['matched_ambiguous'].append(key)
            adopted[key] = chosen
        for key, track in old.items():
            if key not in adopted:
                track.detection['recognition_support'] = 'held_after_miss'
                adopted[key] = track
                diagnostics['held_after_miss'].append(key)
        self.tracks = list(adopted.values())
        self.last_diagnostics = diagnostics
        return True

    def outputs(self):
        return [t.output() for t in self.tracks]

    def clear(self):
        self.tracks = []
        self.history.clear()
        self.last_diagnostics = {}
