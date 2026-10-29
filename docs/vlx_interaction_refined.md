# Fixed-object video interaction analysis

The independent offline backend labels fixed instances with VLX, follows those
instances with SAM3.1 masks, then computes anonymous user counts and holding
durations from source presentation timestamps. It does not write the database
or change the frontend. The original VLX/CSRT runner remains available.

Use the interaction Python environment and an unused output directory:

```powershell
& 'D:/project/RetailVisionDev/envs/vlx-interaction-py311/Scripts/python.exe' scripts/run_vlx_interaction_refined.py --video 'D:/surveillance_video_raw/example.mp4' --output-dir 'D:/project/RetailVisionDev/runtime/vlx-interaction-video/new_test' --classes 0,1,2
```

Target indices: 0 orange cartoon toy, 1 white egg-shaped toy, 2 pink plush,
3 yellow robot dog, 4 white electric guitar, 5 bee plush. Targets must be
confidently localized in the first observation; unsupported initial mappings
fail explicitly. Omitting `--classes` selects the initial VLX targets.

For previously processed input, replace `--video` with `--recorded-run` pointing
to the directory containing `manifest.json` and `observations.jsonl`. The source
hash is verified, raw model outputs remain untouched, and the new report states
that VLX/pose detections were reused. Mask tracking and user association are
recomputed. The runner uses separate model processes; do not run another GPU
inference worker concurrently on the 16 GB test machine.

Outputs include the full annotated video, timestamped observations, event log,
session durations, per-object totals, and per-user/object totals. Toss/catch is
one session until an observed stable release. Missing evidence remains unknown;
it is not converted into a fabricated putdown. Source face rectangles and
other annotations already embedded in the uploaded recording remain visible.

SAM runs in bounded overlapping windows. Window identities are matched using
observed VLX boxes or the already tracked overlapping source frame. Ambiguous
matches are omitted, not assigned by order. Largest connected mask components
prevent small disconnected artifacts from expanding a box toward another hand.
Temporary body overlap is not a proven handover; sustained exclusive other-user
contact is required. A held object's observed resting origin survives the idle
reference timeout so a long hold can still end and rearm another pickup.

The overlay displays both the item total across users and each user's own count.
Counts and timers at a frame use only observations up to that frame. These are
2D visual estimates; timing precision and completeness still require visual
review of the exported recording, particularly across occlusion and frame gaps.

The white electric guitar uses independent SAM3.1 semantic masks at each sampled
frame, because propagated thin-object masks can drift onto an empty stand. The
initial VLX instance remains the identity anchor. Other guitars observed during
initialization are tracked as competitors. A recorded VLX box is used only when
the independent mask supports it, or for at most one second of overlapping pixel
continuity after semantic confirmation. A nearby strong semantic observation can
recover a short occlusion but is marked discontinuous. A class label alone cannot
replace the target with another guitar. No action timestamps or expected counts
are inputs to these rules.

The refinement configuration allows four seconds for an anonymous person's
temporary occlusion; the independent two-second missing-object timeout remains
unchanged. Pending pickups retain their original hand through bounded uncertainty
without adding hidden confirmation time. After unambiguous same-hand recovery,
continued co-motion can confirm the pickup even if a bystander's wrist is nearby.
The onset and confirmation timestamps are separately available in the event log;
an uncertain onset may appear in the overlay only once confirmation is available.

In addition to mask groups, guitar runs save independent candidates and the
selected geometry under `grounding_4`. The final manifest lists exactly which
groups contributed. Superseded propagation diagnostics do not contribute to counts.
