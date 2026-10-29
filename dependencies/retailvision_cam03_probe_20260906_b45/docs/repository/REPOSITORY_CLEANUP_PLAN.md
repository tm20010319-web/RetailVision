# Repository Cleanup Execution Plan

## Execution Gate

No cleanup batch may start unless all conditions are true:

1. Phase 2A is frozen at commit `97c1193` and tag
   `phase-2a-complete`.
2. `git status --short` shows no Phase 2A source/config/schema/test changes.
3. The five focused commands in `PRE_CLEANUP_BASELINE.md` pass with the same
   counts or better.
4. The batch has a complete path manifest, reference search, and explicit
   action for every file it touches.
5. The staged diff contains only that batch and no business-logic change.

The full suite's three known legacy/Event Time blockers are tracked in
`TEST_INVENTORY.md`. A batch may not restore a deprecated production API just
to make those tests green.

## Per-Batch Protocol

Every batch follows the same protocol:

1. Record `git status --short` before modification.
2. Generate a manifest with `path`, `size_bytes`, `git_tracked`, `sha256`,
   `reference_hits`, `rebuildable`, `source_owner`, `destination`, and
   `action`.
3. For each `DELETE_CANDIDATE`, run an exact-path `rg -F` search excluding
   `.git`, vendor directories, generated outputs, and the manifest itself.
4. Apply only the batch's file operations and necessary reference updates.
5. Inspect staged paths and reject logs, media, databases, output directories,
   datasets, and unrelated source unless the batch explicitly owns them.
6. Run every focused gate command.
7. Compare result counts and failures with `PRE_CLEANUP_BASELINE.md`.
8. If a gate regresses before commit, restore only the batch's paths from the
   Phase 2A tag. If a committed batch regresses, revert that batch commit.
9. Create one independent Git commit with no business-logic changes.

## Batch 1: Generated And Runtime Files

**Approval state:** eligible for the next execution request after this plan is
reviewed. This document does not execute Batch 1.

### Candidate roots

- `outputs/`
- root and nested `*.log`
- `graphify-out/`
- `.task5-tests/`
- `runs/`
- debug overlays and generated screenshots outside tracked fixtures
- generated MP4/AVI/MKV outputs
- test-generated JSON and capture output

### Required manifest

Create `cleanup/manifests/batch1-generated-runtime.csv` before deletion. It
must enumerate every file, not just directory globs. A directory may be
removed only when every contained file has `IGNORE_RUNTIME` or
`DELETE_CANDIDATE`, zero protected references, and a known regeneration path.

### Special rules

- Do not touch `demo_flow.db`, training datasets, configured test fixtures,
  or the Store camera configuration.
- Tracked frontend screenshots require an individual reference search and
  their own staged review even though they are rebuildable.
- Running process logs may be truncated/recreated by active services; stop
  owning processes before file operations.

## Batch 2: Development Asset Relocation

**Approval state:** plan only; not approved for execution yet.

| Current asset | Required move target | Safety condition |
| --- | --- | --- |
| `data/staff_vest_dataset/` | `D:\\GJJ\\work\\retailvision-dev-assets\\staff_vest_dataset\\` | Per-file SHA-256 manifest; training scripts updated before source removal. |
| `data/staff_vest_hard_cases/` | `D:\\GJJ\\work\\retailvision-dev-assets\\staff_vest_hard_cases\\` | Preserve labels and relative paths; checksum source and destination. |
| `demo_flow.db` | `D:\\GJJ\\work\\retailvision-runtime\\store-1\\demo_flow.db` | Stop all DB users, checkpoint WAL, copy/verify, update explicit `DATABASE_URL`; never delete without verified destination. |
| Test videos and large media | `D:\\GJJ\\work\\retailvision-dev-assets\\videos\\` | Preserve a small referenced fixture only when tests cannot synthesize it. |
| Camera Profile Demo assets | `D:\\GJJ\\work\\retailvision-dev-assets\\camera-profile-demo\\` | Blocked until Phase 2A live validation and Demo owner sign-off. |

No non-rebuildable asset is a deletion candidate. Movement must be atomic at
the batch level and reversible from the destination manifest.

## Batch 3: Demo Greeting And Demo Frontend

**Approval state:** dependency plan only. Deletion is blocked until Phase 2A
live validation completes.

1. Inventory Demo Greeting API, runtime, state, vision, configuration,
   database writes, tests, and frontend control calls.
2. Keep shared BehaviorEvent, Evidence, Greeting query, and operations models
   outside the deletion set.
3. Preserve `/customer-display` and operations Demo behavior for current
   presentations.
4. Mark manual welcome polling as Demo-only; do not implement the formal
   Member Arrival Effect Consumer in this cleanup.
5. Only after live validation, decide whether Demo code remains supported,
   moves to external development assets, or becomes an archive candidate.

## Batch 4: Legacy Identity, Entry, And SimpleTracker

**Approval state:** dependency-removal plan only.

1. Build an import graph for EntryEventCoordinator, MemberBusinessRuntime,
   old RealtimeEntryPipeline behavior, legacy startup scripts, SimpleTracker,
   and compatibility tests.
2. Separate shared domain models and repositories from legacy orchestrators.
3. Keep new-mode startup fail-fast guards and Store lease mutual exclusion.
4. Move legacy-only tests/configuration to an explicit legacy suite before
   removing any source.
5. Do not delete any file still imported by StoreRuntime, Device Identity,
   Behavior, Visit / Arrival, schema evolution, or focused gate tests.

## Batch 5: Test Migration And Retirement

**Approval state:** plan only.

Use `TEST_INVENTORY.md` as the source of truth:

- Preserve `CORE_UNIT` and `NEW_MODE_INTEGRATION` tests as the formal suite.
- Group `LEGACY`, `DEMO`, `MANUAL`, `HARDWARE`, and `SMOKE` tests into
  explicit selectable suites rather than relying on `.gitignore` accidents.
- Update `test_camera_face_api.py` to the normalized UTC Event Time contract.
- Migrate or retire `test_device_identity_runtime.py`; do not restore
  `initialize_schema` solely for that test.
- Replace legacy assertions in `test_device_recognition_end_to_end.py` with a
  new-mode Resolver/Member Arrival integration seam, while retaining any
  still-supported legacy expectations in a legacy-only suite.
- The end state must have a green formal new-mode suite; legacy/demo failures
  must not mask it.

## Batch 6: Documentation, Startup, Module Map, And Ignore Rules

**Approval state:** final documentation batch after earlier decisions.

1. Production startup documentation shows only unified StoreRuntime new mode.
2. Legacy and component-debug commands move to explicit support docs.
3. README links the module inventory, test inventory, artifact policy, and
   focused/full-suite commands.
4. `.gitignore` is normalized so tracked formal tests remain visible and
   generated runtime outputs remain ignored.
5. Runtime data and external development-asset locations are explicit.
6. Historical Demo location and support/retirement status are recorded.

## Prohibited During Cleanup

- Greeting deletion before Phase 2A live validation.
- Legacy deletion before import and shared-domain separation.
- Large package migration.
- Phase 2B Binding/Finalizer/Cache/Backfill/VisitActivity implementation.
- Candidate, Shadow, Reusable Visit, Arrival, Store lease, or other business
  rule changes.
- Mixed commits containing cleanup plus feature fixes.
