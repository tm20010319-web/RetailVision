# VLX guitar holding timer regression

Date: 2026-10-27
Status: REPORTED TIMERS FIXED / REGRESSION PASS / ACTION ACCURACY NOT ACCEPTED

The user reported missing holding time while the blue-shirt user holds the
white guitar at source PTS 113.641s, then supplied a second screenshot of the
black-shirt user holding it around 159s. This continues the authorized backend
pickup/putdown fix; no new frontend, DB, identity or production scope.

The diagnosing-bugs workflow reproduced both failures using unmodified model
observations from the third recording. Fixtures preserve all people and only
remove unrelated object classes. The blue-user case needs the observed origin,
contact, partial-box interruption and subsequent holding interval; the second
case includes resting, nearby touch, intervening operation and the next lift.
Both currently display `unknown` with no elapsed timer.

Command: `python -m pytest test/test_vlx_interaction_video.py -k real_guitar_lift -q`
Baseline: 2 failed (`unknown != holding`), 30 deselected, about two seconds.

Ranked hypotheses and predictions:

1. Model box extent changes contaminate center displacement and clear the
   resting origin. Comparing movement of both box edges should reject an
   extent-only change while retaining actual translation.
2. Choosing one closest wrist discards a second observed contacting hand.
   Independently retaining both hands should detect a lift by either one,
   while a hand first appearing after a discontinuity cannot replace one.
3. A short lost origin prevents subsequent operations even when the same
   previously observed resting pose is visible again. Re-observing that pose
   for the baseline window should rearm a new action without treating every
   stationary held object as resting or fabricating a pickup on reacquisition.

Before-change code and diagnostics are preserved outside the repository at
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/diagnostics_guitar_timer_20261027/`.
All prior final artifacts stay immutable. The live dual preview remains running;
recorded-evidence rule replay needs no model GPU or preview interruption.

## Implemented and verified

Policy: `observed_extent_rest_pose_recovery_v4`.

- Estimate translation from the motion shared by opposite box edges, rather
  than box-center shifts caused by changing object extent.
- Retain independently observed contact origins for both wrists before a
  pickup candidate. Pin a pending candidate to one wrist and one user.
- Preserve contact across a short overlapping partial-box reacquisition at the
  known origin; this does not itself provide pickup confirmation.
- Remember observed resting poses for 30 seconds. Rearm only after matching all
  box edges continuously for the baseline window; subsequent contact and motion
  remain necessary. No predefined table or ROI, no initial-held pickup invented.
- Place a readable user / elapsed holding label beside the object box as well
  as in the existing bottom summary.

The original two fixture expectations were refined after source-frame review:
the blue screenshot is the second lift around 111s, not the earlier 98s lift;
the black case requires the earlier full resting evidence at 120–125s, omitted
by the first 126s crop. Initial fixtures and refinements remain in diagnostics.
Both refined fixtures were rerun against the saved v3 implementation and still
returned `unknown / None`; current code passes. This avoids defining a false
15-second continuous hold across the blue user's visible release and next lift.

`python -m pytest test/test_vlx_interaction_video.py test/test_vlx_live_tracking.py test/test_vlx_dual_support.py -q`
passes **65 tests** (38 interaction/file tests, 27 existing support tests), with
the existing SQLAlchemy deprecation warning only. New cases cover the two real
traces, second-hand contact, size-only changes, pose expiry/nonmatch, no pickup
on reacquisition alone, and a different user arriving during pending pickup.

Full existing-trace comparisons preserve immutable model evidence:

| Recording | Before v3 | After v4 |
| --- | --- | --- |
| 17:03:42 | 3 pickups / 2 putdowns, 15.841s complete | 2 / 2, 15.841s; suspected false open guitar removed |
| 17:52:03 | 1 / 1, 7.880s | 1 / 1, 7.880s |
| 19:16:36 | 4 / 3, 18.400s | 6 / 4, 25.800s |

Final output:
`D:/project/RetailVisionDev/runtime/vlx-interaction-video/test_20261027_191636_v4_guitar_replay`.
The source v2 model inference is reused without any new model inference. All 900
source frames/timestamps/object/person/diagnostic observations match exactly,
with source hash, unchanged inference configuration and code hashes recorded.
The new rule-only parameter is explicit in manifest provenance.

At source 113.641s: guitar, user 2, holding **2.6s**, pickup 111.041s. The machine
closes this session at 118.441s, 7.4s duration; the release is visibly late.
At source 159.641s: guitar, user 6, holding **5.8s**, pickup 153.841s. The session
later becomes incomplete after user tracking loss and has uncertain attribution;
no putdown or complete duration is synthesized.

The full 183.8s / 919-frame H.264 video and combined 26s / 130-frame example clip
passed full ffmpeg decoding. The clip joins source 108–121s and 151–164s, retaining
original PTS labels. Both exported screenshot frames were visually inspected;
the green labels are readable beside the guitar and show the expected timers.
See output `REVIEW.md` and `verification.json`.

## Remaining limitations

The earlier blue-user guitar lift around 98s and gray-user operations around
140s remain missed. Partial-box rotation and noisy wrist trajectories still
prevent sustained confirmation; this is not general action-accuracy acceptance.
Release timing, ambiguous overlap, fixed-instance grounding and anonymous-ID
fragmentation remain unresolved. The green user label identifies the pickup's
initiating track; it is not proof that later ownership remains unambiguous.
Recent matching resting poses are a 2D estimate and can confuse similar objects
at the same position. Model outputs were not manually corrected for this replay.

No production debug instrumentation remains. Diagnostic scripts/prototypes are
confined to the named external diagnostics directory. No SAM3, DB or frontend
changes. Dual preview PID 54456 was never interrupted during this follow-up;
both feeds remained fresh and GUI targets [1, 2], revision 3, were preserved.
