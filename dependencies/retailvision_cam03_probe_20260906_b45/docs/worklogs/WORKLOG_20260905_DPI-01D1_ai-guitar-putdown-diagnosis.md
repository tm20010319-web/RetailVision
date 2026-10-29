# Worklog: DPI-01D.1 AI Guitar PUT_DOWN Diagnosis

Date: 2026-09-05
Status: `DIAGNOSIS COMPLETE / NO SOFTWARE PUT_DOWN FIX JUSTIFIED`

## Scope

The existing cam03 Offline Stage5/7C/8/9 chain was replayed in
`agents-py311` against the confirmed derived video. No Stage9, Person-Product,
identity, Surface, support point, or Track-loss threshold was changed.

## Evidence

Input video:

`D:\GJJ\work\video\dpi01b_2026_09_04_16_43_24_1280x720.mp4`

Replay and diagnostics:

- `D:\GJJ\work\dpi01d_ai_guitar_putdown_diagnostics\replay_dpi01d1.json`
- `D:\GJJ\work\dpi01d_ai_guitar_putdown_diagnostics\dpi01d_window_diagnostics.json`
- `D:\GJJ\work\dpi01d_ai_guitar_putdown_diagnostics\dpi01d_ai_guitar_120s_eof_diagnostic.mp4`

SHA-256:

- replay JSON: `BD82A26FF353B36BA2A843453DB18847209E84FA903253FF9FBE392798ADBB0B`
- window diagnostics: `326C5B6B84E0266311AB001E6992A29DF33D90D822893CB9D2F0263A09BD6403`
- diagnostic video: `F3745C055F2644CFA8C84F4DBA0F36D2C7DC109A29FCF523D5F89DA7A92A7E04`

The detailed runner now preserves Stage 7C candidate ID, Track status/token,
missed-window count, display reason, surface overlap, support-contact values,
Person-Product association, Stage9 interaction state, and real SAM masks for
the diagnostic overlay. This is diagnostic-only evidence; normal Stage 8/9
observation contracts remain unchanged.

## Layered finding

`ai_guitar` was picked up at `124.44115361977634s` and retained the original
Track token. After the hold, masks reappeared at `129.4304414361389s` through
`132.424014125956s`, but their dynamic support contact remained outside the
approved point `[0.6875, 0.7333333333333334]` with distances about `0.098` to
`0.196`, above tolerance `0.08`. The last reliable mask was at
`137.20541494997056s`; its distance was `0.23399017162639912` and its small
candidate region was not a fixed-position guitar mask. From `138.03696291936433s`
to EOF (`139.2011300765156s`) the Track was `MISSING_PENDING` with no Mask.

The base Surface overlap was not the failing gate during the held/recovery
frames; the fixed-support gate failed. Product Track remained the same token,
never entered `LOST`, and was not associated with the other guitar. Stage9
remained `HELD` and never received `OUTSIDE_DISPLAY -> IN_DISPLAY`.

No `approximate_put_down_pts` can be supported by this video replay. The
evidence therefore does not justify changing Stage9 or synthesizing a
`PUT_DOWN`. A new operator-confirmed video fact showing the AI guitar stably
back at its own fixed support point is required before a recovery-layer fix can
be selected.

## Result

```text
DPI_01D_PARTIAL_PASS
AI_ELECTRIC_GUITAR_BEHAVIOR_PASS
AI_GUITAR_PUT_DOWN_MISSING
DPI_01E_MEMBER_BINDING_NOT_STARTED
```

The prior electric-guitar cycles remain unchanged. Full dual-product behavior
replay remains pending.
