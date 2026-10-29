# Camera Demo Greeting

This isolated service is for a live RTSP product demonstration only. It does
not modify the V1 or V2 greeting evaluators.

Role attribution reuses the V1 production boundary:

- Person detections are tracked first;
- vest detections are associated one-to-one with person tracks;
- a five-frame role window requires three Staff hits;
- tracks that stabilize without a vest become Customer;
- unresolved tracks do not participate in a greeting pair.

This prevents one vest box from turning every overlapping person into Staff.
After role attribution, the intentionally relaxed Demo trigger creates one
`staff_greeting` Behaviour Event marked `is_demo=true` when:

1. a resolved Staff and Customer are visible together for 0.5 s;
2. the Staff Role moves at least 15 image pixels while that pair is visible.

The event keeps only track tokens. It does not create a Staff Identity or a
staff name. Evidence is requested as `pending` through the existing event
store, so the UI never implies that a playable clip already exists.

The trigger still uses image motion so the Demo remains tolerant. Event
metrics use the configured `entrance-1` provisional ground calibration:

- `demo_response_seconds`: stable Staff/Customer pair to Demo confirmation;
- `minimum_distance_metres`: minimum Staff/Customer foot-point distance;
- `staff_travel_metres`: maximum Staff foot-point displacement from pair start;
- `distance_source=ground_homography`;
- `calibration_status=provisional`.

The default calibration is loaded from `behavior_cameras.example.json` and is
valid for the configured `2560x1440` frame size. If the frame size or
projection is unavailable, metre values remain null rather than falling back
to invented metres.

## Start

```powershell
Set-Location D:\GJJ\work\code
$env:DATABASE_URL = 'sqlite:///D:/GJJ/work/code/demo_flow.db'
$env:DEMO_GREETING_VIDEO_SOURCE = 'rtsp://admin:@192.0.2.2:554/live/0/MAIN'
& 'C:\Users\Administrator\miniconda3\envs\agents-py311\python.exe' -m demo_greeting
```

Then use `POST http://localhost:8082/api/demo-greeting/start` or the desktop
operations console control bar. The button creates a new session; it does not
write a greeting event itself.

## Control API

```text
GET  /healthz
GET  /api/demo-greeting/status
POST /api/demo-greeting/start
POST /api/demo-greeting/reset
POST /api/demo-greeting/stop
```

The main member API remains on port `8081`. This service only writes the
existing SQLite Behaviour Event tables, so the operations console sees the
event via its existing `/api/behaviors/recent` polling.

The Demo service does not use HSV blue-color fallback, greeting areas, pose,
or V2 scoring. Ground calibration supplies display metrics only and does not
make the trigger stricter. The result is only for live feature demonstration
and must remain separated with `is_demo=true`.
