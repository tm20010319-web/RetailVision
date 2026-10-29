"""Validate a fixed VLX instance against fresh semantic masks, frame by frame.

Thin objects can leave a propagation mask on their empty support. Independent
grounding rejects that drift. Other observed instances remain competitors; an
unmatched VLX label is never enough to jump to a neighbouring instance.
"""
from __future__ import annotations

import math


def overlap(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    aa, ab = (a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])
    return intersection/max(1e-9, aa+ab-intersection)


def contained_overlap(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    return intersection/max(1e-9, min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1])))


def center(box):
    return ((box[0]+box[2])/2, (box[1]+box[3])/2)


class GroundedInstanceSelector:
    def __init__(self, seed, max_observation_gap=.75):
        self.box = tuple(seed)
        self.competitors = []
        self.initialized = False
        self.last_seen = self.semantic_seen = None
        self.max_observation_gap = max_observation_gap

    def update(self, timestamp, candidates, vlx=None):
        deduplicated = []
        for candidate in sorted(candidates, key=lambda g: g["score"], reverse=True):
            if not any(overlap(candidate["bbox"], g["bbox"]) > .45 for g in deduplicated):
                deduplicated.append(candidate)
        candidates = deduplicated
        if not self.initialized:
            scores = sorted((overlap(self.box, g["bbox"]), i) for i, g in enumerate(candidates))
            if not scores or scores[-1][0] < .35 or (len(scores)>1 and scores[-1][0]-scores[-2][0]<.15):
                return None
            self.competitors = [g["bbox"] for g in candidates if overlap(self.box, g["bbox"]) < .1]
            self.initialized = True

        # Preserve the observed identities of neighbouring guitars, including
        # when the requested guitar is temporarily hidden. These are tracked
        # competitors, not a configured table, camera ROI or pickup location.
        assigned = set()
        for i, box in enumerate(self.competitors):
            scores = sorted((overlap(box, g["bbox"]), j) for j, g in enumerate(candidates) if j not in assigned)
            if scores and scores[-1][0] > .35:
                _, j = scores[-1]
                if overlap(self.box, candidates[j]["bbox"]) >= scores[-1][0]-.15:
                    assigned.add(j)  # conflicting instance identity is unknown
                    continue
                self.competitors[i] = candidates[j]["bbox"]
                assigned.add(j)
        matches = sorted((overlap(self.box, g["bbox"]), j) for j, g in enumerate(candidates) if j not in assigned)
        relocated = False
        if matches and matches[-1][0] <= .08 and self.last_seen is not None and timestamp-self.last_seen <= 2:
            # A short occlusion can end with a rotated, disjoint visible part.
            # Require one strong semantic candidate after competitor exclusion.
            matches = sorted((.25-math.dist(center(self.box), center(g["bbox"])), j)
                             for j, g in enumerate(candidates) if j not in assigned and g["score"] >= .6
                             and math.dist(center(self.box), center(g["bbox"])) < .1)
            relocated = True
        if matches and matches[-1][0] > .08 and (len(matches)==1 or matches[-1][0]-matches[-2][0] > .07):
            _, j = matches[-1]
            result = dict(candidates[j])
            self.box = result["bbox"]
            result["continuous"] = (not relocated and self.last_seen is not None
                                    and timestamp-self.last_seen <= self.max_observation_gap)
            result["position_source"] = "fresh_semantic_mask"
            self.last_seen = self.semantic_seen = timestamp
            if vlx and (overlap(vlx["bbox"], self.box) > .35 or
                        (overlap(vlx["bbox"], self.box) > .1 and contained_overlap(vlx["bbox"], self.box) > .65)):
                result["semantic_bbox"] = self.box
                result["bbox"] = self.box = vlx["bbox"]
                result["position_source"] = "vlx_validated_by_fresh_semantic_mask"
            return result
        if self.semantic_seen is not None and timestamp-self.semantic_seen <= 1 and vlx:
            if overlap(vlx["bbox"], self.box) > .3 and not any(overlap(vlx["bbox"], b) > .3 for b in self.competitors):
                self.box = vlx["bbox"]
                self.last_seen = timestamp
                return {"bbox": self.box, "continuous": vlx["continuous"],
                        "position_source": "bounded_vlx_pixel_bridge",
                        "semantic_confirmation_seconds": self.semantic_seen}
        return None
