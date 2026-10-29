"""Conservative contradictions for the six user-confirmed fixed instances.

These checks can veto a label; they never assign a class from color.
"""
import cv2
import numpy as np
from scripts.probe_vlx_seek import iou


def same_region(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    areas = [(x[2]-x[0])*(x[3]-x[1]) for x in (a, b)]
    return (iou(a, b) >= .7 or (min(areas) > 0 and min(areas) >= .25*max(areas)
                               and intersection >= .9*min(areas)))


def claims_existing_track(candidate, existing):
    """A shifted proposal must not steal the center of a verified instance.

    This is stricter than comparing two new raw detections: a continuing pixel
    track supplies temporal identity, whereas a conflicting new label does not.
    """
    centers = [((b[0]+b[2])/2, (b[1]+b[3])/2) for b in (candidate, existing)]
    mutual = all(b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]
                 for b, p in zip((candidate, existing), reversed(centers)))
    intersection = max(0, min(candidate[2], existing[2])-max(candidate[0], existing[0])) * max(0, min(candidate[3], existing[3])-max(candidate[1], existing[1]))
    smaller = min((b[2]-b[0])*(b[3]-b[1]) for b in (candidate, existing))
    return same_region(candidate, existing) or iou(candidate, existing) >= .5 or (mutual and intersection >= .5*smaller)


def appearance_contradiction(key, box, frame):
    # The user explicitly identified this instance as pink. A largely warm
    # yellow crop dominated by yellow rather than pink contradicts it (e.g. the
    # bee's whole body + antenna). Partial/neutral crops are left undecided.
    if key != "pink_plush":
        return None
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(v)) for v in box]
    crop = frame[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if not crop.size:
        return None
    hue, saturation, value = cv2.split(cv2.cvtColor(crop, cv2.COLOR_BGR2HSV))
    warm = float(np.mean((hue >= 10) & (hue <= 40) & (saturation >= 100) & (value >= 70)))
    pink = float(np.mean(((hue >= 140) | (hue <= 7)) & (saturation >= 15) & (value >= 70)))
    if warm >= .12 and warm > 1.25*pink:
        return {"reason": "pink_target_dominated_by_yellow", "warm_fraction": warm, "pink_fraction": pink}
    return None
