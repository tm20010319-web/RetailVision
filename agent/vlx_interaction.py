"""Video-time interaction rules for the independent VLX file-analysis backend.

No membership, database, camera, or model dependencies. Coordinates are full
frame normalized. Decisions use observed media time, never inference latency.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Optional

Point = tuple[float, float]
Box = tuple[float, float, float, float]
INTERACTION_POLICY = "occlusion_aware_release_v9"


@dataclass(frozen=True)
class Target:
    key: str
    label: str
    phrase: str


TARGETS = (
    Target("orange_cartoon", "橙色卡通玩偶", "orange cartoon toy"),
    Target("white_egg", "白色蛋形玩偶", "small white egg-shaped toy"),
    Target("pink_plush", "粉色毛绒玩偶", "pink plush toy"),
    Target("yellow_robot_dog", "黄色机器狗", "yellow robot dog toy"),
    Target("white_guitar", "白色电吉他", "white electric guitar"),
    Target("bee_plush", "小蜜蜂玩偶", "yellow and black bee plush toy"),
)


@dataclass(frozen=True)
class PersonEvidence:
    # None is an observed person whose anonymous track is currently ambiguous.
    user_id: Optional[int]
    bbox: Box
    wrists: tuple[Optional[Point], Optional[Point]]


@dataclass(frozen=True)
class ObjectEvidence:
    key: str
    bbox: Box
    # False after a tracker break/relocalization, never a fabricated motion.
    continuous: bool = True
    # Earlier source pixels continuously verified this same box as stationary.
    # Only usable with an already observed resting pose, never a new baseline.
    stationary_since_seconds: Optional[float] = None


@dataclass(frozen=True)
class InteractionConfig:
    baseline_seconds: float = 0.6
    pickup_confirm_seconds: float = 0.4
    putdown_confirm_seconds: float = 0.8
    stable_radius: float = 0.008
    pickup_distance: float = 0.018
    wrist_motion_distance: float = 0.004
    hand_margin: float = 0.018
    missing_grace_seconds: float = 2.0
    user_missing_grace_seconds: float = 2.0
    max_observation_gap_seconds: float = 0.75
    rest_pose_memory_seconds: float = 30.0
    occluded_hold_grace_seconds: float = 15.0

    def __post_init__(self):
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0
               for v in asdict(self).values()):
            raise ValueError("interaction settings must be finite positive numbers")
        if self.stable_radius >= self.pickup_distance:
            raise ValueError("stable_radius must be below pickup_distance")


@dataclass
class _State:
    baseline: Optional[Point] = None
    baseline_box: Optional[Box] = None
    resting_poses: list = field(default_factory=list)
    rest_match_box: Optional[Box] = None
    rest_match_since: Optional[float] = None
    stable_since: Optional[float] = None
    stable_center: Optional[Point] = None
    stable_box: Optional[Box] = None
    last_seen: Optional[float] = None
    actor_last_seen: Optional[float] = None
    last_center: Optional[Point] = None
    missing: bool = False
    pending: Optional[dict] = None
    contacts: dict = field(default_factory=dict)
    contact_options: dict = field(default_factory=dict)
    contact_last_seen: Optional[float] = None
    pickup_gaps: int = 0
    session: Optional[dict] = None
    release_since: Optional[float] = None
    other_holder_since: Optional[float] = None
    other_holder_id: Optional[int] = None
    sequence: int = 0
    status: str = "unseen"
    reason: str = "not_observed"
    held_reference_area: float = 0.0
    partial_extent: bool = False
    last_box: Optional[Box] = None
    occlusion_started: Optional[float] = None
    pickup_origins: dict = field(default_factory=dict)
    release_rest_box: Optional[Box] = None
    release_full_seen: Optional[float] = None


def center(box: Box) -> Point:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def point_box_distance(point: Point, box: Box) -> float:
    return math.hypot(max(box[0] - point[0], 0, point[0] - box[2]),
                      max(box[1] - point[1], 0, point[1] - box[3]))


def box_distance(a: Box, b: Box) -> float:
    return math.hypot(max(a[0]-b[2], b[0]-a[2], 0),
                      max(a[1]-b[3], b[1]-a[3], 0))


def box_motion(origin: Box, current: Box) -> Point:
    """Conservative translation shared by both opposite edges of a box.

    Extending a guitar box from its body to its neck moves the center without
    moving the object. A stationary opposite edge supplies no translation.
    Rotation/size changes are deliberately under-estimated, not invented motion.
    """
    result = []
    for axis in (0, 1):
        a, b = current[axis] - origin[axis], current[axis+2] - origin[axis+2]
        result.append(math.copysign(min(abs(a), abs(b)), a) if a*b > 0 else 0.0)
    return tuple(result)


def _box_valid(box):
    return (len(box) == 4 and all(math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)


class InteractionAnalyzer:
    """One object instance per fixed target; disjoint model matches are unknown.

    Pickup requires a stable origin, sustained displacement, one unambiguous
    nearby wrist and hand/object co-motion. Putdown requires sustained object
    stability and either visible release or return to an observed resting pose.
    This is a 2D visual estimate, not proof of physical grasp or support.
    """

    def __init__(self, config=None, targets=TARGETS):
        self.config = config or InteractionConfig()
        self.targets = {t.key: t for t in targets}
        if not self.targets or len(self.targets) != len(targets):
            raise ValueError("unique targets required")
        self.states = {key: _State() for key in self.targets}
        self.sessions: list[dict] = []
        self.events: list[dict] = []
        self.last_time: Optional[float] = None
        self.finished = False

    def process(self, timestamp: float, objects, people):
        if self.finished:
            raise ValueError("analysis already finished")
        if (not math.isfinite(timestamp) or timestamp < 0
                or (self.last_time is not None and timestamp <= self.last_time)):
            raise ValueError("media timestamps must be finite and strictly increasing")
        objects, people = tuple(objects), tuple(people)
        by_key = {o.key: o for o in objects}
        if len(by_key) != len(objects) or any(k not in self.targets for k in by_key):
            raise ValueError("unknown or duplicate object instance")
        ids = [p.user_id for p in people if p.user_id is not None]
        if len(ids) != len(set(ids)) or any(isinstance(i, bool) or not isinstance(i, int) or i < 1 for i in ids):
            raise ValueError("unique positive anonymous user IDs required")
        for value in (*objects, *people):
            if not _box_valid(value.bbox):
                raise ValueError("invalid normalized box")
        for obj in objects:
            if obj.stationary_since_seconds is not None and not (
                    math.isfinite(obj.stationary_since_seconds) and 0 <= obj.stationary_since_seconds <= timestamp):
                raise ValueError("invalid stationary source interval")
        for person in people:
            if len(person.wrists) != 2 or any(
                p is not None and (len(p) != 2 or not all(math.isfinite(v) and 0 <= v <= 1 for v in p))
                for p in person.wrists
            ):
                raise ValueError("invalid wrist evidence")
        gap = self.last_time is not None and timestamp - self.last_time > self.config.max_observation_gap_seconds
        start = len(self.events)
        for key, state in self.states.items():
            self._step(key, state, timestamp, by_key.get(key), people, gap)
        self.last_time = timestamp
        return {"timestamp_seconds": timestamp, "objects": self.snapshot(timestamp),
                "events": self.events[start:]}

    def _near_hands(self, obj, people):
        near = []
        for person in people:
            hands = [(point_box_distance(w, obj.bbox), w) for w in person.wrists if w is not None]
            if hands and min(hands)[0] <= self.config.hand_margin:
                near.append((person, min(hands)[1]))
            elif any(w is None for w in person.wrists):
                # Unknown wrists inside an overlapping person box cannot be
                # treated as absence of contact (especially during occlusion).
                a, b = obj.bbox, person.bbox
                if min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1]):
                    near.append((person, None))
        return near

    def _step(self, key, s, t, obj, people, gap):
        cfg = self.config
        previous_seen = s.last_seen
        recent_contact = (s.contact_last_seen is not None
                          and t - s.contact_last_seen <= cfg.missing_grace_seconds)
        if not s.session and not recent_contact:
            self._clear_pickup(s)
        if s.session and previous_seen is not None and t - previous_seen > cfg.missing_grace_seconds:
            owner = next((p for p in people if p.user_id == s.session["user_id"]), None)
            # Only a shrinking, body-occluded track may outlive the ordinary
            # missing lease. Visible owner continuity is not a new detection;
            # keep the state explicitly unobserved, with a bounded timeout.
            occluded_hold = (s.partial_extent and s.last_box is not None
                             and owner is not None and box_distance(s.last_box, owner.bbox)==0
                             and not s.session["attribution_uncertain"]
                             and t-previous_seen <= cfg.occluded_hold_grace_seconds
                             and (obj is None or (box_distance(obj.bbox, owner.bbox)==0
                                  and math.dist(center(obj.bbox), center(s.last_box))
                                      <= 2*math.sqrt(s.held_reference_area))))
            source_gap = t-self.last_time if gap and self.last_time is not None else 0
            known_return = (obj is not None and 0 < source_gap <= cfg.missing_grace_seconds
                            and self.last_time-previous_seen <= cfg.missing_grace_seconds
                            and not s.session["attribution_uncertain"]
                            and any(p.user_id == s.session["user_id"] for p in people)
                            and not any(p.user_id != s.session["user_id"] for p, _ in self._near_hands(obj, people))
                            and self._matching_resting_pose(s, obj.bbox, t) is not None)
            if occluded_hold:
                if s.occlusion_started is None:
                    s.occlusion_started = previous_seen
            elif known_return:
                # A bounded gap in the source may cross the timeout without
                # any intervening frame proving further absence. Keep an
                # already active session only when the same user's object is
                # now observed at a known support. Future frames must still
                # confirm release; never backdate it into the missing source.
                s.session["source_gap_return_recoveries"] += 1
            else:
                self._interrupt(s, "object_lost", previous_seen)
        if obj is None:
            s.pickup_origins.clear()
            s.missing = True
            s.release_since = None
            s.stable_center = s.stable_since = None
            s.rest_match_since = None
            if not recent_contact or s.session:
                s.baseline = None
                self._clear_pickup(s)
            elif s.pending:
                s.pending["last_supported_at"] = None
            s.status = "holding_unobserved" if s.session else "unseen"
            s.reason = "object_missing_or_ambiguous"
            return
        pos = center(obj.bbox)
        if s.session and s.occlusion_started is not None:
            s.session.setdefault("occlusion_intervals", []).append(
                {"last_seen_seconds": s.occlusion_started, "reacquired_seconds": t})
            s.session["duration_includes_occlusion"] = True
            s.occlusion_started = None
        near = self._near_hands(obj, people)
        discontinuous = gap or s.missing or not obj.continuous
        if discontinuous or s.session:
            s.pickup_origins.clear()
        else:
            s.pickup_origins = {k:c for k,c in s.pickup_origins.items()
                                if t-c[5] <= cfg.missing_grace_seconds}
            # Preserve only contact actually observed at a known resting
            # extent. A bystander first touched after displacement has no
            # origin and cannot borrow the original user's motion evidence.
            at_origin = (s.baseline_box is not None
                         and math.hypot(*box_motion(s.baseline_box,obj.bbox)) < cfg.pickup_distance
                         and self._matching_resting_pose(s,obj.bbox,t,allow_partial=True) is not None)
            if at_origin and len(near)==1 and near[0][0].user_id is not None:
                actor = near[0][0]
                for hand,wrist in enumerate(actor.wrists):
                    if wrist is not None and point_box_distance(wrist,obj.bbox)<=cfg.hand_margin:
                        hand_key = (actor.user_id,hand)
                        old = s.pickup_origins.get(hand_key)
                        s.pickup_origins[hand_key] = ((*old[:5],t) if old else
                            (t,wrist,hand,pos,obj.bbox,t))
        if s.pending and math.hypot(*box_motion(s.pending["origin_box"], obj.bbox)) < cfg.pickup_distance:
            # Returning to the contact origin cancels an unconfirmed lift even
            # if a wrist is hidden. A later lift needs its own onset timestamp.
            s.pending = None
        if s.pending and recent_contact and not s.session:
            pending_actor = next((p for p in people if p.user_id == s.pending["user_id"]), None)
            if pending_actor is not None:
                pending_wrist = pending_actor.wrists[s.pending["hand_index"]]
                own_contact = (pending_wrist is not None
                               and point_box_distance(pending_wrist, obj.bbox) <= cfg.hand_margin)
                competing_contact = any(p.user_id != pending_actor.user_id for p, _ in near)
                if pending_wrist is None or (own_contact and competing_contact
                        and (discontinuous or (s.pending["supported_seconds"] <= 0
                                               and s.pending["last_supported_at"] is None))):
                    # Occlusion or a bystander's overlapping wrist is no new
                    # pickup evidence, but need not erase a supported onset.
                    # Only the original hand can keep the contact lease alive.
                    if own_contact:
                        s.contact_last_seen = t
                        hand_key = (pending_actor.user_id, s.pending["hand_index"])
                        contact = s.contact_options.get(hand_key)
                        if contact is not None:
                            s.contact_options[hand_key] = (*contact[:5], t)
                    s.last_seen = t
                    s.missing = discontinuous
                    s.pending["last_supported_at"] = None
                    s.status, s.reason = "pickup_pending", "awaiting_unambiguous_same_hand"
                    return
        if discontinuous and s.pending and recent_contact and len(near) == 1:
            actor = near[0][0]
            contact = s.contacts.get(actor.user_id)
            if contact and actor.wrists[contact[2]] is None:
                # The other wrist is not a substitute for the original hand.
                # Wait briefly for it to return, without advancing evidence.
                s.missing = True
                s.pending["last_supported_at"] = None
                s.status, s.reason = "pickup_pending", "awaiting_same_hand_recovery"
                return
        recover_pickup = (discontinuous and not s.session and recent_contact
                          and self._can_recover_pickup(s, obj, near))
        if discontinuous:
            s.stable_center = s.stable_since = None
            s.release_since = None
            if recover_pickup:
                s.pickup_gaps += 1
                if s.pending:
                    s.pending["last_supported_at"] = None
            else:
                s.baseline = None
                self._clear_pickup(s)
        s.missing = False
        s.last_seen = t
        s.last_center = pos
        s.last_box = obj.bbox
        if s.session:
            area = (obj.bbox[2]-obj.bbox[0])*(obj.bbox[3]-obj.bbox[1])
            s.held_reference_area = max(s.held_reference_area, area)
            s.partial_extent = area < .5*s.held_reference_area
        # Model extents can grow/shrink around a stationary object (notably a
        # guitar neck). Shared edge translation measures physical motion;
        # center displacement alone would restart release confirmation forever.
        stable_distance = math.dist(s.stable_center, pos) if s.stable_center is not None else float("inf")
        actor = next((p for p in people if s.session and p.user_id == s.session["user_id"]), None)
        visible_hands_clear = (actor is not None and any(w is not None for w in actor.wrists)
                               and all(w is None or point_box_distance(w, obj.bbox) > cfg.hand_margin
                                       for w in actor.wrists))
        partial_return = (s.session is not None and s.release_since is not None
                          and s.release_rest_box is not None and s.release_full_seen is not None
                          and t-s.release_full_seen <= cfg.missing_grace_seconds
                          and self._contained_resting_fragment(s.release_rest_box,obj.bbox)
                          and abs(s.release_rest_box[3]-obj.bbox[3]) <= cfg.stable_radius)
        if partial_return:
            # A fragment can preserve a release already observed at a known
            # support. It cannot start one or finish one: both endpoints need
            # full resting extents, with a bounded intervening fragment lease.
            stable_distance = 0.0
        if s.session and visible_hands_clear and s.stable_center is not None and s.stable_box is not None and self._matching_resting_pose(s, obj.bbox, t) is not None:
            stable_distance = math.hypot(*box_motion(s.stable_box, obj.bbox))
            if self._matching_resting_pose(s, s.stable_box, t) is not None:
                # Both extents are at an observed support and the actor's
                # visible hands have left. A neck/body extent oscillation
                # inside that support must not restart release indefinitely.
                stable_distance = 0.0
        if stable_distance > cfg.stable_radius:
            s.stable_center, s.stable_since = pos, t
            s.stable_box = obj.bbox
            s.release_since = None
        if s.session:
            actor = next((p for p in people if p.user_id == s.session["user_id"]), None)
            if actor is not None:
                s.actor_last_seen = t
            elif s.actor_last_seen is not None and t - s.actor_last_seen > cfg.user_missing_grace_seconds:
                self._interrupt(s, "user_track_lost", s.session["last_observed_seconds"])
                s.status, s.reason = "unknown", "user_track_lost"
                return
            s.status, s.reason = "holding", "awaiting_visible_release"
            if discontinuous:
                s.session["observation_gaps"] += 1
            if actor is not None and not discontinuous:
                s.session["last_observed_seconds"] = t
            other_contact = any(p.user_id != s.session["user_id"] for p, _ in near)
            if other_contact:
                s.reason = "multiple_users_or_possible_handover"
            competitors = [p.user_id for p, wrist in near if p.user_id is not None
                           and p.user_id != s.session["user_id"] and wrist is not None]
            actor_contact = any(p.user_id == s.session["user_id"] for p, _ in near)
            if (len(competitors) == 1 and not actor_contact and not discontinuous
                    and self._matching_resting_pose(s, obj.bbox, t) is None):
                if s.other_holder_id != competitors[0] or s.other_holder_since is None:
                    s.other_holder_id, s.other_holder_since = competitors[0], t
                if t-s.other_holder_since+1e-9 >= cfg.putdown_confirm_seconds:
                    s.session["attribution_uncertain"] = True
            else:
                # A passing wrist or simultaneous contact does not prove that
                # ownership changed. Require sustained exclusive other contact.
                s.other_holder_id = s.other_holder_since = None
            # A shrunken fragment on the owner's body cannot prove release
            # merely because wrists fall outside it. Away from the body a
            # rotated/smaller object may still be fully visible on a support.
            fragment_on_body = (actor is not None and s.partial_extent
                                and actor.bbox[0]<=pos[0]<=actor.bbox[2]
                                and actor.bbox[1]<=pos[1]<=actor.bbox[3])
            hands_clear = (actor is not None and any(w is not None for w in actor.wrists)
                           and all(w is None or point_box_distance(w,obj.bbox)>cfg.hand_margin
                                   for w in actor.wrists))
            body_clear = actor is not None and box_distance(obj.bbox,actor.bbox)>cfg.hand_margin
            away = (hands_clear and not near and not fragment_on_body
                    and (all(w is not None for w in actor.wrists) or body_clear))
            returned = (actor is not None and not other_contact
                        and (self._matching_resting_pose(s, obj.bbox, t) is not None or partial_return))
            full_return = self._matching_resting_pose(s,obj.bbox,t)
            if returned and full_return is not None:
                s.release_rest_box, s.release_full_seen = full_return, t
            # A return to a previously observed support may START evidence on
            # reacquisition. Only later continuous observations can confirm it;
            # the unseen interval contributes no release-confirmation time.
            if (away or returned) and (not discontinuous or returned):
                if s.release_since is None:
                    s.release_since = t
                if (not discontinuous and (not partial_return or full_return is not None)
                        and t - s.release_since + 1e-9 >= cfg.putdown_confirm_seconds):
                    if s.session["attribution_uncertain"]:
                        self._interrupt(s, "possible_handover", s.session["last_observed_seconds"])
                        s.reason = "possible_handover"
                    else:
                        self._complete(key, s, s.release_since, t)
                        s.reason = "rest_pose_return_confirmed" if returned else "release_confirmed"
                    s.status = "resting"
                    s.baseline = pos
                    s.baseline_box = obj.bbox
                    self._remember_resting_pose(s, obj.bbox, t)
            else:
                s.release_since = None
            return

        s.status, s.reason = "resting", "building_stable_baseline"
        if discontinuous and not recover_pickup:
            s.rest_match_since = None
            if self._matching_resting_pose(s, obj.bbox, t, allow_partial=True) is not None:
                s.rest_match_since, s.rest_match_box = t, obj.bbox
            if (obj.stationary_since_seconds is not None
                    and t-obj.stationary_since_seconds >= cfg.baseline_seconds
                    and self._matching_resting_pose(s, obj.bbox, t) is not None):
                s.baseline, s.baseline_box = pos, obj.bbox
                self._remember_resting_pose(s, obj.bbox, t)
                s.reason = "verified_rest_pose_recovered"
                return
            if near:
                s.status, s.reason = "unknown", "initial_contact_without_observed_pickup"
            return
        if s.baseline is None:
            self._reobserve_resting_pose(s, obj.bbox, t)
        if s.baseline is None:
            if near:
                s.stable_since = t
                s.status = "unknown"
                s.reason = "initial_contact_without_observed_pickup"
                return
            if t - s.stable_since + 1e-9 >= cfg.baseline_seconds:
                s.baseline = s.stable_center
                s.baseline_box = s.stable_box
                self._remember_resting_pose(s, s.stable_box, t)
            return
        continuing_contact = None
        if not s.pending and len(near)>1 and not discontinuous:
            supported = []
            for person,wrist in near:
                if person.user_id is None or wrist is None:
                    continue
                for hand,current in enumerate(person.wrists):
                    origin = s.pickup_origins.get((person.user_id,hand))
                    if origin is None or current is None or point_box_distance(current,obj.bbox)>cfg.hand_margin:
                        continue
                    delta = box_motion(origin[4],obj.bbox)
                    moved = math.hypot(*delta)
                    hand_delta = tuple(a-b for a,b in zip(current,origin[1]))
                    hand_moved = math.hypot(*hand_delta)
                    if (moved>=cfg.pickup_distance and hand_moved>=max(cfg.wrist_motion_distance,.5*moved)
                            and sum(a*b for a,b in zip(delta,hand_delta)) >= .5*moved*hand_moved):
                        supported.append((person,current,origin))
            if len({p.user_id for p,_,_ in supported})==1:
                person,wrist,origin = supported[0]
                continuing_contact = person,wrist
                s.contact_options[(person.user_id,origin[2])] = origin
        if s.pending and not discontinuous and (s.pending["supported_seconds"] > 0
                                                or s.pending["last_supported_at"] is not None):
            for person, _ in near:
                if person.user_id == s.pending["user_id"]:
                    original_wrist = person.wrists[s.pending["hand_index"]]
                    if original_wrist is not None and point_box_distance(original_wrist, obj.bbox) <= cfg.hand_margin:
                        # Once unique hand/object co-motion has started a lift,
                        # a nearby second person cannot erase that onset. The
                        # same original hand must still supply further co-motion;
                        # this never bridges a missing or switched object.
                        continuing_contact = (person, original_wrist)
        unique = continuing_contact is not None or (len(near) == 1 and near[0][0].user_id is not None and near[0][1] is not None)
        if not unique:
            # A fast lift can leave a stale flow box for a few observations.
            # Retain an already observed contact/motion candidate briefly;
            # absence of contact never contributes confirmation time.
            if s.pending and recent_contact and not near:
                if (not discontinuous and not s.pickup_gaps and s.pending["supported_seconds"] > 0
                        and math.hypot(*box_motion(s.pending["origin_box"], obj.bbox)) >= cfg.pickup_distance):
                    s.pending["flight_observed"] = True
                s.pending["last_supported_at"] = None
                s.status, s.reason = "pickup_pending", "awaiting_contact_recovery"
                return
            self._clear_pickup(s)
            if near:
                s.reason = "hand_or_user_ambiguous"
            elif t - s.stable_since >= cfg.baseline_seconds:
                s.baseline = s.stable_center
                s.baseline_box = s.stable_box
                self._remember_resting_pose(s, s.stable_box, t)
            return
        actor, wrist = continuing_contact or near[0]
        if s.pending and s.pending["user_id"] != actor.user_id:
            self._clear_pickup(s)
            s.baseline = None
            s.status, s.reason = "unknown", "pickup_user_changed"
            return
        if s.pending:
            original_hand = actor.wrists[s.pending["hand_index"]]
            if (original_hand is None or point_box_distance(original_hand, obj.bbox)>cfg.hand_margin):
                catch_hands=[(point_box_distance(w,obj.bbox),i,w) for i,w in enumerate(actor.wrists)
                             if w is not None and point_box_distance(w,obj.bbox)<=cfg.hand_margin]
                if (s.pending.get("flight_observed") and not discontinuous and not s.pickup_gaps and catch_hands):
                    _,hand_index,original_hand=min(catch_hands)
                    s.pending.update(hand_index=hand_index,wrist_origin=original_hand,
                                     caught_by_same_user=True,last_supported_at=None)
                    s.contact_options[(actor.user_id,hand_index)]=(t,original_hand,hand_index,pos,obj.bbox,t)
            if original_hand is None:
                s.pending["last_supported_at"] = None
                s.status, s.reason = "pickup_pending", "awaiting_same_hand_recovery"
                return
            if point_box_distance(original_hand, obj.bbox) > cfg.hand_margin:
                self._clear_pickup(s)
                s.baseline = None
                s.status, s.reason = "unknown", "pickup_hand_changed"
                return
        self._refresh_contact_options(s, actor, obj, t)
        options = [c for (uid, hand), c in s.contact_options.items()
                   if uid == actor.user_id and actor.wrists[hand] is not None
                   and point_box_distance(actor.wrists[hand], obj.bbox) <= cfg.hand_margin
                   and (not s.pending or hand == s.pending["hand_index"])]
        if not options:
            self._clear_pickup(s)
            s.status, s.reason = "unknown", "pickup_contact_expired"
            return
        old_contact = max(options, key=lambda c: self._contact_motion_score(c, actor, obj))
        wrist = actor.wrists[old_contact[2]]
        s.contacts = {actor.user_id: old_contact}
        s.contact_last_seen = t
        # A changed model box before the user touches the object is not a lift.
        # The object itself must move after the same-hand contact was observed.
        contact_box = s.pending["origin_box"] if s.pending else s.contacts[actor.user_id][4]
        displaced = (math.hypot(*box_motion(s.baseline_box, obj.bbox)) >= cfg.pickup_distance
                     and math.hypot(*box_motion(contact_box, obj.bbox)) >= cfg.pickup_distance)
        if not displaced:
            s.pending = None
            if (math.hypot(*box_motion(s.baseline_box, obj.bbox)) <= cfg.stable_radius
                    and self._matching_resting_pose(s, obj.bbox, t) is not None):
                # A stationary, anchored partial extent is another view of
                # the already observed resting object, even while touched.
                # It cannot establish a new origin or advance pickup evidence.
                self._remember_resting_pose(s, obj.bbox, t)
            s.reason = "contact_without_pickup_motion"
            return
        if s.pending is None or s.pending["user_id"] != actor.user_id:
            s.pending = {"user_id": actor.user_id, "started": t,
                         "wrist_origin": old_contact[1], "hand_index": old_contact[2],
                         "origin_box": contact_box,
                         "supported_seconds": 0.0, "last_supported_at": None}
        s.status, s.reason = "pickup_pending", "confirming_contact_and_motion"
        hand_delta = tuple(wrist[i] - s.pending["wrist_origin"][i] for i in (0, 1))
        object_delta = box_motion(contact_box, obj.bbox)
        co_motion = (math.hypot(*hand_delta) >= cfg.wrist_motion_distance
                     and sum(a * b for a, b in zip(hand_delta, object_delta)) > 0)
        co_motion = co_motion or s.pending.get("caught_by_same_user",False)
        last_supported = s.pending["last_supported_at"]
        if co_motion and last_supported is not None and not discontinuous:
            s.pending["supported_seconds"] += min(t-last_supported, cfg.max_observation_gap_seconds)
        s.pending["last_supported_at"] = t if co_motion else None
        if (s.pending["supported_seconds"] + 1e-9 >= cfg.pickup_confirm_seconds
                and co_motion and not discontinuous):
            s.sequence += 1
            s.session = {"session_id": f"{key}:{s.sequence}", "object_key": key,
                         "object_name": self.targets[key].label, "user_id": actor.user_id,
                         "user_label": f"用户{actor.user_id}", "pickup_seconds": s.pending["started"],
                         "pickup_confirmed_seconds": t, "putdown_seconds": None,
                         "putdown_confirmed_seconds": None, "holding_seconds": None,
                         "last_observed_seconds": t, "status": "holding", "end_reason": None,
                         "observation_gaps": s.pickup_gaps, "attribution_uncertain": False}
            s.session["source_gap_return_recoveries"] = 0
            s.held_reference_area = max((obj.bbox[2]-obj.bbox[0])*(obj.bbox[3]-obj.bbox[1]),
                (s.baseline_box[2]-s.baseline_box[0])*(s.baseline_box[3]-s.baseline_box[1]))
            s.partial_extent = False
            s.occlusion_started = None
            s.release_rest_box = s.release_full_seen = None
            self.sessions.append(s.session)
            self._event(s.session, "PICK_UP", s.session["pickup_seconds"], t)
            s.actor_last_seen = t
            s.other_holder_id = s.other_holder_since = None
            self._clear_pickup(s)
            s.status, s.reason = "holding", "pickup_confirmed"

    @staticmethod
    def _clear_pickup(s):
        s.pending = None
        s.contacts.clear()
        s.contact_options.clear()
        s.contact_last_seen = None
        s.pickup_gaps = 0

    def _refresh_contact_options(self, s, actor, obj, t):
        # Both hands may have been observed touching the same object before it
        # moved. Preserve their independent origins; never borrow a new hand
        # to finish another hand's already pending pickup.
        s.contact_options = {key: c for key, c in s.contact_options.items()
                             if key[0] == actor.user_id
                             and t-c[5] <= self.config.missing_grace_seconds}
        for index, wrist in enumerate(actor.wrists):
            key = (actor.user_id, index)
            if wrist is None:
                continue
            if point_box_distance(wrist, obj.bbox) > self.config.hand_margin:
                s.contact_options.pop(key, None)
            elif key in s.contact_options:
                s.contact_options[key] = (*s.contact_options[key][:5], t)
            elif not s.pending:
                s.contact_options[key] = (t, wrist, index, center(obj.bbox), obj.bbox, t)

    @staticmethod
    def _contact_motion_score(contact, actor, obj):
        hand = tuple(a-b for a, b in zip(actor.wrists[contact[2]], contact[1]))
        motion = box_motion(contact[4], obj.bbox)
        return sum(a*b for a, b in zip(hand, motion))

    def _remember_resting_pose(self, s, box, t):
        # References come only from observed no-hand stability or release.
        # Keep distinct extents/placements; a partial model box must not erase
        # the recent full-object reference. These are video-local, not an ROI.
        s.resting_poses = [(b, seen) for b, seen in s.resting_poses
                           if t-seen <= self.config.rest_pose_memory_seconds]
        for i, (old, _) in enumerate(s.resting_poses):
            if max(abs(a-b) for a, b in zip(old, box)) <= self.config.stable_radius:
                s.resting_poses[i] = (old, t)
                return
        s.resting_poses.append((tuple(box), t))
        s.resting_poses = s.resting_poses[-16:]

    def _reobserve_resting_pose(self, s, box, t):
        # Contact alone cannot establish a baseline. Only a recent observed
        # resting pose may be rearmed, after its entire extent stays matched
        # for the baseline window. Reacquisition itself never counts a lift.
        match = self._matching_resting_pose(s, box, t, allow_partial=True)
        if match is None:
            s.rest_match_since = s.rest_match_box = None
            return
        if (s.rest_match_since is None or
                math.hypot(*box_motion(s.rest_match_box, box)) > self.config.stable_radius):
            s.rest_match_since = t
        s.rest_match_box = box
        if t-s.rest_match_since + 1e-9 >= self.config.baseline_seconds:
            s.baseline, s.baseline_box = center(box), tuple(box)
            self._remember_resting_pose(s, box, t)
            self._clear_pickup(s)
            s.rest_match_since = s.rest_match_box = None

    def _matching_resting_pose(self, s, box, t, allow_partial=False):
        # The observed origin of an ongoing hold remains relevant even when
        # the user holds the object longer than the idle reference cache TTL.
        if s.session and s.baseline_box is not None and self._same_resting_extent(s.baseline_box, box):
            return s.baseline_box
        return next((b for b, seen in reversed(s.resting_poses)
                     if t-seen <= self.config.rest_pose_memory_seconds
                     and (self._same_resting_extent(b, box)
                          or (allow_partial and self._contained_resting_fragment(b, box)))), None)

    def _contained_resting_fragment(self, old, box):
        # Occlusion may expose only an interior fragment of a known support
        # pose. It can rearm that existing origin after sustained observation,
        # but cannot confirm a putdown or invent an origin for a new object.
        margin = self.config.stable_radius*.5
        return (box[0]>=old[0]-margin and box[1]>=old[1]-margin
                and box[2]<=old[2]+margin and box[3]<=old[3]+margin
                and box[2]-box[0]>=.3*(old[2]-old[0])
                and box[3]-box[1]>=.3*(old[3]-old[1]))

    def _same_resting_extent(self, old, box):
        radius = self.config.stable_radius
        if max(abs(a-v) for a, v in zip(old, box)) <= radius:
            return True
        # A detector can omit an antenna/neck; a returned object can shift a
        # little on its support. Keep the footprint overlap and lower edge
        # anchored inside the pickup threshold, not merely its box center.
        old_width, width = old[2]-old[0], box[2]-box[0]
        return (abs(center(old)[0]-center(box)[0]) <= min(.5*min(old_width, width), .5*self.config.pickup_distance)
                and min(old[2], box[2])-max(old[0], box[0]) >= .6*min(old_width, width)
                and min(old_width, width) >= .5*max(old_width, width)
                and abs(old[3]-box[3]) <= .8*self.config.pickup_distance
                and min(old[3]-old[1], box[3]-box[1])+1e-9 >= .5*max(old[3]-old[1], box[3]-box[1])
                and abs(old[1]-box[1]) <= .5*max(old[3]-old[1], box[3]-box[1])+1e-9)

    def _can_recover_pickup(self, s, obj, near):
        """Bridge a short break only with the same previously observed hand.

        A relocalized label alone cannot establish object continuity. Require
        an observed resting origin, prior contact, unique same-user contact,
        and matching hand/object displacement. The reacquired sample cannot
        itself confirm pickup; further supported observations are required.
        """
        if s.baseline is None or len(near) != 1:
            return False
        actor, wrist = near[0]
        if actor.user_id is None or wrist is None:
            return False
        if s.pending and s.pending["user_id"] != actor.user_id:
            return False
        object_delta = box_motion(s.baseline_box, obj.bbox)
        for (uid, index), contact in s.contact_options.items():
            if uid != actor.user_id or (s.pending and index != s.pending["hand_index"]):
                continue
            wrist = actor.wrists[index]
            if wrist is None or point_box_distance(wrist, obj.bbox) > self.config.hand_margin:
                continue
            hand_delta = tuple(v-b for v, b in zip(wrist, contact[1]))
            object_distance, hand_distance = math.hypot(*object_delta), math.hypot(*hand_delta)
            # A partial box at the same observed origin can preserve contact,
            # but supplies no pickup confirmation. A later move is required.
            a, b = s.baseline_box, obj.bbox
            intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
            smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
            if object_distance < self.config.pickup_distance and intersection >= 0.7*smaller:
                s.contacts = {uid: contact}
                return True
            if object_distance < self.config.pickup_distance or hand_distance < self.config.wrist_motion_distance:
                continue
            alignment = sum(a*b for a, b in zip(object_delta, hand_delta)) / (object_distance*hand_distance)
            size = math.hypot(obj.bbox[2]-obj.bbox[0], obj.bbox[3]-obj.bbox[1])
            if alignment >= 0.5 and math.dist(object_delta, hand_delta) <= max(size, 2*self.config.hand_margin):
                s.contacts = {uid: contact}
                return True
        return False

    def _complete(self, key, s, released_at, confirmed_at):
        session = s.session
        session.update(status="completed", putdown_seconds=released_at,
                       putdown_confirmed_seconds=confirmed_at,
                       holding_seconds=round(released_at - session["pickup_seconds"], 6),
                       end_reason="observed_release")
        self._event(session, "HOLDING", session["pickup_seconds"], confirmed_at,
                    ended_at_seconds=released_at, duration_seconds=session["holding_seconds"])
        self._event(session, "PUT_DOWN", released_at, confirmed_at)
        s.session = None
        s.occlusion_started = None
        s.release_rest_box = s.release_full_seen = None
        s.release_since = None
        self._clear_pickup(s)

    def _interrupt(self, s, reason, last_observed):
        if s.session:
            s.session.update(status="incomplete", end_reason=reason,
                             last_observed_seconds=last_observed)
            s.session = None
        s.occlusion_started = None
        s.release_rest_box = s.release_full_seen = None
        s.pickup_origins.clear()
        s.baseline = s.stable_since = s.stable_center = None
        s.baseline_box = s.rest_match_since = s.rest_match_box = None
        s.release_since = None
        self._clear_pickup(s)

    def _event(self, session, kind, t, confirmed, **extra):
        self.events.append({"event_id": f'{session["session_id"]}:{kind}', "event_type": kind,
                            "session_id": session["session_id"], "object_key": session["object_key"],
                            "object_name": session["object_name"], "user_id": session["user_id"],
                            "user_label": session["user_label"], "timestamp_seconds": t,
                            "confirmed_at_seconds": confirmed, **extra})

    def snapshot(self, timestamp):
        return [{"object_key": k, "object_name": self.targets[k].label, "state": s.status,
                 "reason": s.reason, "user_id": s.session["user_id"] if s.session else None,
                 "elapsed_holding_seconds": round(timestamp - s.session["pickup_seconds"], 6) if s.session else None}
                for k, s in self.states.items()]

    def finish(self):
        if not self.finished:
            for s in self.states.values():
                if s.session:
                    self._interrupt(s, "end_of_video", s.session["last_observed_seconds"])
            self.finished = True
        return self.report()

    @staticmethod
    def _summary(sessions):
        complete = [s for s in sessions if s["status"] == "completed"]
        times = [s["holding_seconds"] for s in complete]
        return {"pickup_count": len(sessions), "putdown_count": len(complete),
                "completed_count": len(complete), "incomplete_count": sum(s["status"] == "incomplete" for s in sessions),
                "total_holding_seconds": round(sum(times), 6),
                "average_holding_seconds": round(sum(times) / len(times), 6) if times else None,
                "max_holding_seconds": max(times, default=None)}

    def report(self):
        rows = [{**s, "observed_holding_seconds": round(max(0, s["last_observed_seconds"] - s["pickup_seconds"]), 6)}
                for s in self.sessions]
        users = sorted({s["user_id"] for s in rows})
        return {"schema_version": "vlx_interactions_v1", "interaction_policy": INTERACTION_POLICY,
                "time_basis": "source_video_pts_seconds",
                "total": self._summary(rows), "sessions": rows, "events": list(self.events),
                "by_object": [{"object_key": key, "object_name": target.label,
                               **self._summary([s for s in rows if s["object_key"] == key])}
                              for key, target in self.targets.items()],
                "by_user": [{"user_id": uid, "user_label": f"用户{uid}",
                             **self._summary([s for s in rows if s["user_id"] == uid])} for uid in users],
                "by_user_object": [{"user_id": uid, "user_label": f"用户{uid}", "object_key": key,
                                    "object_name": self.targets[key].label,
                                    **self._summary([s for s in rows if s["user_id"] == uid and s["object_key"] == key])}
                                   for uid, key in sorted({(s["user_id"], s["object_key"]) for s in rows})]}
