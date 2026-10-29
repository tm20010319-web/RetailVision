# cam03 new action diagnostic run

Status: `BEHAVIOR REPLAY PASS / DIAGNOSTIC IDENTITY PROBABLE / FORMAL MEMBER BINDING BLOCKED`

## Scope

This entry records the completed independent cam03 Run for the user-confirmed
September 7 recording. It does not modify SAM3, Stage8, Stage9, Track tokens,
similarity defaults, formal qualification, or any prior video/run artifacts.

```text
analysis_run_id     = cam03-a824bd52328e4e5c8354c1fe6708eb32
camera_id           = cam03
store_id            = store-1
source SHA-256      = b7133e373c3d072252bbd2c771bcd0b6e4d9a2fdb8d7638c6982148c24500490
local source start  = 2026-09-07T14:41:00+08:00
timezone            = Asia/Shanghai
UTC query interval  = [2026-09-07T06:41:00, 2026-09-07T06:44:14.360000)
duration            = 194.360 seconds
PTS basis           = original 1/90000 presentation timeline
```

The recording start is the operator-confirmed first-frame OSD time; the file
name is export time. The presentation timeline is verified from original PTS.
The sampler recorded 15 real sampling gaps and created no synthetic frames.

## Behavior Result

The source-bound, operator-confirmed product masks were used for a separate
Run. Existing frozen behavior stages processed 957 of 957 samples with
complete provider sample coverage and emitted six Stage9 facts, all on Person
Track 1:

| product | interaction | PTS | duration |
| --- | --- | --- | --- |
| `ai_electric_guitar` | PICK_UP / HOLDING | 33.200 | 21.440 s |
| `ai_electric_guitar` | PUT_DOWN | 54.640 | |
| `ai_guitar` | PICK_UP / HOLDING | 137.201 | 6.400 s |
| `ai_guitar` | PUT_DOWN | 143.601 | |

The detection video is
[`sam3_behavior_overlay.mp4`](../../reports/cam03_20260907_144722_run/sam3_behavior_overlay.mp4).

## Identity Result

The normal diagnostic identity CLI executed a read-only
`DurablePushInboxRecognitionProvider` query over the exact Run interval.
Coverage is complete; five Recognition records were evaluated and one passed
the explicit on-site diagnostic threshold of `0.6`. The existing Event-Time
historical mapping resolved the accepted Recognition evidence.

The behavior-bearing Track 1 is `probable`, not `confirmed`; its `member_id`
therefore remains null. The other observed tracks are `unmatched`. The
candidate identity and face imagery remain restricted evidence in the identity
artifact and are not copied into this worklog or a public behavior fact.

Although diagnostic overlays visually support the Track 1 association, they
are explicitly unqualified: the cam03 capability profile has
`profile_path_not_configured` and the candidate full-frame resize has
`vertical_padding_unverified`. The existing aggregation rule correctly stops
before `confirmed`; no manual identity was assigned and no status was promoted
because of the 0.6 diagnostic policy.

## Publication Boundary

```text
confirmed member behavior facts = 0
anonymous behavior facts        = 6
formal BehaviorEvent writes     = 0
publication attempted           = false
publication eligible            = false
database mode                   = read-only / query_only
```

This is a completed behavior and diagnostic identity run, not completion of
formal member association. A qualified cam03 capability profile and verified
face-to-recording geometry remain required before an independently valid Run
can emit confirmed member behavior facts. The next work must address those
formal identity gates, not lower the default similarity threshold or alter the
behavior pipeline.

## Artifacts

- [Diagnostic summary](../../reports/cam03_20260907_144722_identity/diagnostic_run_summary.json)
- [Final hash manifest](../../reports/cam03_20260907_144722_identity/finalization_artifact_sha256.json)
- [Run manifest](../../reports/cam03_20260907_144722_run/source_run_manifest.json)
- [Identity report](../../reports/cam03_20260907_144722_identity/identity_report.json)
- [Track associations](../../reports/cam03_20260907_144722_identity/track_identity_associations.jsonl)
