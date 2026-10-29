# Worklog: DPI-01C.3 cam03 Upright Display State

Date: 2026-09-05
Status: `CAM03_UPRIGHT_DISPLAY_STATE_PASS / SURFACE_SEMANTICS_CONFIRMED / FORMAL_PRODUCT_TRACK_READY`

## Implementation

Added optional `product_display_surface.support_contact_points` parsing to the
independent cam03 product configuration and optional support-point enforcement
to `MultiProductTrackRuntime`. The rule uses the median x coordinate at the
lowest mask row, normalized to the 1280x720 frame, and compares it with the
configured upright support point. When configured, a product is `IN_DISPLAY`
only when the existing formal Surface gate and support-point proximity both
pass. Without support points, the legacy overlap behavior is unchanged.

Cam03 support points:

```text
ai_electric_guitar = [793, 520]
ai_guitar         = [880, 528]
```

The physical tabletop remains diagnostic evidence. It is not added as a
Runtime Gate and no legacy cam02 configuration or semantics were changed.

## Real Evidence

The existing real SAM3 propagation artifact for the derived cam03 video
contains 900 frames (`source_seq=1700..2599`, `time_base=1/12800`). The white
guitar action windows are:

```text
stationary_on_stand  1700..1733
just_leaving_stand   1734..1745
held                 1746..2315
returned             2316..2599
```

The black guitar remains stationary for the full bounded range and does not
false-trigger departure. During the held interval the white guitar's support
point is far from `[793,520]`, so the configured rule rejects `IN_DISPLAY` even
when the expanded rectangle overlap is high. Standing and returned masks reach
their configured support points.

Existing artifacts remain at:

`D:\\GJJ\\work\\dpi01c3_cam03_dpi01b_action_propagation\\`

including `real_propagation_overlay.mp4`, `action_window_diagnostic.json`,
`surface_evidence.json`, and `action_window_keyframes.jpg`.

## Verification

Focused upright, action-window, propagation, initialization, and adapter tests:
`33/33 passed`.

The broader previously executed DPI/Stage7C/association/interaction suite
passed `155/155`. `compileall` and `git diff --check` passed. The provider
failure traceback printed by one Stage10 failure-path test is expected; the
test passes.

No Stage9, Person-Product Association, membership, or behavior replay was run.
The new video initialization confirmation file remains `required`; existing
confirmed C.1 masks and the bounded propagation tokens are retained as the
formal track evidence boundary until the configured cam03 mask confirmation is
recorded.

## Qualification

```text
CAM03_UPRIGHT_DISPLAY_STATE_PASS
SURFACE_SEMANTICS_CONFIRMED
FORMAL_PRODUCT_TRACK_READY
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```
