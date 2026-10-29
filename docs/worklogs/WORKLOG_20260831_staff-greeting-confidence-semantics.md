# Worklog: Staff Greeting P1.1 Confidence Semantics

Date: 2026-08-31
Status: `COMPLETED / SUSTAINED-WINDOW CONFIDENCE VALIDATED`

## Scope

This phase changes only Official V1 terminal confidence aggregation from a
Pair-lifetime cumulative minimum to the minimum over the final continuous
qualifying sustained window. `confirmed_confidence` remains `0.7`; all Greeting
behavior gates and Offline/Realtime boundaries remain unchanged.

## Implementation

- `_GreetingPair` now distinguishes `lifetime_confidence` from
  `qualifying_confidence`.
- A qualifying confidence window starts with `within_since` when Customer area,
  Staff eligibility, latched approach, and Greeting distance are all valid.
- Every branch that resets `within_since` also clears
  `qualifying_confidence`.
- Terminal assessment, `event.confidence`, and the existing
  `details.greeting_confidence` value use `qualifying_confidence`.
- Lifetime confidence remains diagnostic-only and is not added to the formal
  Artifact schema.

## Evidence

- Existing P1 trace lifetime minimum: `0.5186229348182678` at PTS
  `68.36283185840708`.
- Final qualifying window: PTS `69.02654867256638` to
  `70.06360619469027`.
- Final qualifying minimum: `0.7185347080230713` at PTS
  `69.0680309734513`.
- Deterministic Cases A-F and the combined focused suite pass `120/120`.
- Controlled CUDA replay finalized as analysis run
  `7c01a9ac41eb4a0288f5bbc2b39849a7`, with verified PTS and `1808/1808`
  frames. Summary: `total=2`, `confirmed=1`, `uncertain=0`, `cancelled=1`.
- The target Track13/Track24 Cycle naturally finishes `confirmed` with
  `event.confidence=0.7185347080230713`.

Detailed evidence is recorded in
`outputs/staff_greeting_p1_1_20260831/P1_1_CONFIDENCE_SEMANTICS_REPORT.md`
and its JSON companion.

## Boundary Confirmation

No threshold was lowered. No distance, duration, area, approach, Staff Role,
tracking, PTS, EOF, Artifact lifecycle, schema shape, identity, or realtime
flag changed. No video-specific branch was introduced.
