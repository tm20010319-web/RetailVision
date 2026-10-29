# Staff Greeting V1 - Stage Closeout / Remaining Risks & Production Hardening

Date: 2026-08-31  
Scope: Staff Greeting Offline V1 / Demo Effect Validation  
Review basis: current code and tests, current camera configuration, P0/P0.1/P1/P1.1 evidence, and the finalized supplied MP4 replay.

## Closeout Decision

`STAFF_GREETING_OFFLINE_V1_DEMO_READY`

The current Offline Staff Greeting Demo is closed for the supplied video and
frozen configuration. The target Cycle completes naturally as `confirmed`.
There is no current Demo Algorithm Blocker.

This is not a production-readiness declaration:

`DEMO_READY / PRODUCTION_HARDENING_PENDING`

The remaining items are mainly broader-data validation, camera calibration,
deployment performance, downstream integration, and observability. They must
not be used to reopen the resolved P0/P1 failures for this Demo.

## Evidence Baseline

### Verified capabilities

- Formal Offline entrypoint exists at
  [`scripts/run_staff_greeting_offline.py`](../../scripts/run_staff_greeting_offline.py).
- The runner performs verified PTS, every-frame MP4 replay and artifact-only
  finalization through [`agent/staff_greeting_offline.py`](../../agent/staff_greeting_offline.py).
  It does not publish Offline Greeting results to `BehaviorEventStore`, Member,
  Visit, or another formal business table.
- Staff/Customer Role Integrity passed. The merged-person-bbox failure that
  transferred Staff Vest evidence into a Customer was contained upstream by
  the lower-torso association guard; it was not a Greeting Core failure.
- Subject Truth is tied to the real people in the supplied MP4, deliberately
  independent of Track IDs. Source SHA-256 and frame-size provenance fail
  closed. The source hash is
  `7a6b195ae7c912a74a829e8067923bfeaf1b608d063304f77917d5e5621c6541` and
  the source frame size is `2560x1440`.
- The target real-video Pair passed approach, staff travel, toward-customer
  movement, distance reduction, Greeting Area eligibility, Greeting distance,
  and sustained duration. The historical P1 report records the exact G1-G10
  evidence.
- P1.1 changed only confidence aggregation: terminal confidence is the minimum
  over the final continuous qualifying sustained window. The threshold remains
  `confirmed_confidence = 0.7`; behavior thresholds and Core gates were not
  lowered.
- The real CUDA replay used the supplied MP4, `cuda:0`, person `imgsz=1280`,
  current ByteTrack, current Staff Role fix, and the existing camera config.
  Analysis run `7c01a9ac41eb4a0288f5bbc2b39849a7` completed with verified PTS
  and `1808/1808` decoded/output frames.
- Final Summary for that run is:
  `total_events=2`, `confirmed_count=1`, `uncertain_count=0`,
  `not_observed_count=0`, `cancelled_count=1`,
  `successful_greeting_count=1`.
- The target Cycle is `confirmed` with
  `event.confidence=0.7185347080230713`, above the unchanged `0.7` threshold.
  The previous lifetime minimum `0.5186229348182678` remains diagnostic
  evidence only.
- P1.1's focused/combined suite was `120/120 PASS`. The later presentation-only
  Chinese Overlay additions brought the same related current suite to
  `123/123 PASS`; no Greeting algorithm change was made by that addition.

### Resolved historical findings

These are closed for the current Demo and are not Active Blockers:

| Finding | Current status | Evidence |
| --- | --- | --- |
| Track23 false `STAFF` / `customer_role_corrected_to_staff` | `RESOLVED` | P0 CUDA replay and Subject Truth evidence show the black-shirt Customer remains Customer with no Vest assignment. |
| Merged person bbox contaminating Customer with Staff Vest | `RESOLVED` for demonstrated geometry | Lower-torso placement guard and ambiguity fail-closed behavior are covered by focused Vision tests and the P0 replay. |
| Lifetime-min confidence made target Cycle `uncertain` | `RESOLVED` for the frozen Demo semantics | P1.1 qualifying-window minimum is `0.718534708`; target replay is `confirmed`. |
| Duration, distance, approach, Greeting Area, Staff Role, Pair continuity | `RESOLVED` for target Cycle | P1 G1-G9 evidence and the P1.1 replay. |

## Risk Register

The required categories and evidence levels are used consistently:

- `PROVEN`: directly shown by current code, test, or artifact evidence.
- `OBSERVED`: directly observed in a real replay but not necessarily a failure
  of the current target Cycle.
- `INFERRED`: a code-derived consequence not directly observed in the supplied
  replay.
- `THEORETICAL`: plausible without a current real failure artifact.

| ID | Area | Finding | Evidence | Category | Priority | Demo Blocker | Recommended Next Step |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | Person tracking | Track fragmentation and ID switches still objectively exist. Historical evidence includes Track20 -> Track42 and mixed Track42/Track57 ownership during overlap. The current fix prevents the demonstrated Vest contamination; it does not make Track continuity stable. A future switch can lose Pair state, approach history, reset `within_since`, or cancel a Cycle. | `OBSERVED` in P0/P0.1 manual truth and diagnostic replay; target Cycle itself stayed continuous. | `PRODUCTION_HARDENING` | P1 | No | Build multi-video track continuity metrics and test whether switches cause business-result loss before considering any ReID. Do not add Track stitching or forced Staff fallback for this Demo. |
| R2 | Approach confidence coverage | `approach_confirmed` can be latched before the final qualifying window. The Core updates lifetime confidence on every Pair observation, while terminal confidence only covers the final sustained window. Thus low-quality observations could theoretically contribute to travel/toward/reduction/trend and later be followed by a high-confidence final window. | `THEORETICAL`; no current false-confirmed artifact or failed golden assertion proves this path. The current MP4's low-confidence overlap caused the old lifetime-min issue, not a proven false Approach decision. | `PRODUCTION_HARDENING` | P2 | Evaluate confidence coverage for Approach on a multi-video set. Do not change Approach semantics or add a second threshold based on this report alone. |
| R3 | Qualifying confidence | A single low-confidence sample inside an otherwise valid final sustained window lowers the whole final-window minimum and can produce `uncertain`. This is the intentionally conservative P1.1 rule and is covered by deterministic Case B. More robust percentile/median aggregation has no multi-video evidence yet. | `PROVEN` semantic behavior; not a current failure. | `PRODUCTION_HARDENING` | P2 | Keep the minimal conservative rule for the Demo. Compare robust aggregations only in a separately approved multi-video evaluation. |
| R4 | Non-confirmed expiry confidence | `_expire()` uses `lifetime_confidence` for timeout/`not_observed`/`uncertain` expiry details, while confirmed terminal assessment uses `qualifying_confidence`. This is intentional: expiry has no completed qualifying terminal window, and the value is descriptive, not a success metric. Summary success counts only `confirmed`. | `PROVEN` by Core code and current Summary contract. | `NO_ACTION_REQUIRED` | P3 | Keep the distinction. Do not reinterpret expiry confidence as a successful-greeting metric. |
| R5 | Sticky Staff Role | Once a Track reaches the existing Vest hit threshold, `PersonRoleResolver` keeps `staff_confirmed=True` for that Track lifetime. Vest misses do not demote it; state is removed only after `maximum_missing_frames` (`8`) of absence. A stale role after long occlusion, erroneous association, or premature Track reuse is plausible, but no current failure evidence shows it. | `THEORETICAL`; code path and tests cover persistence/missing cleanup, not a real stale-role failure. | `PRODUCTION_HARDENING` | P2 | Add controlled long-occlusion and token-reuse fixtures. Preserve sticky semantics unless real data demonstrates stale-role harm. |
| R6 | Vest-to-Person association | Current heuristic includes torso placement (`torso_bottom = 0.60`), containment/area checks, ambiguity fail-closed behavior, and one-Vest-to-at-most-one-Track assignment. It is validated on the supplied camera/video and deterministic overlap fixtures, not on a broad population of angles, heights, poses, clothing, or occlusion patterns. | `OBSERVED` validation scope; lack of broad evidence is explicit, not a discovered bug. | `GENERALIZATION_RISK` | P1 | Create a labeled multi-camera Vest association set covering viewpoints, heights, sitting, occlusion, clothing fit, and lighting. Do not retune the `0.60` guard from one new clip. |
| R7 | Person inference resolution | Offline uses the validated `person_imgsz=1280` candidate. It is acceptable for the Demo because the actual RTX 5070 Ti CUDA replay completed the full 75-second source with 1808/1808 frames and preserved the target result. Processing latency/throughput was not benchmarked as a deployment SLO, and only this GPU environment was validated. | `PROVEN` for current environment; no cross-GPU benchmark. | `PRODUCTION_HARDENING` | P1 | Benchmark 1280 on each target deployment GPU, including end-to-end inference latency, memory, and sustained throughput. Keep 1280 for the current Demo. |
| R8 | Calibration | Current camera config is `calibration_status=provisional`. Ground homography, metre distances, Greeting Area and camera pose are therefore Demo evidence, not production-certified geometry. Installation movement and resolution mismatch remain relevant; the adapter warns on frame-size mismatch, while Offline requires the calibration inputs but accepts provisional status. | `PROVEN` in current camera config and finalized Summary (`calibration_status=provisional`). | `PRODUCTION_HARDENING` | P1 | Perform formal camera calibration/verification, validate homography control points and metre scale, lock camera pose, and make production resolution checks an explicit deployment gate. |
| R9 | Single-video coverage | The confirmed result is from one supplied MP4. Deterministic tests cover overlap, ambiguity, role persistence, behavior gates, reset, and artifact boundaries, but real-video evidence does not cover the full operating distribution. | `PROVEN` evidence-scope fact. | `GENERALIZATION_RISK` | P1 | Establish a multi-video replay set and classify real-video coverage separately from fixture coverage. |
| R10 | Scenario coverage | The following remain `REAL_VIDEO_NOT_VALIDATED` even where some deterministic tests exist: multiple Staff approaching, multiple Customers in Greeting Area, long Staff/Customer occlusion, Staff crossing without greeting, Customer approaching a stationary Staff member, Staff leaving and returning, different heights/uniform wear, lighting changes, other layouts, camera shake, partial occlusion, very short greetings, and absent/covered Vests. Existing deterministic cases must be retained as `DETERMINISTIC_TESTED`, not mislabeled as real-world qualification. | `PROVEN` as an evidence gap; no claim that each scenario is a failure. | `GENERALIZATION_RISK` | P1 | Add a small, labeled scenario matrix to the multi-video replay acceptance package. |
| R11 | Statistical quality | No formal precision, recall, false-greeting rate, missed-greeting rate, or multi-video production dataset baseline exists for Staff Greeting. | `PROVEN` repository/evidence inventory fact. | `OBSERVABILITY_GAP` | P1 | Define annotation and metric policy, then produce a versioned multi-video quality baseline. Do not invent quality numbers from the one confirmed clip. |
| R12 | Artifact/data boundary | Offline remains artifact-only, track-only and identity-independent. It does not write Behavior DB, Member, Visit, or Staff identity records and does not use `BehaviorEventStore` as an implicit publication path. This is intentional V1 scope, not a defect. Future consumers should use the verified `greeting_summary.json` / safe Artifact Result, not internal diagnostics. | `PROVEN` in Offline runner, collector, Summary tests, and current run manifest. | `INTENTIONAL_V1_BOUNDARY` | P2 | Keep publication behind a separately authorized Publication Gate; do not add database writes to the Offline runner. |
| R13 | Frontend/integration | The analyzed video can visibly show `confirmed` Greeting. The repository's `/api/staff-greetings/summary` query is a legacy database query over confirmed events with `staff_name`; it is not a consumer of the new Offline `greeting_summary.json`. No current formal frontend/Agent integration for the Offline artifact was found. | `OBSERVED` from current code paths and artifacts. | `PRODUCT_INTEGRATION_PENDING` | P2 | Design a read-only Offline Summary consumer with explicit `offline_mp4` and `analysis_run_id` boundaries. Do not make legacy DB queries silently mix Offline artifacts. |
| R14 | Realtime Staff Greeting | `BEHAVIOR_REALTIME_GREETING_ENABLED` defaults to `false`; V2 is a child shadow switch and cannot bypass the parent. Realtime Staff Greeting remains legacy compatibility/diagnostic capability, separate from the Offline default path. | `PROVEN` by config, factory gate, tests and current Worklog. | `INTENTIONAL_V1_BOUNDARY` | P2 | Keep default-off. Enable legacy realtime only explicitly for compatibility diagnostics; do not reopen the realtime product chain in this Offline phase. |
| R15 | Golden replay asset | Source SHA-256, source frame size, MP4-local Subject Truth, target subject roles, and a finalized expected Summary/target Cycle are all present across fixtures and run artifacts. They are not yet packaged as one named `Staff Greeting Golden Replay Acceptance` manifest. The MP4 should remain outside Git and be referenced by hash/path. | `PROVEN` current evidence; packaging gap is real. | `OBSERVABILITY_GAP` | P1 | Create a compact sidecar acceptance manifest containing source hash, expected Summary, target Cycle, confidence range, and Subject Truth reference. Do not copy the large video into the repository. |

## Specific Answers Required by This Closeout

### A. Is tracking actually solved?

No. Fragmentation and ID switches remain observed upstream behavior. The P0
fix contains the demonstrated Role/Vest contamination; it does not guarantee
identity continuity. The current target Cycle is nevertheless continuous and
confirmed. Full ReID is not required to close this Demo and should not be
introduced without evidence that ordinary tracker continuity causes material
business-result loss across a broader set.

### B. Does terminal confidence cover Approach evidence?

No, by design. `qualifying_confidence` covers the final continuous sustained
Greeting window. `approach_confirmed` may have latched earlier. This is a
real code-level semantic boundary but only a theoretical production risk in
the current evidence: no false confirmation caused by low-quality Approach
history has been demonstrated.

### C. Is qualifying-window confidence acceptable?

Yes for this Demo. It is minimal, conservative, deterministic, and validated
by P1.1 Cases A-F plus the real replay. A single low sample inside the valid
window still matters. Median/percentile or other robust aggregation requires
multi-video evaluation and is not justified as current tuning.

### D. Why does expiry retain lifetime confidence?

This is intentional and requires `NO_ACTION_REQUIRED`. An expiry has not
completed a qualifying terminal Greeting window, so its lifetime confidence is
diagnostic context. It cannot increase `confirmed_count` and is not interpreted
as successful Greeting evidence.

### E. Can Staff Role become stale?

The code is sticky within a Track lifetime and clears state only after the
configured missing-frame timeout. A stale-role failure is theoretically
possible under bad association or Track reuse, but current fixtures and real
evidence do not establish one. Add long-occlusion/token-reuse tests before
changing this behavior.

### F. Is `torso_bottom=0.60` production validated?

It is a validated heuristic for the demonstrated camera and failure mode, not a
population-level calibration. Treat it as a `GENERALIZATION_RISK`, not a
current bug. Broader viewpoint/pose/occlusion evidence is required before
adjustment.

### G. Is `imgsz=1280` production-ready?

It is Demo-accepted and CUDA-validated on the RTX 5070 Ti environment. It is
`NOT YET BENCHMARKED` as a cross-device production performance setting.

### H. Is provisional calibration acceptable?

`DEMO ACCEPTED`; `PRODUCTION HARDENING REQUIRED`. The current run's metre
distances and Greeting Area are meaningful for the configured recording, but
formal production calibration and installation/resolution verification are
still required.

### I/J. What is the coverage and statistical status?

The repository has strong deterministic tests and one confirmed real MP4, but
not a statistical production-quality baseline. The correct statement is:

`NO STATISTICAL PRODUCTION QUALITY BASELINE YET`

### K/L. What should downstream consumers use?

The Offline boundary remains intentional: consume the same-run, verified
`greeting_summary.json` and its Artifact Result. Do not read internal P1
diagnostics as business data, and do not use the legacy DB `staff_name` query
as an implicit Offline consumer.

## Demo Readiness vs Production Status

| Dimension | Status | Meaning |
| --- | --- | --- |
| Offline MP4 replay path | `DEMO_READY` | Explicit runner, verified PTS, every-frame replay, atomic artifact finalization. |
| Role integrity | `DEMO_READY` | Demonstrated false-Staff root cause contained and Subject Truth passes. |
| Greeting behavior gates | `DEMO_READY` | Target approach, distance, area, and duration pass without threshold reduction. |
| Confidence semantics | `DEMO_READY` | Final qualifying-window minimum produces natural confirmed result at threshold `0.7`. |
| Real MP4 evidence | `DEMO_READY` | Run `7c01a9ac41eb4a0288f5bbc2b39849a7`, `1808/1808`, one confirmed target Cycle. |
| Camera geometry | `PRODUCTION_HARDENING_PENDING` | Current calibration is provisional. |
| Tracker/generalization | `PRODUCTION_HARDENING_PENDING` | Fragmentation is observed and real-video coverage is narrow. |
| Statistical quality | `PRODUCTION_HARDENING_PENDING` | No precision/recall or false-positive/false-negative baseline yet. |
| Product integration | `PRODUCT_INTEGRATION_PENDING` | Offline Summary is not yet a formal frontend/Agent data source. |
| Realtime Staff Greeting | `INTENTIONAL_ARCHITECTURE_BOUNDARY` | Legacy and default-off; not an Offline completion blocker. |

Therefore the current Algorithm/Demo phase can close as:

`STAFF_GREETING_OFFLINE_V1_DEMO_READY`  
`REAL_MP4_CONFIRMED_EVIDENCE_PRESENT`  
`CURRENT_DEMO_BLOCKER_NONE`

It must not be labeled `PRODUCTION READY`.

## Top 3 Next Priorities

### 招商 Demo 前

Use the finalized artifact/video and its run manifest as-is. Pin the run ID,
source hash, Summary, and the visible `CALIBRATION: PROVISIONAL` disclosure.
Do not retune thresholds, add ReID, or reopen the resolved Track23/lifetime
confidence findings for this Demo.

### 招商 Demo 后 / Production Hardening

1. **Multi-video Golden Replay and Quality Baseline (P1).** Build a compact
   labeled replay set covering the listed Staff/Customer, occlusion, tracking,
   clothing, lighting and layout cases. Measure precision, recall,
   false-greeting and missed-greeting rates, plus track fragmentation and its
   effect on Pair outcomes.
2. **Formal Calibration and Deployment Benchmark (P1).** Verify homography,
   metre scale, Greeting Area, camera pose/resolution checks, and benchmark
   `imgsz=1280` on each target GPU with an explicit throughput/memory SLO.
3. **Safe Offline Result Integration (P2).** Add a read-only consumer of the
   same-run Offline Summary behind explicit `source=offline_mp4` and
   `analysis_run_id` validation. Keep any database publication or identity
   binding behind a separate future Gate.

## Do Not Touch for This Closeout

- Do not lower `confirmed_confidence=0.7` or change distance, duration,
  approach, area, EOF, or PTS semantics.
- Do not re-open Track23 false Staff, merged-bbox Vest contamination, or the
  P1.1 lifetime-min blocker as current Demo failures.
- Do not add video-specific branches, Track-number rules, nearest-Staff
  fallback, forced Track stitching, or full ReID as a reaction to this one
  video.
- Do not add Member/Staff identity inference, `BehaviorEventStore` publication,
  or mixed Offline/legacy realtime counting to the Offline runner.
- Do not replace qualifying-window minimum with median/percentile without a
  separately approved multi-video evaluation.
- Do not treat provisional calibration or single-video coverage as proof of
  production quality.

## Machine-Readable Closeout State

```text
STAFF_GREETING_OFFLINE_V1_DEMO_READY
REAL_MP4_CONFIRMED_EVIDENCE_PRESENT
CURRENT_DEMO_BLOCKER_NONE
TRACK_FRAGMENTATION_KNOWN
CALIBRATION_PROVISIONAL
MULTI_VIDEO_VALIDATION_PENDING
PRODUCTION_QUALITY_BASELINE_PENDING
FRONTEND_INTEGRATION_PENDING
PRODUCTION_HARDENING_REQUIRED
NO_FURTHER_CORE_TUNING_REQUIRED_FOR_CURRENT_DEMO
```

