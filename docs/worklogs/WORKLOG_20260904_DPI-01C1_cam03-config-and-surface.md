# Worklog: DPI-01C.1 cam03 Configuration And Surface Candidate

Date: 2026-09-04
Status: `REAL SINGLE-PROBE PASS / OPERATOR MASK CONFIRMATION PENDING`

## Operator Inputs

```text
logical_camera_id = cam03
product_key       = ai_electric_guitar / ai_guitar / ai_robot
product_name      = AI电吉他 / AI吉他 / AI机器人
product_type      = electric_guitar / guitar / robot
```

SAM3 runtime and checkpoint references are local-only:

```text
runtime_root  = D:\\GJJ\\work\\sam3
checkpoint    = C:\\Users\\Administrator\\.cache\\huggingface\\hub\\models--facebook--sam3.1\\snapshots\\daa63191845a41281374e725f4c9e51c7a824460\\sam3.1_multiplex.pt
```

## Video Preflight

Input video (original preserved):

```text
D:\\GJJ\\work\\video\\2026_09_04_18_17_36.mp4
```

```text
source_size       = 2560x1440
coordinate_size   = 1280x720 (in-memory full-frame resize only)
decoded_frames    = 1028
duration          = 43.120000 seconds
time_base         = 1/90000
first_pts         = 0
last_pts          = 3877200
all_pts_positive  = true
source_sha256     = 2d77d07b8f2cc7040ed5218cd0766b19fc9a8561b713498cf1aa1fe212ee2d49
```

No re-encoding was performed and no RTSP connection was attempted.

## Surface Candidate

`source_seq=0`, `source_pts=0` is an unobstructed frame. The candidate
`product_display_surface` is the central blue tabletop, with pixel points:

```text
[(520,454), (952,452), (968,526), (521,531)]
```

The resized frame hash is:

```text
9ded0be914cc340112ae868a9958ee50ff4911924e2a0d34b30832a830e8f800
```

Overlay artifact:

```text
D:\\GJJ\\work\\dpi01c_cam03_review\\new_video_survey\\product_display_surface_overlay_cam03_seq0000.jpg
```

The central tabletop is unobstructed. The yellow robot visible in the video is
on a separate foreground table, not inside this candidate surface. The operator
must confirm that the formal target set is located on this one central surface;
otherwise the run is fail-closed and requires a revised placement/frame.

## Real Single-Product Probe

The first controlled Probe used the right white guitar as the provisional
`ai_electric_guitar` target:

```text
source_seq       = 0
source_pts       = 0
bbox             = [760, 300, 845, 500]
positive_points  = [(803,458)]
negative_points  = [(858,450)]
requested_obj_id = 1
```

Evidence directory:

```text
D:\\GJJ\\work\\dpi01c_cam03_guitars_run\\right_white_guitar\\dpi01c_real_sam3_initialization
```

The real Adapter returned one candidate and one non-empty `720x1280` Mask:

```text
adapter_status  = PASS
sam_version     = 0.1.0
candidate_count = 1
mask_area       = 3245
mask_sha256     = 5fc75b1745ad740c86eb3548a4cd76233261cda2dcc4af34c735bce469198808
call_sequence   = bbox, points
session_closed  = true
```

The Overlay and JSON are pending explicit operator Mask confirmation. No
Product Track has been created from this Probe.

## Configuration

Independent draft configuration:

```text
product_cameras.cam03.dpi01c.stage7c.json
```

It contains `cam03`, the three confirmed product keys, the new Surface
candidate, the exact source-frame provenance, and safe local SAM3 references.
The legacy `cam02` configuration and its A/B meanings remain unchanged.

## Validation Boundary

One real single-product SAM3 Mask has been produced, but no operator-confirmed
Mask, Product Track, Rolling Window, or behavior event exists yet. The next
authorized step is confirmation of the Probe Overlay, followed by the second
guitar and the bounded two-product atomic initialization. The yellow robot
remains deferred.
