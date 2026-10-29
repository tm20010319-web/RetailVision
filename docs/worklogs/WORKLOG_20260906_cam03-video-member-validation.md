# WORKLOG 20260906 cam03 video member validation

Status: `BEHAVIOR REPLAY PASS / MEMBER IDENTITY BLOCKED`

## Scope

Validated only `D:/GJJ/work/video/2026_09_06_19_16_00.mp4` (SHA-256 starts
`48c1cdbb`, full hash is recorded in the report). The frozen DPI-01E.2
12-event replay was not modified or merged.

## Initialization failure and minimal repair

The first attempt used the existing cam03 two-product atomic initialization
contract. `ai_electric_guitar` produced a valid mask, while the `ai_guitar`
prompt bbox included adjacent stand/background and SAM returned
`CANDIDATE_AMBIGUOUS`, which the existing validator correctly surfaced as
`MASK_EMPTY` for the atomic batch.

The repair only tightened the `ai_guitar` bbox to the visible guitar body
(`860,380,905,505`) and retained the existing points, gates, thresholds, and
same-session/two-target policy. Both masks then passed the existing single
instance, connected-component, display-surface, and overlap checks. No
production code or frozen behavior algorithm was changed.

## Replay result

The existing offline Stage5/7C/8/9 entry completed against the new source:

- 2748 source frames, complete PTS timeline;
- 6 Stage9 facts: two `PICK_UP -> HOLDING -> PUT_DOWN` cycles;
- all 6 facts are `ai_electric_guitar` / Person Track 2;
- no new formal `BehaviorEvent` rows were written;
- overlay video is retained under `reports/cam03_20260906_behavior_validation/replay/`.

## Recognition result

Read-only cam03 Coverage for `[2026-09-06T11:11:00, 2026-09-06T11:12:53.201)`
is complete. The inbox contains one in-scope record at `11:11:40`, status
`ignored`, similarity `0.66492233`, and no member mapping. The cam03 capability
profile resolver still reports `profile_not_found`, so the formal Provider and
identity projection remain blocked. Person Track 2 is `unavailable`; confirmed
member behavior count is zero and all 6 behavior facts remain anonymous or
unavailable.

Artifacts are in
`reports/cam03_20260906_behavior_validation/`, including the source-bound
initialization config/confirmation, replay output, identity summary, behavior
facts, and SHA-256 manifest.

## Mock-only visualization

For operator review, a copy of the SAM3 overlay was generated at
`replay/sam3_mock_member_overlay.mp4`. It labels Person Track 2 with the
ignored Recognition candidate `member-wudongxin` at similarity `0.66492233`
and labels Person Track 1 as having no in-window evidence. Both labels are
explicitly mock/not-confirmed; no identity or database state was changed.
