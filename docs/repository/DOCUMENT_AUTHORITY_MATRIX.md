# Document Authority Matrix

Audit date: 2026-08-23. Classification is about governance authority, not
whether a document is useful. Accepted ADRs and `CONTEXT.md` remain the highest
authority, followed by current production behavior/tests, then Architecture v2.

## Authority classes

| Class | Meaning |
| --- | --- |
| AUTHORITATIVE | Governs terminology, accepted decisions, or the frozen current logical architecture |
| CURRENT_HANDOFF | Current session/business/qualification context; not a replacement for ADRs |
| CURRENT_VALIDATION | Current evidence/report for a named qualification or freeze gate |
| HISTORICAL_REFERENCE | Retained context or prior plan; useful but not a current rule |
| DUPLICATE | Overlaps a newer authority and should point to it or be archived later |
| GENERATED | Produced by tooling or a run; evidence may be retained but is not policy |
| OBSOLETE_CANDIDATE | Likely removable after reference and owner review |
| UNKNOWN | Requires owner decision before relying on it |

## First-party Markdown matrix

| Path or exact group | Classification | Authority / conflict note | Recommended logical home |
| --- | --- | --- | --- |
| `CONTEXT.md` | AUTHORITATIVE | Domain language and accepted constraints; highest repository authority | root |
| `docs/adr/0001-*.md` through `docs/adr/0020-*.md` | AUTHORITATIVE | Accepted ADRs; supersede informal plans when conflict exists | `docs/adr/` |
| `AGENTS.md`, `docs/agents/*.md` | AUTHORITATIVE | Agent/repository operating instructions | root and `docs/agents/` |
| `docs/handoff/RETAILVISION_CURRENT_HANDOFF.md` | CURRENT_HANDOFF | Current B2/P6S status and frozen Offline V1 boundaries | `docs/handoff/` |
| `docs/architecture/v2/*` | AUTHORITATIVE | Current frozen logical architecture baseline; no physical migration authorization | `docs/architecture/v2/` |
| `docs/architecture/v2/PROJECT_MODULE_ARCHITECTURE_V2.md` | AUTHORITATIVE | Current capability map, target logical tree, v1 supersession notes | `docs/architecture/v2/` |
| `docs/architecture/v2/FACT_OWNERSHIP_V2.md` | AUTHORITATIVE | Fact/projection ownership and source-of-truth rules | `docs/architecture/v2/` |
| `docs/architecture/v2/MODULE_DEPENDENCY_RULES_V2.md` | AUTHORITATIVE | Allowed/forbidden logical dependencies | `docs/architecture/v2/` |
| `docs/architecture/v2/PHASE_MODULE_MAPPING.md` | AUTHORITATIVE | Phase is provenance, not a long-term module | `docs/architecture/v2/` |
| `docs/architecture/v2/MODULE_MAPPING_V2.md` | AUTHORITATIVE | Existing-file logical mapping; physical paths remain frozen | `docs/architecture/v2/` |
| `docs/architecture/v2/TEST_ARCHITECTURE_V2.md` | AUTHORITATIVE | Test layers and future capability grouping | `docs/architecture/v2/` |
| `docs/architecture/v2/DEVELOPMENT_GUIDE_V2.md` | AUTHORITATIVE | New-code placement and review rules | `docs/architecture/v2/` |
| `docs/architecture/v2/MODULE_MIGRATION_PLAN_V2.md` | AUTHORITATIVE | Design-only migration order; blocked before E2E gate | `docs/architecture/v2/` |
| `docs/repository/CLEANUP_INVENTORY.md` | AUTHORITATIVE (governance) | This audit's path classification; does not authorize cleanup | `docs/repository/` |
| `docs/repository/DOCUMENT_AUTHORITY_MATRIX.md` | AUTHORITATIVE (governance) | This matrix; conflicts are recorded, not silently resolved | `docs/repository/` |
| `docs/repository/PHASE0A_EXECUTION_REPORT.md` | CURRENT_VALIDATION | Repository execution evidence for completed strict Phase 0A | `docs/repository/` |
| `docs/repository/PHASE1A_EXECUTION_REPORT.md` | CURRENT_VALIDATION | Repository execution evidence for completed Phase 1A | `docs/repository/` |
| `docs/architecture/v3/*` | AUTHORITATIVE (approved target/refactor design) | Approved target/refactor design; execution remains phase-gated and does not authorize physical migration | `docs/architecture/v3/` |
| `docs/offline_v1_module_mapping.md` | CURRENT_HANDOFF | Offline V1 mapping; constrained by handoff and v2 freeze | `docs/handoff/` or `docs/architecture/v2/` mapping later |
| `docs/product_analytics_mcp_server.md` | CURRENT_HANDOFF | Describes implemented Product Analytics/MCP seam; must not override v2 fact ownership | `docs/integration/` or `docs/guides/` later |
| `docs/handoff/PHASE2D_SAM3_HANDOFF_20260810.md` | HISTORICAL_REFERENCE | Phase 2D proposal; v2 explicitly says do not implement SAM3/2D yet | `docs/archive/` or `docs/handoff/` |
| `docs/research/sam3-official-20260810.md` | HISTORICAL_REFERENCE | Research input, not a product contract | `docs/archive/research/` |
| `docs/2026-07-27-three-week-behaviour-video-scope.md` | HISTORICAL_REFERENCE | Early scope; cannot override current handoff or ADRs | `docs/archive/` |
| `docs/GREETING_DETECTION_V1_V2_DEMO_COMPARISON.md` | HISTORICAL_REFERENCE | Demo comparison, not production authority | `docs/reports/behavior/` or archive |
| `docs/STAFF_VEST_ANNOTATION_GUIDE.md` | CURRENT_HANDOFF | Operator/training guide; only governs annotation workflow | `docs/guides/` |
| `docs/archive/architecture-v1/PROJECT_MODULE_ARCHITECTURE.md` | HISTORICAL_REFERENCE | v1 architecture; v2 supersedes phase/module status and is authoritative | `docs/archive/architecture-v1/` |
| `docs/archive/architecture-v1/MODULE_DEPENDENCY_RULES.md` | HISTORICAL_REFERENCE | v1 dependency matrix; use v2 for current rules | `docs/archive/architecture-v1/` |
| `docs/archive/architecture-v1/MODULE_MIGRATION_PLAN.md` | HISTORICAL_REFERENCE | v1 migration plan; v2 design-only plan is current | `docs/archive/architecture-v1/` |
| `docs/archive/architecture-v1/DEVELOPMENT_GUIDE.md` | HISTORICAL_REFERENCE | v1 placement guide; v2 guide is current | `docs/archive/architecture-v1/` |
| `docs/archive/architecture-v1/AGENT_MODULE_MAPPING.md` | HISTORICAL_REFERENCE | v1 mapping retained for history; v2 mapping is current | `docs/archive/architecture-v1/` |
| `docs/archive/architecture-v1/MODULE_INVENTORY.md` | HISTORICAL_REFERENCE | Earlier inventory; useful evidence, not authority over v2 | `docs/archive/architecture-v1/` |
| `docs/verification/TEST_INVENTORY.md` | CURRENT_VALIDATION | Test taxonomy/baseline; not a module authority | `docs/verification/` |
| `docs/verification/PRE_CLEANUP_BASELINE.md` | CURRENT_VALIDATION | Cleanup gate baseline | `docs/verification/` |
| `docs/verification/PHASE_2A_BASELINE.md` | CURRENT_VALIDATION | Phase 2A baseline and gate | `docs/verification/` |
| `docs/repository/REPOSITORY_CLEANUP_PLAN.md` | AUTHORITATIVE (cleanup guardrail) | Existing execution protocol; no execution approval by itself | `docs/repository/` |
| `docs/repository/RUNTIME_ARTIFACT_AUDIT.md` | CURRENT_VALIDATION | Repository/artifact governance evidence; retained as a historical audit snapshot where its dated observations remain useful | `docs/repository/` |
| `docs/reports/p6s/P6S_*_VALIDATION.md` | CURRENT_VALIDATION | Migrated P6S real-device capability/qualification evidence; each scope is explicit | `docs/reports/p6s/` |
| `docs/reports/p6s/P6S_EVENT_804_SPATIAL_IDENTITY_DIAGNOSTIC.md`, `docs/reports/p6s/P6S_TRUSTED_PRODUCTION_PUBLICATION_VALIDATION.md` | CURRENT_VALIDATION | Current-handoff-linked restricted diagnostic and production-publication evidence | `docs/reports/p6s/` |
| `docs/reports/offline/OFFLINE_V1_COMPLETE_LOOP_GOLDEN_REPORT.md` | CURRENT_VALIDATION | Golden-loop result with known baseline limitations | `docs/reports/offline/` |
| `docs/reports/offline/OFFLINE_V1_FINAL_FREEZE_REPORT.md` | CURRENT_VALIDATION | Offline V1 freeze evidence | `docs/reports/offline/` |
| `docs/reports/offline/OFFLINE_V1_POST_FIX_TEST_REPORT.md`, `docs/reports/offline/OFFLINE_V1_REVIEW_FIX_REPORT.md` | CURRENT_VALIDATION | Fix/review evidence; not design rules | `docs/reports/offline/` |
| `docs/archive/handoff/PROJECT_HANDOFF_20260805.md` | HISTORICAL_REFERENCE | Prior handoff superseded by current handoff | `docs/archive/handoff/` |
| `docs/archive/handoff/SESSION_HANDOFF.md` | HISTORICAL_REFERENCE | Prior handoff; retained as historical planning and qualification context | `docs/archive/handoff/` |
| `docs/archive/validation/stage10/STAGE10_*_REPORT.md`, `docs/archive/validation/stage10/STAGE10_PERFORMANCE_BLOCKER.md` | HISTORICAL_REFERENCE | Historical Stage10 validation evidence; Stage10 is delivery/test provenance, not a current business module | `docs/archive/validation/stage10/` |
| `STAGE10_2_1_REVIEW_MANIFEST.md`, `STAGE10_2_2_REVIEW_MANIFEST.md`, `STAGE10_3_REVIEW_MANIFEST.md` | CURRENT_VALIDATION | Provenance manifests with dense review-bundle/media dependencies; manual review required | root temporarily |
| `docs/archive/handoff/STAGE10_TO_STAGE11_HANDOFF.md` | HISTORICAL_REFERENCE | Historical Stage handoff; Stage10 remains delivery/test provenance, not a current business module | `docs/archive/handoff/` |
| `reports/staff_vest_*.md` | CURRENT_VALIDATION | Tracked/manual training reports; owner and data lineage required | `docs/reports/behavior/` |
| `stage10_*/**/*.md` | GENERATED | Generated profile/preflight/performance reports | `artifacts/profiling/` |
| `graphify-out/GRAPH_REPORT.md` | GENERATED | Graph output, rebuildable | `artifacts/graph/` |
| `camera_profile_demo/README.md`, `demo_greeting/README.md`, `test/README.md` | CURRENT_HANDOFF | Local component instructions, subordinate to v2 and AGENTS | `docs/guides/` or remain adjacent |
| `skill/skill.md` | UNKNOWN | Tool-specific notes; no explicit authority declaration found | `docs/guides/` after owner review |
| `门店摄像头AI分析项目一个月开发计划.md` | HISTORICAL_REFERENCE | Early planning document; terminology may predate CONTEXT | `docs/archive/` |
| `demo-frontend/node_modules/**` Markdown | OBSOLETE_CANDIDATE | Vendored dependency documentation, not repository docs | exclude from repository scan/ship |
| `.pytest_cache/README.md`, `test/.pytest_cache/README.md` | GENERATED | Tool-generated cache documentation | ignore |

## Explicit conflicts and authority decisions

1. The v1 architecture files are retained under
   `docs/archive/architecture-v1/`. The v2 README explicitly supersedes v1
   phase-status/module-boundary statements; v2 is authoritative for logical
   architecture, while ADRs and `CONTEXT.md` remain above it.
2. `docs/offline_v1_module_mapping.md` and the current handoff describe the
   frozen Offline V1 implementation. They do not authorize the v2/v3 physical
   migration or a new Schema.
3. Stage10 reports and generated profile Markdown are validation artifacts,
   not module names or architecture decisions.
4. Product Analytics/MCP documentation describes an implemented seam but does
   not create permission to change the approved anonymous analytics boundary;
   that boundary remains an explicit product approval item in the handoff.

## Proposed document tree (mapping only)

```text
docs/
  architecture/v2/       current frozen logical architecture
  architecture/v3/       this audit's target design
  domain/                future extracted domain guides
  guides/                operational/developer guides
  handoff/               current and historical handoffs
  reports/{offline,behavior,identity,p6s,analytics,mcp}/
  verification/          baselines and test architecture
  repository/             inventories and authority governance
  archive/                superseded v1 plans, old handoffs, research
```

Phase 1A moved the six root v1 architecture documents into
`docs/archive/architecture-v1/` after reference scanning. Their content is
retained with a historical supersession note.
