# cam03 Profile and Calibration Reuse Audit

Status: `LOADER WIRING FIXED / CAM03 APPLICABLE CALIBRATION NOT LOCATED`

Followed the authority registry and current handoff into the 20260826 external
archive. Fully read the archived Face Recording Spatial Geometry and Capability
Profile real-device validation reports, and checked their current JSON evidence.
These are real verified cam02 artifacts (1920x1088 → 800x448, serial-scoped).
Existing cam02 profile construction exactly matches the archived content hash.
Comparing that profile to persisted cam03 metadata with the existing Resolver
returns `serial_scope_mismatch`, despite identical model and firmware.

Existing cam03 video → analysis resize (2560x1440 → 1280x720) and merchandise
Surface configuration were found. Neither is a FaceReco-to-recording FOV
qualification. No applicable cam03 face calibration or full capability profile
was found in the bounded indexed search. Current profile path is unconfigured.
No claim is made that the coordinate/association/aggregation/projection modules
are missing; their existing implementations are explicitly reused unchanged.

Fixed `audit_cam03_identity_readiness.py`'s empty profile resolver and unfinished
placeholder loader. It now shares full-profile deserialization with the existing
DPI-01E.2 runner, uses the parsed camera configuration, preserves/checks the
stored profile hash, and differentiates loading errors from qualification errors.
The older runner's Coverage auditor now uses the same resolver as well.
`build_recognition_runtime` now passes existing profile geometry parameters into
OfflineSpatialGeometry, rather than leaving geometry unset. No new transform,
association, aggregation or projection algorithm was implemented.

Readiness now calls existing runtime geometry checks against the actual face
frame (1920x1088) and analysis frame (1280x720), separately reporting decoded
video size (2560x1440). A valid applicable profile selects the existing Provider;
an unavailable profile remains explicitly diagnostic only. There is no real
applicable cam03 profile to execute the conditional new-Run identity path yet.

The diagnosing-bugs feedback loop reproduced the configured-file load failure
(NameError in the unfinished placeholder), then passed after the repair.
Tests cover actual loading, custom environment-name priority, stored hash,
abbreviated reports, malformed/missing files, wrong ID/mode/serial and runtime
geometry mismatch, plus existing Provider/geometry reuse. No business DB writes,
SAM runs, threshold changes or frozen event changes occurred in this turn.

Verification: **183 tests passed, 22 subtests passed**, including the new loading
tests and prior DPI-01E.2, identity, media, behavior, pipeline and member API suites.

Evidence and explicit four-way conclusions:
`reports/cam03_20260907_profile_reuse_audit/REUSE_AUDIT.md`.
Actual post-fix readiness:
`reports/cam03_20260907_profile_reuse_audit/readiness_final.json`.
Old readiness reports remain intact; their empty-set `profile_not_found` result
must not be treated as evidence that a configured profile was searched.

To finish applicability review, supply an existing cam03 profile/validation
package path if stored elsewhere; otherwise the missing artifact is a same-time
cam03 Recognition BackgroundImage / recording PTS frame pair and associated
event/time/dimension evidence. Reuse the existing qualification mechanism.
