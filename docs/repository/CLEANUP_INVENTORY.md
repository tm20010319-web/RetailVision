# RetailVision Cleanup Inventory

Audit date: 2026-08-23. This is an inventory and recommendation snapshot. No
file was deleted by this audit; Phase 1A later moved the six root v1
architecture documents after a separate reference scan and move manifest.

> **Post-execution status (2026-08-24):** This file remains a pre-execution
> inventory snapshot. Strict Phase 0A subsequently completed successfully;
> see the archived Phase 0A execution report in
> `D:\GJJ\work\retailvision_archive\20260826\historical-docs\`. Phase 0B is
> **NOT APPROVED**. Historical scan numbers and recommendations below have not
> been rewritten as post-execution observations.

## Git safety baseline

| Item | Observed value |
| --- | --- |
| Branch | `master` |
| HEAD | `ed0e00093b6f605383294e6ce01d34d011e32474` |
| Status | ahead of `origin/master` by 4; existing modifications in `agent/models.py`, `pipelines/offline_behavior_pipeline.py`, `scripts/run_offline_behavior_analysis.py`; many untracked reports, archives, source additions, and generated directories |
| Tracked generated files | `reports/staff_vest_baseline_v1.md`, `reports/staff_vest_baseline_v1_manual_review.json`, `reports/staff_vest_test_blue_staff.md` are tracked; most runtime output is untracked |
| Large untracked files | `demo_flow.db` (46 MB), `retailvision_stage10_3_review_bundle_20260816_182215_redacted.zip` (60 MB), `retailvision_b28_handoff_ed0e000_20260820.zip` (58 MB), `stage10_2_2_review_bundle...zip` (23 MB), plus `data/` (about 943 MB) and `demo-frontend/` dependencies |
| Duplicate archives | `core_business_logic_v1_20260806.zip`, `retailvision_master_7_commits_20260806.zip`, `retailvision_v2_core_modules_20260812.zip`, two Offline V1 zip/patch pairs, and several Stage10 review bundles |
| Prohibited actions | `git reset`, `git clean`, `git checkout .`, `git rm`, cleanup execution, and automatic commit |

## Important paths and directories

The table uses the required category and recommendation vocabularies. Grouped
rows cover generated children below a named root; a later cleanup batch must
expand each grouped row to a per-file manifest before changing anything.

| Path | Category | Git Tracked | Referenced By Code | Referenced By Docs | Recommendation | Reason |
| --- | --- | ---: | --- | --- | --- | --- |
| `agent/` | PRODUCTION_SOURCE | partial | Yes; imported by pipelines, APIs, runtime and tests | v2 mapping, handoff, ADRs | KEEP | Current Python capability implementation; physical location is frozen by v2 |
| `agent/models.py` | PRODUCTION_SOURCE | Yes (modified) | Yes; ORM imported across agent/pipelines/tests | v2 fact ownership, handoff | KEEP_ROOT | Current ORM source of truth; split only in final migration phase |
| `agent/product_interaction_analytics.py` | PRODUCTION_SOURCE | No | Yes; pipeline writer, MCP adapter, tests | `docs/product_analytics_mcp_server.md`, v3 design | KEEP | Existing ProductInteractionFact writer and query service |
| `agent/mcp_server/` | PRODUCTION_SOURCE | No | Yes; executable MCP server and tests | product analytics MCP doc | KEEP | Integration boundary; should become `integration/mcp` only after tests/gates |
| `pipelines/` | PRODUCTION_SOURCE | partial | Yes; runtime and offline orchestration | v2 mapping, handoff | KEEP | Composition/orchestration, not a long-term capability module |
| `pipelines/offline_behavior_pipeline.py` | PRODUCTION_SOURCE | Yes (modified) | Yes; offline runner/tests | handoff, v3 mapping | KEEP | Frozen Offline V1 orchestration; mapping only |
| `scripts/` | DEV_TOOL | partial | Invoked manually/CI and referenced by docs | validation and handoff docs | KEEP | Explicit operational and diagnostic tools; isolate from production imports |
| `test/` | TEST_SOURCE | partial | Pytest discovery | TEST_INVENTORY, v2 test architecture | MOVE | Reorganize by capability after baseline; do not change tests in this audit |
| `test/__pycache__/`, `.pytest_cache/` | RUNTIME_GENERATED | No | No | No | GITIGNORE | Rebuildable Python/pytest cache |
| `test/outputs/`, `outputs/` | RUNTIME_GENERATED | No | Test/demo runs | reports and cleanup plans | MOVE | Generated videos, JSON, logs and captures; target `artifacts/test-runs/` |
| `.task4-tests/`, `.task5-tests/`, `.task7-device-recognition/` | RUNTIME_GENERATED | No | No production imports; temporary verification | cleanup plans | MOVE | Temporary task snapshots; target `artifacts/test-runs/` |
| `runs/` | RUNTIME_GENERATED | No | ML tooling convention | cleanup plans, `.gitignore` | MOVE | Model/training run output; target `artifacts/profiling/` or external asset store |
| `graphify-out/` | RUNTIME_GENERATED | No | No production imports | `graphify-out/GRAPH_REPORT.md` only | MOVE | Generated graph/cache output; target `artifacts/graph/` |
| `analysis_results/` | VALIDATION_REPORT | No | Analysis tooling only | historical reports | MOVE | Generated analysis result bundles; target `artifacts/reports/` |
| `reports/` | VALIDATION_REPORT | mixed (3 tracked) | Scripts consume staff-vest baseline; P6S reports are evidence | handoff and validation docs | MANUAL_REVIEW | Preserve authoritative validation evidence; move only with manifest and restricted-data review |
| `reports/p6s_trusted_publication_20260820/` | RESTRICTED_ARTIFACT | No | Diagnostic scripts/read-only probes | current handoff | KEEP | B2.8 qualification evidence and restricted identity diagnostics |
| `stage10_*` | RUNTIME_GENERATED | No | Stage10 tools/tests may write/read these paths | Stage10 reports | MOVE | Profiling, acceptance, logs and review bundles; target `artifacts/profiling/` and `artifacts/demo/` |
| `stage10_3_demo_videos/`, `stage10_3_live_acceptance/` | DEMO_SOURCE | No | Demo acceptance scripts | Stage10 reports | MOVE | Demo inputs/results, not production modules; target `artifacts/demo/` |
| `camera_profile_demo/` | DEMO_SOURCE | mixed | Demo camera profile scripts/tests | README and cleanup plan | MANUAL_REVIEW | Presentation fixture with validation dependencies; protect until owner sign-off |
| `demo_greeting/`, `demo-frontend/` | DEMO_SOURCE | mixed | Demo runtime/API/frontend tests | v1/v2 architecture and cleanup plan | MOVE | Isolate demo code/assets from production; keep current demo contract during transition |
| `cleanup/` | DEV_TOOL | Yes | Cleanup planning only | `REPOSITORY_CLEANUP_PLAN.md` | KEEP | Manifest destination for an approved future cleanup batch |
| `config/` | CONFIG | Yes | Runtime configuration loaders | v1/v2 mapping | KEEP_ROOT | Configuration is operational input, not generated output |
| `data/` | DATA_ASSET | No | Training/validation tools | cleanup and staff-vest docs | MANUAL_REVIEW | Contains local camera imagery and training corpus; externalize only with checksums and owner approval |
| `offline_v1_complete_loop_acceptance/` | VALIDATION_REPORT | No | No production imports | Offline V1 reports | ARCHIVE | Acceptance bundle; preserve as historical evidence under `artifacts/reports/` |
| `docs/architecture/v2/` | DOCUMENTATION | No (worktree additions) | N/A | AGENTS, handoff | KEEP_ROOT | Current frozen logical architecture; authoritative over v1 architecture docs |
| `docs/architecture/v3/` | DOCUMENTATION | No (created by this audit) | N/A | this audit | KEEP_ROOT | Design-only target mapping; no physical migration implied |
| `docs/adr/` | DOCUMENTATION | Yes | N/A | CONTEXT, v2 README | KEEP_ROOT | Accepted ADRs and domain decisions are highest authority |
| `docs/repository/` | DOCUMENTATION | No (created by this audit) | N/A | this audit | KEEP_ROOT | Governance inventories and authority matrix |
| `docs/offline_v1_module_mapping.md` | DOCUMENTATION | Yes | N/A | handoff/v2 | KEEP | Current Offline V1 ownership mapping; superseded only where v2 says so |
| root `AGENTS.md`, `CONTEXT.md` | DOCUMENTATION | Yes | N/A | agent instructions | KEEP_ROOT | Repository policy and domain vocabulary |
| `RETAILVISION_CURRENT_HANDOFF.md` | DOCUMENTATION | Yes | N/A | session startup | KEEP_ROOT | Current business/qualification handoff |
| `docs/archive/architecture-v1/PROJECT_MODULE_ARCHITECTURE.md` | DOCUMENTATION | No | N/A | Phase 1A archive | ARCHIVED | Duplicate v1 architecture; v2 is authoritative |
| `docs/archive/architecture-v1/MODULE_DEPENDENCY_RULES.md` | DOCUMENTATION | No | N/A | Phase 1A archive | ARCHIVED | Duplicate v1 dependency rules; v2 supersedes phase statements |
| `docs/archive/architecture-v1/MODULE_MIGRATION_PLAN.md` | DOCUMENTATION | No | N/A | Phase 1A archive | ARCHIVED | Duplicate v1 migration plan; retain as historical reference |
| `docs/archive/architecture-v1/DEVELOPMENT_GUIDE.md` | DOCUMENTATION | No | N/A | Phase 1A archive | ARCHIVED | v1 guide conflicts with v2 freeze and physical-location constraints |
| `docs/archive/architecture-v1/AGENT_MODULE_MAPPING.md`, `docs/archive/architecture-v1/MODULE_INVENTORY.md` | DOCUMENTATION | No/Yes | N/A | Phase 1A archive | ARCHIVED | Useful historical mapping/inventory evidence; v2 is current |
| root `PRE_CLEANUP_BASELINE.md`, `REPOSITORY_CLEANUP_PLAN.md`, `RUNTIME_ARTIFACT_AUDIT.md` | DOCUMENTATION | Yes | N/A | cleanup workflow | KEEP | Existing cleanup guardrails; this inventory updates classification only |
| root `P6S_*_VALIDATION.md`, `OFFLINE_V1_*REPORT.md` | VALIDATION_REPORT | mixed | N/A | current handoff | KEEP | Qualification and freeze evidence; do not rewrite as architecture authority |
| root `STAGE10_*REPORT.md`, `STAGE10_*HANDOFF.md` | VALIDATION_REPORT | mostly untracked | Stage10 tools/tests | Stage10 docs | ARCHIVE | Historical validation/report bundles; use artifacts mapping |
| `*.zip` snapshots and `*.patch` bundles | ARCHIVE_BUNDLE | No | No runtime imports | handoff/history | DELETE_CANDIDATE | Rebuildable snapshots with Git alternatives; require checksum/reference manifest and user approval |
| root `*.log`, nested `*.log` | RUNTIME_GENERATED | No | No source imports | occasional report links | GITIGNORE | stdout/stderr and service logs are rebuildable and can contain sensitive data |
| root `*.db`, `outputs/**/*.db` | RUNTIME_GENERATED | No | Local runtime/test/demo processes | cleanup plan | MANUAL_REVIEW | Runtime state; delete only after owner/data-loss review and process shutdown |
| `*.pt` model weights | DATA_ASSET | mixed | Detector loaders/tests | model/tool docs | MANUAL_REVIEW | Binary model assets; keep required fixture, externalize large duplicates |
| `demo.py`, `product_analytics_agent_demo.py` | DEMO_SOURCE | mixed | Manual demo entrypoints | demo docs | MOVE | Keep demo behavior but isolate from production package |
| `run_*.py`, `device_*_smoke.py`, `test_*.py` at root | DEV_TOOL | mixed | Manual development commands | historical docs | MOVE | Root command clutter; classify explicitly under `tools/` or `legacy/` later |
| `mewcode-java` (external) | UNKNOWN | outside repository | MCP client dependency only | handoff/product analytics doc | KEEP | External Agent Runtime; not a RetailVision Python domain module |

## Generated-artifact destination mapping

No move is executed here. The proposed logical destination is:

```text
artifacts/
  test-runs/       outputs/, .task*-tests/, offline acceptance bundles
  experiments/     analysis_results/
  profiling/       stage10_* profile/performance directories and logs
  reports/         generated validation/report bundles
  demo/            stage10 demo videos and demo captures
  graph/           graphify-out/
  runtime/         transient databases, stdout/stderr, service logs
```

`reports/p6s_trusted_publication_20260820/` and other restricted evidence must
retain access controls and provenance even if later housed below `artifacts/`.

## Cleanup gate

The repository is **not yet safe to execute Cleanup Phase 0**. The inventory is
complete enough to design Phase 0, but the worktree has source modifications,
untracked architecture/source additions, active-looking runtime databases, and
large untracked evidence/assets. Phase 0 requires an approved per-file manifest,
process shutdown, restricted-data review, and a clean focused-test baseline.
