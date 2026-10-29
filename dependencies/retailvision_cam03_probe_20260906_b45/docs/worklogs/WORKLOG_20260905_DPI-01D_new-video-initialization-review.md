 # Worklog: DPI-01D New Video Full Replay

Date: 2026-09-05
Status: `COMPLETED / FULL DUAL-PRODUCT REPLAY PASS`

## Scope

The new source video `D:/GJJ/work/video/2026_09_05_20_33_56.mp4` was bound to
an independent cam03 configuration. The prior source's Mask confirmation was
not reused. A same-frame, same-session real SAM3 initialization was confirmed
for this run, then the existing `GoldenMp4Analyzer -> SAM3 Stage5/7C ->
Stage8 -> Stage9` chain was replayed from source_seq=0 to EOF.

## Evidence

- Source SHA-256: `921f6ec3881a8222ee269c767a6a3bab276e95be337b95272a0ad53a9afd2920`
- Replay: 394/394 samples, PTS available, provider coverage `complete`, terminal pending `0`.
- Replay JSON: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/full_replay_rerun2.json`
- Window diagnostics: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/full_diagnostics_rerun2/dpi01d_window_diagnostics.json`
- Final report: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/FULL_REPLAY_REPORT.md`
- Chinese diagnostic video: `D:/GJJ/work/dpi01d_cam03_20260905_203356_review/full_diagnostics_rerun2/cam03_20260905_203356_report_video.mp4`

The report video follows the reference presentation direction while retaining
real Mask, Candidate, Track, support, display-gate, Stage9 and PTS diagnostics.

## Result

The accepted reruns produced the same normalized event sequence. The final
sequence contains 12 Stage9 events: three complete cycles for
`ai_electric_guitar` and one complete cycle for `ai_guitar`. All cycles are
`PICK_UP -> HOLDING -> PUT_DOWN`; holding durations are computed from source
PTS. Product tokens remain unique and continuous, and Person Track association
does not cross products.

A first exploratory run emitted an extra EOF pickup for the black guitar. It
was not accepted as evidence. The subsequent two complete reruns agreed on the
12-event sequence; no business threshold was changed to remove the discrepancy.

## Verification

Focused suite: `81 passed, 5 subtests passed`, one existing SQLAlchemy warning.
No Stage9, Person-Product Association, member identity, MCP, database or cam02
semantic code was changed for this replay. The next phase is DPI-01E offline
member binding; this entry makes no member identity claim.

```text
DPI_01D_DUAL_PRODUCT_BEHAVIOR_REPLAY_PASS
PERSON_PRODUCT_ASSOCIATION_PASS
STAGE9_DUAL_PRODUCT_BEHAVIOR_PASS
READY_FOR_DPI_01E_OFFLINE_MEMBER_BINDING
```
