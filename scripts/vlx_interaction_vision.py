"""Concrete VLX/WeDetect object grounding and CPU person-pose adapters."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import cv2
import numpy as np
from PIL import Image

from agent.vlx_interaction import TARGETS, ObjectEvidence
from agent.vlx_person_tracking import AnonymousUsers, PersonDetection
from scripts.probe_vlx_seek import iou, load_quantized_worker
from scripts.vlx_dual_support import CachedOfficialProposals, unique_targets
from scripts.vlx_interaction_tracking import LocalFlowTrack, InteractionTracks
from scripts.vlx_target_evidence import appearance_contradiction, same_region, claims_existing_track


def parse_objects(output, width, height, *, frame=None, appearance_rejections=None):
    """Exact fixed-query labels only. No confidences invented for Seek regions."""
    aliases = {target.phrase.casefold(): i for i, target in enumerate(TARGETS)}
    candidates = []
    for row in output.get("result_bbox_list", []):
        key = aliases.get(str(row.get("label", "")).strip().casefold())
        if key is None:
            continue
        box = [float(row[name]) for name in ("xmin", "ymin", "xmax", "ymax")]
        if not all(np.isfinite(box)) or not (0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
            raise ValueError("invalid VLX object box")
        contradiction = appearance_contradiction(TARGETS[key].key, box, frame) if frame is not None else None
        if contradiction:
            if appearance_rejections is not None:
                appearance_rejections.append({"class_id": key, "bbox_xyxy": box, **contradiction})
            continue
        candidates.append({"class_id": key, "label": TARGETS[key].label, "bbox_xyxy": box})
    selected, ambiguous = unique_targets(candidates)
    # All six classes are instances, so any essentially identical region with
    # two different labels is ambiguous, not just the old guitar pair.
    conflicts = {a["class_id"] for a in selected for b in selected
                 if a["class_id"] != b["class_id"] and same_region(a["bbox_xyxy"], b["bbox_xyxy"])}
    return [d for d in selected if d["class_id"] not in conflicts], sorted(set(ambiguous) | conflicts), candidates


def clothing_signature(frame, box):
    """Normalized regional HSV histograms; head pixels deliberately excluded."""
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = box
    values = []
    for top, bottom in ((0.25, 0.58), (0.58, 0.88)):
        left, right = int((x1 + (x2-x1)*0.2)*w), int((x2-(x2-x1)*0.2)*w)
        a, b = int((y1+(y2-y1)*top)*h), int((y1+(y2-y1)*bottom)*h)
        crop = frame[max(0, a):min(h, b), max(0, left):min(w, right)]
        if not crop.size:
            return ()
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
        values.extend((hist / max(float(hist.sum()), 1) / 2).tolist())
    return tuple(values)


class PoseUsers:
    def __init__(self, config):
        from ultralytics import YOLO
        self.config = config
        self.model = YOLO(config["pose_weights"])
        self.users = AnonymousUsers()

    def infer(self, frame, timestamp):
        cfg = self.config
        result = self.model.predict(frame, device=cfg["pose_device"], imgsz=cfg["pose_image_size"],
                                    conf=cfg["pose_confidence"], verbose=False)[0]
        h, w = frame.shape[:2]
        detections = []
        if result.keypoints is not None:
            boxes = result.boxes.xyxy.cpu().numpy()
            points = result.keypoints.data.cpu().numpy()
            for raw_box, keypoints in zip(boxes, points):
                box = tuple(max(0, min(1, float(v)/scale)) for v, scale in zip(raw_box, (w, h, w, h)))
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                wrists = []
                for index in (9, 10):
                    x, y, confidence = keypoints[index]
                    wrists.append((float(x)/w, float(y)/h) if confidence >= cfg["wrist_confidence"]
                                  and 0 <= x <= w and 0 <= y <= h else None)
                detections.append(PersonDetection(box, tuple(wrists), clothing_signature(frame, box)))
        return self.users.update(timestamp, detections)


class VideoVision:
    """Synchronous offline inference: every decision follows its source frame.

    Optical flow runs at the decoded source cadence. Model confirmation runs
    at a configurable media interval, independently of wall-clock speed.
    """
    def __init__(self, config, vlx_config):
        os.environ["HF_HUB_OFFLINE"] = "1"
        sys.path.insert(0, str(Path(vlx_config["external_repository"])))
        self.config = config
        self.vlx_config = vlx_config
        self.people = PoseUsers(config)
        self.proposer = CachedOfficialProposals(vlx_config["external_repository"], vlx_config["detector_checkpoint"])
        self.worker = load_quantized_worker(vlx_config["model_path"])
        self.initialize_tracking()

    def initialize_tracking(self):
        self.tracks = InteractionTracks(ttl_seconds=self.config["tracking_ttl_seconds"],
                                        use_csrt=self.config.get("appearance_tracking") == "csrt",
                                        stable_radius=self.config["interaction"]["stable_radius"])
        self.last_recognition = -float("inf")
        self.previous_boxes = {}
        self.last_observation = None
        self.ambiguous_classes = set()
        self.correction_candidates = {}

    def advance(self, frame, sequence, timestamp):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self.tracks.history:
            _, previous_time, previous_gray = self.tracks.history[-1]
            for candidate in self.correction_candidates.values():
                track = candidate["track"]
                if timestamp-previous_time > self.tracks.max_gap_seconds or gray.shape != previous_gray.shape:
                    track.alive = False
                else:
                    track.update(previous_gray, gray)
                if track.alive:
                    h, w = gray.shape
                    delta = [abs(a-b)/size for a, b, size in zip(track.output()["bbox_xyxy"], candidate["box"], (w, h, w, h))]
                    candidate["stationary"] &= max(delta) <= self.config["interaction"].get("stable_radius", .008)
                else:
                    candidate["stationary"] = False
        if isinstance(self.tracks, InteractionTracks):
            self.tracks.advance(sequence, timestamp, gray, frame)
        else:
            self.tracks.advance(sequence, timestamp, gray)

    def recognition_due(self, sequence, timestamp):
        tracked_keys = {d["class_id"] for d in self.tracks.outputs()}
        return (bool(set(self.previous_boxes) - tracked_keys)
                or timestamp - self.last_recognition + 1e-9 >= self.config["recognition_interval_seconds"])

    def recognize(self, frame, sequence, timestamp):
        import torch
        image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        proposals = self.proposer(image)
        query = "; ".join(t.phrase for t in TARGETS)
        with torch.inference_mode():
            raw = self.worker.run_task(image, "detection", query, lang="en", bbox_list=proposals,
                                       temperature=0.0, max_new_tokens=self.config["max_new_tokens"])
        return raw, len(proposals)

    def reconcile(self, selected, ambiguous, timestamp, frame):
        """A single model jump is unknown; repeated stable correction starts anew.

        This repairs a stale localization without treating the change of region
        as object motion. Cross-class and multi-instance conflicts stay unknown.
        """
        before = {d["class_id"]: d for d in self.tracks.outputs()}
        rejected, corrected, pending = set(ambiguous), {}, {}
        for detection in selected:
            key, box = detection["class_id"], detection["bbox_xyxy"]
            # A new label may be a shifted/partial proposal on an established
            # physical track. Its box need not be nearly identical to steal
            # that instance; reject substantial overlap before seeding it.
            if any(other != key and claims_existing_track(box, old["bbox_xyxy"])
                   for other, old in before.items()):
                rejected.add(key)
                continue
            old = before.get(key)
            candidate = self.correction_candidates.get(key)
            jumping = old is not None and iou(box, old["bbox_xyxy"]) < .1
            if jumping or candidate is not None:
                if (candidate is not None and 0 < timestamp-candidate["time"] <= self.tracks.ttl_seconds
                        and iou(box, candidate["box"]) >= .7):
                    tracked_match = candidate["track"].alive and iou(box, candidate["track"].output()["bbox_xyxy"]) >= .7
                    h, w = frame.shape[:2]
                    delta = [abs(a-b)/size for a, b, size in zip(box, candidate["box"], (w, h, w, h))]
                    tracked_match &= max(delta) <= self.config["interaction"].get("stable_radius", .008)
                    corrected[key] = candidate["time"] if candidate["stationary"] and tracked_match else None
                elif jumping:
                    pending[key] = {"box": box, "time": timestamp, "stationary": True,
                                    "track": LocalFlowTrack(detection, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), timestamp)}
                    rejected.add(key)
        self.correction_candidates = pending
        # Even a low-texture replacement must retire the contradicted track.
        self.tracks.tracks = [t for t in self.tracks.tracks if t.detection["class_id"] not in corrected]
        return [d for d in selected if d["class_id"] not in rejected], sorted(rejected), corrected

    def observe(self, frame, sequence, timestamp):
        h, w = frame.shape[:2]
        recognition = None
        direct = {}
        ambiguous = []
        appearance_rejections = []
        retained = []
        for track in self.tracks.tracks:
            detection = track.output()
            contradiction = appearance_contradiction(TARGETS[detection["class_id"]].key, detection["bbox_xyxy"], frame)
            if contradiction:
                appearance_rejections.append({"class_id": detection["class_id"], "bbox_xyxy": detection["bbox_xyxy"], **contradiction})
            else:
                retained.append(track)
        self.tracks.tracks = retained
        corrected = {}
        if self.recognition_due(sequence, timestamp):
            start = time.perf_counter()
            raw, proposal_count = self.recognize(frame, sequence, timestamp)
            query = "; ".join(t.phrase for t in TARGETS)
            selected, ambiguous, candidates = parse_objects(raw, w, h, frame=frame,
                                                             appearance_rejections=appearance_rejections)
            selected, ambiguous, corrected = self.reconcile(selected, ambiguous, timestamp, frame)
            self.tracks.apply((sequence, timestamp, "", frame), selected,
                              candidates=candidates, ambiguous_classes=ambiguous)
            direct = {d["class_id"]: {**d, "position_source": "vlx", "model_frame_id": sequence,
                                      "confirmed_at": timestamp} for d in selected}
            recognition = {"source_frame": sequence, "timestamp_seconds": timestamp,
                           "query": query, "raw_output": raw, "proposal_count": proposal_count,
                           "selected": selected, "ambiguous_classes": ambiguous,
                           "pending_correction_classes": sorted(self.correction_candidates),
                           "corrected_classes": sorted(corrected),
                           "seconds": time.perf_counter()-start}
            self.last_recognition = timestamp
            # A solitary contradictory label does not erase independently
            # verified pixels at the old position. Keep that current track
            # during correction confirmation. Genuine multi-instance/cross-
            # class ambiguity still suppresses evidence.
            self.ambiguous_classes = set(ambiguous) - set(self.correction_candidates)
        people = self.people.infer(frame, timestamp)
        if isinstance(self.tracks, InteractionTracks):
            self.tracks.arm_near_people(frame, people)
        boxes = {d["class_id"]: d for d in self.tracks.outputs()}
        # A low-texture object can be directly detected even when optical flow
        # cannot seed it. Keep only this frame's direct evidence in that case.
        boxes.update(direct)
        conflicts = {a for a, first in boxes.items() for b, second in boxes.items()
                     if a != b and same_region(first["bbox_xyxy"], second["bbox_xyxy"])}
        objects = []
        for key, detection in boxes.items():
            # An unresolved multi-instance/class conflict must not enter action
            # counts merely because an old display box is still retained.
            if key in self.ambiguous_classes or key in conflicts:
                continue
            pixels = detection["bbox_xyxy"]
            box = tuple(float(v)/scale for v, scale in zip(pixels, (w, h, w, h)))
            prior = self.previous_boxes.get(key)
            continuous = (key not in corrected and prior is not None and self.last_observation is not None
                          and timestamp-self.last_observation <= self.config["interaction"]["max_observation_gap_seconds"]
                          and (detection.get("position_source") in ("optical_flow", "csrt") or iou(prior, pixels) >= 0.1))
            objects.append(ObjectEvidence(TARGETS[key].key, box, continuous, corrected.get(key)))
        self.previous_boxes = {key: d["bbox_xyxy"] for key, d in boxes.items()
                               if key not in self.ambiguous_classes and key not in conflicts}
        self.last_observation = timestamp
        return objects, people, {"recognition": recognition, "tracks": list(boxes.values()),
                                 "appearance_rejections": appearance_rejections, "region_conflicts": sorted(conflicts),
                                 "user_association_diagnostics": self.people.users.diagnostics}

    def users_report(self):
        return self.people.users.report()
