# Module Migration Plan v3

Status: `DESIGN_ONLY`; no phase is approved for execution. Every phase must be
an isolated change set with a complete manifest, focused tests, and a reversible
rollback. Physical package movement and ORM/Schema changes are intentionally
deferred.

## Global gates

- Preserve `CONTEXT.md`, accepted ADRs, Architecture v2, current handoff and
  all Behavior/Identity/Offline V1 frozen contracts.
- Record branch, HEAD, status, staged paths and focused/full test results before
  each phase.
- Do not mix cleanup, package movement, behavior fixes, threshold changes or
  Schema changes in one change set.
- A phase is complete only when imports, test ownership, documentation links,
  runtime entrypoints and rollback manifests are verified.

## Phase 0A - Safe Rebuildable Generated Cleanup

**Files affected:** only the exact files listed in
`cleanup/manifests/phase0a_manifest.md`: Python/pytest caches, graphify
generated cache/output, temporary task-test output, confirmed synthetic test
output, confirmed Stage10 profiling/retry temporary output, and unprotected
stdout/stderr logs. The manifest is intentionally file-by-file and excludes
media, databases, source, reports and evidence.

**Safety contract:** every candidate must be 100% rebuildable, have no business
database, no real-device evidence, no identity data, no camera capture, and no
authoritative/current validation report. Any non-zero safety count makes Phase
0A `NOT SAFE`.

**Dependency risk:** low but non-zero: caches and logs can be held by running
processes, and generated test/profile output can be referenced by local review
notes. No candidate may be assumed safe merely because it is untracked.

**Tests required:** no business behavior changes; record branch/HEAD/status,
verify the manifest hashes and counts, confirm no protected paths are listed,
and run the existing focused baseline after any approved cleanup.

**Rollback strategy:** retain the SHA-256 manifest; restore deleted files only
from the manifest or regenerate them. Revert an isolated cleanup commit if the
owner later authorizes one.

**Forbidden business changes:** no source deletion, Schema change, ORM move,
algorithm/threshold change, publication-gate change, identity-policy change,
or evidence rewrite. No database, `data/`, `analysis_results/`, `reports/`,
P6S evidence, real-device capture, camera asset, model weight, zip/patch
archive, or live acceptance evidence is in scope.

## Phase 0B - Manual Asset / Evidence Review

**Files affected:** all `*.db`; `analysis_results/`; `reports/`; P6S evidence;
real-device captures; `data/`; model weights; camera assets; zip/patch
archives; live acceptance evidence; media excluded from Phase 0A; and any
generated file whose ownership or provenance is not proven.

**Dependency risk:** high: these paths may contain operational databases,
identity/member data, camera imagery, authoritative validation evidence,
historical provenance, or non-rebuildable training assets.

**Tests required:** owner-specific data/evidence review, database backup and
restore checks where applicable, restricted-data review, provenance checksums,
and any qualification reruns required by the owning report. No Phase 0B action
is authorized by this plan.

**Rollback strategy:** no movement or deletion until an approved per-file
manifest, destination, checksum verification, process shutdown plan and owner
sign-off exist. Any later operation must be independently reversible.

**Forbidden business changes:** no database deletion/migration, report rewrite,
identity evidence exposure, camera capture modification, model replacement,
source move, or validation-status change.

## Phase 1 - Documentation cleanup

**Files affected:** root v1 duplicates, current handoffs, validation reports,
`docs/architecture/v2/`, `docs/repository/`, `docs/architecture/v3/`, links in
`AGENTS.md` and cleanup plans; future `docs/{handoff,reports,verification,archive}`
mapping only.

**Dependency risk:** changing links or authority labels can cause developers to
follow obsolete architecture; current qualification evidence must remain
discoverable and byte-identical.

**Tests required:** markdown link/path checks, authority-matrix review,
repository instruction smoke check, and a dry-run search for all old document
names. No code tests should change because of documentation cleanup.

**Rollback strategy:** revert only documentation paths; preserve the previous
authority matrix and link manifest.

**Forbidden business changes:** no code move, contract change, test expectation
change, qualification status change, or deletion of an ADR/current handoff.

## REFACTOR-R2A - Product Analytics Harness Isolation

**Status:** `COMPLETED / PASS_WITH_WARNING`; see
`docs/repository/PHASE2A_HARNESS_ISOLATION_REPORT.md` (the historical report
filename predates the REFACTOR naming correction).

The single Python Tool Calling Harness implementation now lives in
`tools/harness/product_analytics/`, with the manual entrypoint at
`demo/product_analytics_agent/run.py`. Production-to-Harness/Demo imports are
zero. `agent.product_analytics_agent_runtime.py` and
`agent.product_analytics_tool_calling.py` remain temporary compatibility
re-export shims and are not a second implementation.

## REFACTOR-R2B - Remaining Demo / Tools Boundary Audit

**Status:** `AUDIT_ONLY`; no Demo, tool, root-entrypoint, or production source
move is authorized by this section. The repository inventory records the
import, hardware, database, Legacy, and test dependencies that must be
resolved before selecting a later small migration.

## Future Demo and tools isolation

**Files affected:** `demo.py`, `demo_greeting/`, `demo-frontend/`,
`camera_profile_demo/`, root `run_*.py`, smoke/diagnostic scripts, and their
tests/configuration references. The already-isolated Product Analytics Harness
and its manual demo entrypoint are not part of this future scope.

**Dependency risk:** demos currently share models, API routes, fixtures and
databases; moving imports can introduce production import side effects or
break presentations.

**Tests required:** all demo tests, camera-profile tests, explicit tool smoke
tests, API import tests, and StoreRuntime production startup/import checks.

**Rollback strategy:** move one entrypoint group at a time behind compatibility
wrappers; restore paths and command manifests if any import or demo gate fails.

**Forbidden business changes:** no changes to production writers, arrival,
identity, behavior thresholds, runtime lease, or database schema.

## REFACTOR-R3 - Analytics / MCP Production Migration

**Files affected:** `agent/product_interaction_analytics.py`,
`agent/product_interaction_mcp.py`, `agent/product_interaction_agent_tools.py`,
`agent/mcp_server/*`, required Offline writer-port wiring, related production
tests, and `docs/product_analytics_mcp_server.md`. The Python Harness
implementation is already isolated and is explicitly out of this production
migration scope.

**Dependency risk:** duplicate writer wiring can double-count facts; query
changes can alter time bounds, idempotency, or anonymous track hashing; MCP
protocol clients may rely on exact tool/error shapes. The current V1 has no
separate AnalyticsAdmissionService, AnalyticsAdmissionFence, or sealed-artifact
admission pipeline.

**Tests required:** all product analytics, MCP server/transport, Tool Calling
Harness, offline pipeline writer, idempotency/conflict, time-range and privacy tests;
re-run Offline V1 and Stage9 regression suites. Add a dual-read or shadow
comparison before switching physical imports.

**Rollback strategy:** keep old import paths as thin compatibility adapters;
use the existing writer uniqueness/idempotency behavior based on
`(analysis_run_id, interaction_session_id, event_type)`; switch the composition
root back if counts or tool contracts diverge.

**Forbidden business changes:** do not change Stage9 facts, member projection,
formal `BehaviorEvent` publication, current V1 scope/time validation,
aggregation semantics, track-hash policy, or MCP protocol without approval.

**Compatibility-shim retirement:** the two old
`agent.product_analytics_*` modules are candidates for retirement only after
REFACTOR-R3 stability has been demonstrated and their compatibility consumers
have been reviewed. They are not a production migration target in this phase.

**Future considerations:** a separately approved admission/fence or sealed
artifact ingestion boundary may be designed later; it is not implemented by
this phase and must not be described as current V1 truth.

## Phase 4 - Behavior capability migration

**Files affected:** behavior monitor/vision/tracking/interaction modules,
`agent/product_interaction.py`, SAM3 adapters/providers, Offline V1 behavior
adapters, `pipelines/behavior_runtime.py`, `pipelines/offline_behavior_pipeline.py`,
behavior/offline tests and fixtures.

**Dependency risk:** highest algorithm/runtime risk; import moves can alter
sampling order, PTS, EOF flush, Stage5/7C/8/9 state, or normalized facts.

**Tests required:** all Stage5/7C/8/9 regression tests, B2.8 focused tests,
EOF terminal-window tests, Offline V1 artifact/publication/media suites,
replay/golden tests, and selected real-device qualification where applicable.

**Rollback strategy:** one capability at a time with old-path adapters; compare
  normalized facts and artifact hashes on golden inputs; revert imports only if
  byte/event counts differ.

**Forbidden business changes:** no thresholds, timestamps, synthetic samples,
session deduplication, identity projection, publication gate, or Schema change.

## Phase 5 - Identity capability migration

**Files affected:** recognition intake/provider/cursor/mapping modules,
`camera_track_identity.py`, association policy/evaluation, finalization/binding,
identity runtime and related tests.

**Dependency risk:** historical identity and binding provenance can be corrupted;
cross-runtime token scope, Primary selection, geometry and conflict lifecycle
are safety boundaries.

**Tests required:** identity intake/repository/end-to-end, track association
policy/geometry, finalization/binding, historical identity, B2.6/B2.8 focused
tests, schema-evolution compatibility, and real-device fingerprint tests.

**Rollback strategy:** preserve exact application identity and immutable rows;
run old and new association projections in shadow mode; restore old composition
if any binding or member-projection diff appears.

**Forbidden business changes:** no identity threshold/policy change, probable
to confirmed promotion, member fallback, coordinate transform, event-time rule,
or Behavior/Visit coupling change.

## Phase 6 - Visit, Member and Effects migration

**Files affected:** entry/Visit/resolver/merge/activity modules, member
services/statistics/APIs, arrival effects and their tests.

**Dependency risk:** canonical Visit/supersede chains and at-most-once Arrival
effects are durable business facts; merge errors are difficult to repair.

**Tests required:** Visit entry/resolver/merge/activity, member arrival/effect,
statistics/API, StoreRuntime integration, ADR 0019/0020 scenarios, and legacy
compatibility suite classification.

**Rollback strategy:** migration adapters retain old service contracts; use
transactional backfills with audit manifests; restore old readers/writers and
reconcile only from immutable facts.

**Forbidden business changes:** no new Arrival semantics, Visit reuse policy,
effect timing, member statistics definition, BehaviorEvent identity, or
external consumer behavior.

## Phase 7 - Infrastructure and persistence decomposition

**Files affected:** `agent/models.py`, `agent/db.py`, schema evolution,
repositories, config/camera/filesystem/model adapters and database migration
scripts.

**Dependency risk:** maximal blast radius: every capability and test imports the
current ORM; table/constraint changes can invalidate historical evidence and
idempotency.

`ProductInteractionFact` is business-owned by `analytics/product` but is
physically persisted by the current SQLAlchemy model in `agent/models.py`.
Only a later approved design may introduce
`analytics/product/domain` plus `infrastructure/persistence/analytics`; this
phase does not split `agent/models.py`.

**Tests required:** full focused suite, schema evolution/migration tests,
repository contract tests, integration tests, backup/restore rehearsal,
artifact/publication verification and a complete baseline classification.

**Rollback strategy:** expand/contract migration with verified backups, dual
read/write only where explicitly approved, immutable migration manifest, and a
database restore plan. Never delete old columns/tables in the first release.

**Forbidden business changes:** no domain rule changes, table meaning changes,
identity/behavior threshold changes, publication-gate changes, or silent data
rewrites. ORM ownership mapping must be approved before physical split.

## Readiness decision

The repository is not ready to execute Phase 0 automatically. It is ready for
an approval discussion because this plan and the companion inventory provide
the required classification and rollback shape. Execution needs a clean,
reviewed per-file manifest and explicit owner approval.
