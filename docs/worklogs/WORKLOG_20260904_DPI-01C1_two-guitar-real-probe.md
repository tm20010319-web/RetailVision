# Worklog: DPI-01C.1 cam03 Two-Guitar Real Probe

Date: 2026-09-04
Status: `DPI-01C1 TWO-GUITAR INITIALIZATION PASS / SURFACE SCOPE DEFERRED`

## Scope

The operator deferred the yellow foreground robot and requested only the two
black/white guitars on the central blue tabletop.  This run does not change the
legacy `cam02` meanings, Stage9, behavior state machines, association logic,
membership, MCP, or RTSP behavior.

## Configuration

Independent configuration:

```text
product_cameras.cam03.dpi01c.guitars.stage7c.json
```

```text
camera_id = cam03
selected_product_keys = ai_electric_guitar, ai_guitar
surface = product_display_surface (central tabletop candidate)
source_seq = 0
source_pts = 0
time_base = 1/90000
frame_sha256 = 9ded0be914cc340112ae868a9958ee50ff4911924e2a0d34b30832a830e8f800
```

The two-product count is an explicit bounded validation batch because the
operator deferred `ai_robot`; it is not a change to the default DPI-01A
three-to-five product policy.

## Real SAM3 Evidence

Run output:

```text
D:\\GJJ\\work\\dpi01c_cam03_guitars_run\\two_guitars_draft_r2
```

Both prompts used the same resized initialization frame and one SAM Session.
The real bbox path returned backend `obj_id=0` for both products.  The Adapter
now uses the requested unique ID only after that backend collision, and requires
the point response to contain it.  The focused Adapter/domain/runtime tests
pass `69/69`.  The full repository discovery run reached 1205 tests but has
five unrelated failures outside this DPI scope (device-recognition end-to-end
and person-role tests).

| product_key | target | candidate_id | mask_area | surface_overlap | mask_sha256 |
| --- | --- | ---: | ---: | ---: | --- |
| `ai_electric_guitar` | white guitar | 0 | 3245 | 1.0 | `5fc75b1745ad740c86eb3548a4cd76233261cda2dcc4af34c735bce469198808` |
| `ai_guitar` | black guitar | 2 | 2324 | 1.0 | `bab2022d7ae5fc37c4acfb12997a8ab2855f8b6bb935c5b38703bfe74231a44c` |

The masks have no pairwise intersection, each has one significant connected
component, and both positive/negative point checks pass.  The operator
confirmed both masks and the batch was committed atomically.

Artifacts:

```text
multi_product_mask_overlay.jpg
initialization_frame.jpg
mask_ai_electric_guitar.png
mask_ai_guitar.png
initialization_mapping.json
initialization_diagnostic.json
short_window_continuity.json
```

## Track Evidence

The one atomic `MultiProductTrackRuntime.initialize_assignments` call created
one segment per product key.  Two bounded Runtime Rolling Windows retained the
same tokens, stable candidate mapping, `ACTIVE` status, successful association,
and no ambiguity.  This continuity artifact uses the initialized masks as a
Runtime association harness; it is not a complete video propagation replay.

```text
DPI_01C_REAL_SAM3_ADAPTER_PASS
DENSE_INITIALIZATION_REAL_MASK_PASS
MULTI_PRODUCT_TRACK_INITIALIZATION_PASS
SHORT_WINDOW_TRACK_CONTINUITY_PASS
FULL_BEHAVIOR_REPLAY_PENDING
NOT_FULLY_CLOSED
```

The yellow robot and the question of whether additional formal display
surfaces should be configured remain deferred.  This run used the central
`product_display_surface` candidate as the bounded scope.  No behavior event
or production validation claim is made.
