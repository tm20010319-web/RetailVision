# Database And Data Asset Inventory

Audit date: 2026-08-23. This is a read-only reconciliation. No database,
dataset, report, camera asset, model weight, or capture was moved, opened for
mutation, deleted, or committed.

## Database authority and usage

The current default/main RetailVision database is **`retailvision.db`** because
`agent/config.py` defaults `DATABASE_URL` to `sqlite:///retailvision.db` and the
MCP server documentation uses it for Product Analytics. This is the default
local RetailVision runtime database path. The actual production database is
selected by `DATABASE_URL` / `RETAILVISION_DATABASE_URL`; this is not an
approval to modify the local file.

| Path | Size | Purpose | Current consumer | Classification | Restricted data? | Regenerable? | Cleanup recommendation |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `retailvision.db` | 786,432 bytes | Default local RetailVision runtime SQLite database; Product Analytics MCP defaults here when no URL is supplied. The actual production database is selected by `DATABASE_URL` / `RETAILVISION_DATABASE_URL`. | `agent/config.py`, `agent/db.py`, `agent/mcp_server/server.py`, Product Analytics docs | PRODUCTION_SOURCE / RUNTIME_STATE | Yes, may contain member/identity/business rows | Schema is regenerable; contents are not safely regenerable | **DO NOT TOUCH**; retain, back up and review only in an approved persistence phase |
| `retailvision_test.db` | 245,760 bytes | Local file-backed test/verification database from older tests or manual runs | Historical test scripts and local verification; most tests use `sqlite:///:memory:` | TEST_SOURCE / RUNTIME_STATE | Possibly, depending on test data | Schema/test rows may be recreated; contents not assumed disposable | **DO NOT TOUCH in Phase 0A**; manual review before any deletion |
| `demo_flow.db` | 46,469,120 bytes | Active local StoreRuntime/Demo Greeting and real-device validation database; contains operational history and device identity evidence | `demo_greeting`, `scripts/start_demo_greeting.ps1`, P6S validation scripts/docs, local `DATABASE_URL` examples | DEMO_SOURCE / RUNTIME_STATE / RESTRICTED_ARTIFACT | Yes; device/member/recognition data | No, not without data loss and re-ingest | **DO NOT TOUCH**; never move/delete while services or validation workflows depend on it |
| `outputs/p6s-e2e-20260727-193151/evidence.db` | 331,776 bytes | Historical P6S E2E evidence database | Historical E2E output only | RESTRICTED_ARTIFACT | Yes, likely identity/camera evidence | Re-run may recreate a different evidence set | Excluded from Phase 0A; manual evidence-owner review |
| `outputs/p6s-history-e2e-20260727-193727/evidence.db` | 331,776 bytes | Historical P6S history E2E evidence database | Historical E2E output only | RESTRICTED_ARTIFACT | Yes, likely identity/camera evidence | Re-run may recreate a different evidence set | Excluded from Phase 0A; manual evidence-owner review |
| `ui-verify-20260731-1445.db` | 1,267,960 bytes | UI verification snapshot database | Historical UI verification tooling | VALIDATION_REPORT / RUNTIME_STATE | Unknown; inspect before disposal | Likely, but contents are not confirmed disposable | Excluded from Phase 0A; manual review |
| `data/demo_flow.db` | 0 bytes | Empty duplicate/path artifact | No confirmed consumer | RUNTIME_GENERATED | Unknown | Yes | Excluded from Phase 0A with all DBs; manual review |
| `outputs/demo_flow.db` | 0 bytes | Empty output-path artifact | No confirmed consumer | RUNTIME_GENERATED | Unknown | Yes | Excluded from Phase 0A with all DBs; manual review |

**Database rule:** Phase 0A does not touch any `*.db`, including empty or
historical files. In-memory SQLite test databases are not listed as files and
remain outside this inventory.

## Dataset and analysis directories

| Path | Size / count | Purpose | Current consumer | Classification | Restricted data? | Regenerable? | Cleanup recommendation |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `data/` | 943,044,556 bytes / 1,238 files | Local training/validation assets, including staff-vest imagery and camera-derived data | Staff-vest training/validation scripts and manual workflows | DATA_ASSET | Yes; camera imagery | No, not without source corpus | **DO NOT TOUCH**; externalization requires owner approval and per-file checksums |
| `data/staff_vest_dataset/` | Included above | Staff uniform training corpus | `tools/training/train_staff_vest.py`, validation tools | DATA_ASSET | Yes | No | Manual review only |
| `data/staff_vest_hard_cases/` | Included above | Hard-case evaluation corpus | Staff-vest evaluation tools | DATA_ASSET | Yes | No | Manual review only |
| `analysis_results/` | 51,075,363 bytes / 128 files | Offline V1 golden runs and generated analysis artifacts, including videos, tracks, keyframes, manifests and identity evidence | Offline runners, golden-loop review, handoff evidence | VALIDATION_REPORT / RESTRICTED_ARTIFACT | Yes; identity and camera evidence present | Rebuildable in principle, but not equivalent evidence | **DO NOT TOUCH**; preserve provenance and review individually |
| `analysis_results/.staging/` | Included above | In-progress/staged Offline V1 run artifacts | Offline artifact writer | RUNTIME_GENERATED / RESTRICTED_ARTIFACT | Yes | Rebuildable, but may be active or incomplete | Excluded from Phase 0A; manual review |

## Reports and real-device captures

| Path | Size / count | Purpose | Current consumer | Classification | Restricted data? | Regenerable? | Cleanup recommendation |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `reports/` | 244,278,753 bytes / 149 files | P6S qualification, identity geometry, publication, analytics and staff-vest evidence | Current handoff, validation reports, diagnostic scripts | VALIDATION_REPORT / RESTRICTED_ARTIFACT | Yes; includes device recordings, face geometry, identity evidence and logs | Some can be rerun; evidence is not interchangeable | **DO NOT TOUCH**; retain current validation reports and P6S evidence |
| `reports/p6s_trusted_publication_20260820/` | Included above | Event 804 B2.8 behavior/identity/publication qualification evidence | Current handoff and restricted diagnostic scripts | RESTRICTED_ARTIFACT | Yes | No equivalent substitute without rerun | **DO NOT TOUCH** |
| `reports/p6s_face_recording_geometry_20260819/` | Includes MP4/PNG and geometry JSON | Real-device Face/Person spatial evidence and debug frames | P6S geometry validation docs/scripts | RESTRICTED_ARTIFACT | Yes | Rerunnable but evidence-specific | **DO NOT TOUCH** |
| `reports/p6s_real_device_validation_20260818/` | Includes XML, logs and capture evidence | Real-device push/pagination/registration evidence | P6S validation docs/scripts | RESTRICTED_ARTIFACT | Yes | Rerunnable but evidence-specific | **DO NOT TOUCH** |
| `outputs/member_track_snapshots/` | 7,593,821 bytes / 290 files | Device push/member track snapshot captures | Identity association diagnostics and historical tests | RESTRICTED_ARTIFACT | Yes | Rerunnable but evidence-specific | Excluded from Phase 0A; manual review |
| `stage10_3_live_acceptance/` and demo trace captures | Large generated tree | Live acceptance video/frame traces and runtime evidence | Stage10 acceptance scripts/reports | DEMO_SOURCE / RESTRICTED_ARTIFACT | Likely; camera imagery | Rerunnable but not equivalent evidence | Excluded from Phase 0A; manual review |

## Camera configuration assets

| Path | Size | Purpose | Current consumer | Classification | Restricted data? | Regenerable? | Cleanup recommendation |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `behavior_cameras.example.json` | 3,836 bytes | Example behavior-camera configuration | Demo/runtime tests and docs | CONFIG / TEST_SOURCE | No secrets expected | Yes, but fixture semantics matter | KEEP; do not move in Phase 0A |
| `behavior_cameras.entrance-1.json` | 1,732 bytes | Local StoreRuntime camera configuration | Runtime/demo scripts | CONFIG | May contain endpoints/credentials or private topology | No without manual reconstruction | **DO NOT TOUCH**; protected configuration |
| `behavior_cameras.cam02.stage6a.json` | 796 bytes | Stage6A behavior camera fixture | Behavior tests/tools | CONFIG / VALIDATION_REPORT | May contain device topology | Yes from approved fixture source | Manual review; do not touch in Phase 0A |
| `product_cameras.cam02.stage7c.json` | 1,081 bytes | Stage7C product-camera fixture | Product tracking tests/tools | CONFIG / VALIDATION_REPORT | May contain camera topology | Yes from approved fixture source | Manual review; do not touch in Phase 0A |
| `person_product_association.cam02.stage8.json` | 539 bytes | Stage8 association fixture | Association tests/tools | CONFIG / VALIDATION_REPORT | May contain camera topology | Yes from approved fixture source | Manual review; do not touch in Phase 0A |
| `camera_profile_demo/` | 2,587,614 bytes / 243 files | Camera profile demo assets and support files | Camera profile demo scripts/tests | DEMO_SOURCE / DATA_ASSET | May contain camera imagery | Partly | Manual review; preserve until Demo/Phase 2A owner sign-off |

## Model weights

| Path | Size | Purpose | Current consumer | Classification | Restricted data? | Regenerable? | Cleanup recommendation |
| --- | ---: | --- | --- | --- | --- | --- | --- |
| `yolo11n.pt` | 5,613,764 bytes | YOLO model weight | Detector experiments/tools if selected | DATA_ASSET | No | Downloadable, but version-specific | Excluded from Phase 0A; manual asset review |
| `yolo26n.pt` | 5,544,453 bytes | YOLO model weight | Detector experiments/tools if selected | DATA_ASSET | Downloadable, version-specific | Excluded from Phase 0A; manual asset review |
| `yolov8n.pt` | 6,549,796 bytes | YOLO model fixture | `agent`/detector tests and loaders | DATA_ASSET / TEST_SOURCE | No | Downloadable, but fixture version matters | KEEP; excluded from Phase 0A |
| `agent/yolov8n.pt` | 6,549,796 bytes | Duplicate YOLO model fixture | Agent detector/model loaders | DATA_ASSET | No | Downloadable, but duplicate identity should be confirmed | Excluded from Phase 0A; manual review |

## Reconciliation conclusion

- `retailvision.db` is the default/main RetailVision database.
- `demo_flow.db` is the active demo/real-device operational database and is
  separately protected; it is not the default production database.
- `retailvision_test.db`, both `evidence.db` files, `ui-verify-*.db`, and empty
  duplicate DB paths are all excluded from Phase 0A.
- `data/`, `analysis_results/`, `reports/`, camera assets, model weights and
  real-device captures remain Manual Review or protected.
