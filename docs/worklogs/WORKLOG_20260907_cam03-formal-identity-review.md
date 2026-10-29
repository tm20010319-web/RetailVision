# cam03 formal identity review for fixed new Run

Status: `IDENTITY-ONLY RECHECK PASS / FORMAL PROFILE-GEOMETRY BLOCKED`

## Fixed input

This review is bound to Run `cam03-a824bd52328e4e5c8354c1fe6708eb32`, source
SHA-256 `b7133e373c3d072252bbd2c771bcd0b6e4d9a2fdb8d7638c6982148c24500490`,
and its existing six Stage9 facts. Existing person snapshots, product tracks,
PTS, and raw behavior evidence were reused. SAM3 and behavior stages were not
rerun or modified.

## Profile and geometry

The normal cam03 configuration still resolves `p6s_cam03_offline_identity`
with `profile_path_not_configured`. The verified cam02 profile was not copied
or relabeled: its serial-bound scope is cam02 and the resolver rejects it for
cam03 with `serial_scope_mismatch`.

The existing diagnostic data has FaceReco dimensions 1920×1088 and Run
recording/analysis dimensions 2560×1440 → 1280×720. The existing converter is
full-frame resize. The aspect-ratio calculation yields a candidate 8-pixel
vertical discrepancy (`1920 / (1280 / 720) = 1080; 1088 - 1080 = 8`). This is
why the binder emits `vertical_padding_unverified`; it does not prove actual
padding. Current material cannot distinguish padding from crop or another
vendor presentation convention, so geometry remains unqualified.

Required field evidence is a complete cam03 profile path/content hash, a cam03
Recognition BackgroundImage, a same-time or PTS-matched recording frame, and
documented crop/padding/mirror/rotation semantics. No new calibration system
was added.

## Formal-threshold identity recheck

The existing identity CLI was run read-only against the same Run and person
snapshots with the formal 0.75 Recognition policy. Five records were queried,
one was accepted at similarity 0.75773224, and Coverage was complete. Track 1
remains `probable` with `member_id = null`; other tracks remain `unmatched`.

The blockers are therefore the missing cam03 profile and unqualified geometry,
not just similarity. The existing projector produced six anonymous facts,
zero confirmed member facts, and no formal BehaviorEvent writes. No probable
status was promoted.

Evidence: [formal review JSON](../../reports/cam03_20260907_144722_identity_formal-review/formal_identity_review.json) and [identity report](../../reports/cam03_20260907_144722_identity_formal-review/identity_report.json).
