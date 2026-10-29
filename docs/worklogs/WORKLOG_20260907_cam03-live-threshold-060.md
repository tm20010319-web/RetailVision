# cam03 live receiver threshold 0.60

Status: `RECEIVER RUNNING / ONSITE ACCEPTANCE THRESHOLD 0.60`

The user explicitly requested changing the threshold to 0.6 after the receiver
was started. This supersedes the earlier no-threshold-change instruction for
the current onsite receiver only. It is not a formal profile qualification or
an identity confirmation. Previous 0.75/0.65 experiments and old Run artifacts
remain unchanged; no historical identity was reassigned.

## Changes and verification

- `scripts/start_cam03_push_receiver.ps1` accepts an explicit
  `-SimilarityThreshold`, validates [0, 1], and passes its invariant-culture
  value through the child process environment. Default remains 0.75.
- `scripts/run_cam03_push_receiver.py` accepts a finite in-range configured
  threshold instead of requiring exactly 0.75. It prints only the effective
  threshold. Existing intake, write restrictions and camera checks are retained.
- `test/test_cam03_run_identity_cli.py` verifies the real application lifespan
  receives both 0.75 and 0.6 and still rejects behavior writes and DDL.
- Focused suite: 22 passed, two existing deprecation warnings. PowerShell
  syntax validation passed.

## Actual execution

Initial receiver started at 2026-09-07T05:58:25.037871 UTC at 0.75, with genuine
cam03 heartbeat persistence. After the user's threshold request, its existing
tool session was interrupted and the same prepared launcher was restarted:

```powershell
& D:/GJJ/work/code/scripts/start_cam03_push_receiver.ps1 -SimilarityThreshold 0.6
```

Startup confirmed effective threshold 0.6. New receiver PID 41520 listens on
0.0.0.0:8081 (tool session 33776). `receiver_started` is recorded at
2026-09-07T06:13:40.114180 UTC. Existing lifecycle logic also recorded
`receiver_unclean_detected`; the restart gap is not claimed as complete Coverage.
The first checked new heartbeat has `heartbeat_accepted` and
`persistence_success` at 2026-09-07T06:14:04.096719 UTC (14:14:04 local).
The unrelated member-management initialization warning persists; actual Push
heartbeat intake and persistence work. No general-service refactor was made.

Only this receiver's process environment changed. Global Settings defaults,
PEAK_ENTRY_CAMERA_ID, cam02 configuration, Stage8/9 and formal business tables
were not changed. The saved-Run identity CLI's diagnostic acceptance still
defaults to 0.75; this receiver restart does not silently change that separate
offline policy or a qualified profile's policy hash. Before a future 0.6
diagnostic Run, explicitly align and record its policy as well.

No new face preflight, new action Run or member association was executed in
this threshold-change turn. Confirmed behavior count produced: 0. Next step:
receive a fresh cam03 Recognition and assess it at 0.6 plus the existing
Event-Time historical mapping; only then proceed with the short action sample.

Pre-start historical evidence remains at
`reports/cam03_20260907_onsite_prepare/`; its earlier status and hashes were not
rewritten to claim this later successful startup.
