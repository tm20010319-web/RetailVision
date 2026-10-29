# cam03 fresh face preflight

Status: `RECOGNITION ACCEPTED / EVENT-TIME HISTORY RESOLVED / NEW ACTION VIDEO PENDING`

The user triggered recognition after the explicit 0.6 receiver restart. This
entry records actual read-only results, not a new identity implementation.

## Actual result

Two fresh cam03 recognitions arrived at local 14:19:51 and 14:24:34 on September
7. Their Event Times are 14:19:49 and 14:24:33; similarity values are 0.84495529
and 0.79945641. Both pass the existing acceptance policy at the requested 0.6.
Both values also exceed the original 0.75 similarity cutoff; no change back to
0.75 was made. The existing historical resolver independently resolves both
events to `member-gaojunjie`, revision `325e184aea6c49d8a4d09856787ff7af`.
No current mapping was substituted for Event-Time history and no revision was
created or backdated.

Each has a parsed face bbox and an existing 1920x1088 background image. The
parsed face camera field is null; device provenance is the cam03 inbox row.
Parsed coordinates and a present background are not qualified geometry.

The actual `DurablePushInboxRecognitionProvider` queried UTC
`[2026-09-07T06:24:23, 2026-09-07T06:24:43)` using `PushCoverageStore`, the
existing acceptance policy and historical resolver. Diagnostic result:
`complete`, Coverage complete, one raw record, one accepted evidence record.
This 20-second preflight window is NOT a video window or formal capability
qualification. No profile resolution was supplied in this diagnostic query.

Both receipt delays (2.809914s and 1.461599s) are recorded, not used to calibrate
or correct a video clock. A new recording still needs its actual OSD/time and
PTS evidence. Coverage for this preflight does not prove a future recording's
Coverage.

At UTC 06:31:42 the receiver remains PID 41520 on 8081, with accepted/persisted
heartbeat at UTC 06:31:37.249684. It is left running. This process collects Push;
it has NOT started a new video recording or the behavior analysis pipeline.

## Current five-stage result

1. Recognition acceptance: PASS for the two new records at 0.6.
2. Event-Time historical mapping: resolved for both.
3. Face-to-operating-person association: not executed, new video/Run needed.
4. Track identity aggregation: not executed, no Track ID assumed.
5. Confirmed member behavior: 0 produced; no formal BehaviorEvent write.

Formal cam03 profile/geometry qualifications remain separate outstanding gates.
No source/video, old Run, threshold default, SAM3, Stage8 or Stage9 was modified.
No code changed and no new test-suite PASS is claimed in this preflight turn.

Evidence: `reports/cam03_20260907_onsite_prepare/face_preflight_20260907_142433.json`
and its adjacent `face_preflight_20260907_142433.sha256.json`. Earlier reports and
hash manifests remain unchanged.

## Next onsite action

One member can now record a short DIAGNOSTIC action sample: start cam03 video,
retain at least five seconds, face cam03 again so Recognition occurs inside that
video, pick up one product, hold, put it back, and retain at least five seconds
at the end. Save the MP4 plus displayed start/end clock evidence. Leave this
Push receiver running through at least one heartbeat after recording ends.
Use existing source-bound initialization, capture and identity CLIs for that
new source's own Run. Do not use these earlier face events for a later video
that excludes them. Recheck Coverage on the actual final recording interval.
