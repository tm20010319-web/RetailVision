"""Source-pixel tracking for the uploaded-video experiment only."""
import cv2
import numpy as np
from scripts.vlx_live_tracking import FlowTrack, LiveTracks


class LocalFlowTrack(FlowTrack):
    """Bound KLT work to a padded source-pixel neighborhood of the object.

    All points, affine fitting, template verification and output remain in
    source coordinates. No extrapolated or held-last-position box is emitted.
    Live-camera callers keep their existing tracker by default.
    """
    def __init__(self, detection, gray, confirmed_at, *, use_csrt=False):
        super().__init__(detection, gray, confirmed_at)
        self.use_csrt = use_csrt
        self.appearance_tracker = None
        self.source_frames = None

    def update(self, previous, gray):
        if not self.alive:
            return
        if self.appearance_tracker is not None:
            self._appearance_update()
            return
        prior_box = self.output()["bbox_xyxy"]
        low, high = self.corners.min(axis=0), self.corners.max(axis=0)
        margin = max(64, float(max(high-low)))
        height, width = gray.shape
        x1, y1 = np.maximum(0, np.floor(low-margin)).astype(int)
        x2, y2 = np.minimum((width, height), np.ceil(high+margin)).astype(int)
        offset = np.float32((x1, y1))
        self.points -= offset
        self.corners -= offset
        try:
            super().update(previous[y1:y2, x1:x2], gray[y1:y2, x1:x2])
        finally:
            self.points += offset
            self.corners += offset
        if not self.alive and self.use_csrt and self.source_frames is not None:
            previous_color, _ = self.source_frames
            self.start_appearance(previous_color, prior_box)
            self._appearance_update()

    def start_appearance(self, frame, box=None):
        if not self.use_csrt or self.appearance_tracker is not None:
            return
        x1, y1, x2, y2 = map(round, box or self.output()["bbox_xyxy"])
        self.appearance_tracker = cv2.TrackerCSRT_create()
        self.appearance_tracker.init(frame, (x1, y1, x2-x1, y2-y1))

    def _appearance_update(self):
        if self.source_frames is None:
            self.alive = False
            return
        _, frame = self.source_frames
        valid, (x, y, width, height) = self.appearance_tracker.update(frame)
        h, w = frame.shape[:2]
        self.alive = bool(valid and width > 4 and height > 4 and x >= 0 and y >= 0
                          and x+width <= w and y+height <= h)
        if self.alive:
            self.corners = np.float32([[x, y], [x+width, y], [x+width, y+height], [x, y+height]])

    def output(self):
        result = super().output()
        if self.appearance_tracker is not None:
            result.update(position_source="csrt", appearance_verified=True)
        return result


class InteractionTracks(LiveTracks):
    def __init__(self, *, ttl_seconds, use_csrt=False, stable_radius=.008):
        if use_csrt and not hasattr(cv2, "TrackerCSRT_create"):
            raise RuntimeError("CSRT requires opencv-contrib-python; use the VLX interaction environment")
        super().__init__(history_seconds=2, ttl_seconds=ttl_seconds,
                         track_factory=lambda d, g, t: LocalFlowTrack(d, g, t, use_csrt=use_csrt))
        self.previous_color = None
        self.last_selected = {}
        self.stable_radius = stable_radius

    def advance(self, sequence, timestamp, gray, frame=None):
        for track in self.tracks:
            track.source_frames = (self.previous_color, frame) if self.previous_color is not None and frame is not None else None
        super().advance(sequence, timestamp, gray)
        self.previous_color = frame

    def apply(self, *args, **kwargs):
        # Retain only a currently verified CSRT track through a missed model
        # frame; never renew its original model-confirmation timestamp.
        retained = [t for t in self.tracks if t.alive and t.appearance_tracker is not None]
        result = super().apply(*args, **kwargs)
        if result:
            from scripts.probe_vlx_seek import iou
            sample, detections = args[:2]
            ambiguous = set(kwargs.get('ambiguous_classes', ()))
            selected = {d['class_id']: d for d in detections if d['class_id'] not in ambiguous}
            height, width = sample[3].shape[:2]
            for prior in retained:
                key = prior.detection['class_id']
                detection = selected.get(key)
                previous_detection = self.last_selected.get(key)
                stable_model = (detection is not None and previous_detection is not None
                                and max(abs(a-b)/s for a, b, s in zip(detection['bbox_xyxy'], previous_detection,
                                                                     (width, height, width, height))) <= self.stable_radius)
                if detection is not None and not stable_model and iou(prior.output()['bbox_xyxy'], detection['bbox_xyxy']) >= .3:
                    # Corroborate the existing appearance model, rather than
                    # replacing it with a blurry/partial crop during a toss.
                    prior.confirmed_at = sample[1]
                    prior.detection.update(detection, model_frame_id=sample[0])
                    self.tracks = [t for t in self.tracks if t.detection['class_id'] != key]
                    self.tracks.append(prior)
            self.last_selected = {k: d['bbox_xyxy'] for k, d in selected.items()}
            adopted = {t.detection['class_id'] for t in self.tracks}
            self.tracks.extend(t for t in retained if t.detection['class_id'] not in adopted)
        return result

    def arm_near_people(self, frame, people):
        height, width = frame.shape[:2]
        for track in self.tracks:
            a = track.output()['bbox_xyxy']
            for person in people:
                b = [v*s for v, s in zip(person.bbox, (width, height, width, height))]
                if min(a[2], b[2]+width*.02) > max(a[0], b[0]-width*.02) and min(a[3], b[3]) > max(a[1], b[1]):
                    track.start_appearance(frame)
                    break

    def clear(self):
        super().clear()
        self.previous_color = None
        self.last_selected = {}
