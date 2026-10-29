"""Anonymous video-local users from position and clothing appearance, no faces.

Ambiguous associations remain unassigned. A missed detection never supplies
predicted wrists to the interaction rules. Archive signatures allow conservative
re-entry matching; look-alike clothes and long occlusions remain a limitation.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional

from agent.vlx_interaction import Box, PersonEvidence, Point, center, _box_valid


@dataclass(frozen=True)
class PersonDetection:
    bbox: Box
    wrists: tuple[Optional[Point], Optional[Point]]
    appearance: tuple[float, ...]


@dataclass
class _User:
    user_id: int
    bbox: Box
    appearance: tuple[float, ...]
    first_seen: float
    last_seen: float
    velocity: Point = (0.0, 0.0)
    observations: int = 1
    reentries: int = 0


def appearance_distance(a, b):
    if len(a) != len(b) or not a or sum(a) <= 0 or sum(b) <= 0:
        return 1.0
    denom = math.sqrt(sum(a) * sum(b))
    return max(0.0, 1.0 - sum(math.sqrt(x * y) for x, y in zip(a, b)) / denom)


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / max(1e-9, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)


class AnonymousUsers:
    def __init__(self, active_gap_seconds=2.0, reentry_distance=0.12, ambiguity_margin=0.06):
        self.active_gap_seconds = active_gap_seconds
        self.reentry_distance = reentry_distance
        self.ambiguity_margin = ambiguity_margin
        self.users: dict[int, _User] = {}
        self.last_time = None
        self.diagnostics = []

    def _cost(self, user, detection, t):
        age = t - user.last_seen
        appearance = appearance_distance(user.appearance, detection.appearance)
        if age > self.active_gap_seconds:
            return appearance if appearance <= self.reentry_distance else None
        predicted = tuple(center(user.bbox)[i] + user.velocity[i] * min(age, 0.5) for i in (0, 1))
        distance = math.dist(predicted, center(detection.bbox))
        scale = max(0.08, math.hypot(user.bbox[2]-user.bbox[0], user.bbox[3]-user.bbox[1]))
        if distance > scale * 0.65 or appearance > 0.6:
            return None
        geometry = (0.45 * min(1, distance / scale) + 0.25 * (1-_iou(user.bbox, detection.bbox))) / .7
        # Equal appearance/geometry weight keeps crossing people from swapping
        # IDs just because the next body is closer to the old box center.
        return .5 * geometry + .5 * appearance

    def update(self, t, detections):
        if not math.isfinite(t) or t < 0 or (self.last_time is not None and t <= self.last_time):
            raise ValueError("person observations need increasing media times")
        detections = tuple(detections)
        for d in detections:
            if not _box_valid(d.bbox) or any(not math.isfinite(v) or v < 0 for v in d.appearance):
                raise ValueError("invalid person evidence")
        costs = {(i, uid): value for i, d in enumerate(detections) for uid, user in self.users.items()
                 if (value := self._cost(user, d, t)) is not None}
        assigned = {}
        remaining = dict(costs)
        while remaining:
            confident = []
            for (i, uid), cost in remaining.items():
                alternatives = [v for (j, u), v in remaining.items()
                                if (j == i and u != uid) or (u == uid and j != i)]
                if all(v-cost >= self.ambiguity_margin for v in alternatives):
                    confident.append((i, uid))
            if not confident:
                break
            # Accept only mutual, unambiguous matches. Once a person is
            # accounted for, that identity cannot compete for another body.
            # Re-evaluate the remaining graph without greedy tie breaking.
            assigned.update(confident)
            remaining = {(i, uid): v for (i, uid), v in remaining.items()
                         if i not in assigned and uid not in assigned.values()}
        self.diagnostics = []
        # Stable tie-break for simultaneous first appearances: left to right.
        for i in sorted(range(len(detections)), key=lambda j: (detections[j].bbox[0], detections[j].bbox[1])):
            d = detections[i]
            if i not in assigned:
                candidates = {uid for j, uid in costs if j == i}
                distinct_new_body = (bool(candidates) and candidates <= set(assigned.values())
                                     and all(_iou(d.bbox, detections[j].bbox) < .3 for j in assigned))
                if candidates and not distinct_new_body:
                    self.diagnostics.append({"detection_index": i, "reason": "anonymous_identity_ambiguous"})
                    continue
                uid = len(self.users) + 1
                self.users[uid] = _User(uid, d.bbox, d.appearance, t, t)
                assigned[i] = uid
                continue
            u = self.users[assigned[i]]
            elapsed = t - u.last_seen
            if elapsed > self.active_gap_seconds:
                u.reentries += 1
                u.velocity = (0.0, 0.0)
            elif elapsed > 0:
                u.velocity = tuple((center(d.bbox)[k] - center(u.bbox)[k]) / elapsed for k in (0, 1))
            if len(u.appearance) == len(d.appearance):
                u.appearance = tuple(0.9*a + 0.1*b for a, b in zip(u.appearance, d.appearance))
            u.bbox, u.last_seen = d.bbox, t
            u.observations += 1
        self.last_time = t
        return [PersonEvidence(assigned.get(i), d.bbox, d.wrists) for i, d in enumerate(detections)]

    def report(self):
        return [{"user_id": u.user_id, "user_label": f"用户{u.user_id}",
                 "first_seen_seconds": u.first_seen, "last_seen_seconds": u.last_seen,
                 "observations": u.observations, "appearance_matched_reentries": u.reentries}
                for u in self.users.values()]
