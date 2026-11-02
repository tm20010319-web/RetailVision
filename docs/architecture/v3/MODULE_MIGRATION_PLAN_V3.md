# Module Migration Plan v3

Status: DESIGN_ONLY / REVIEW_REQUIRED. This is a future decomposition reference, not authorization to execute phases.
The 2026-09-18 physical layout is already implemented; see [current layout](../CURRENT_MODULE_LAYOUT.md)
and [current status](../../CURRENT_STATUS.md). The prior numbered cleanup stages are archived in the
original snapshot recorded by [archive index](../../repository/ARCHIVE_INDEX.md).

## Completed physical organization

Python Harness lives in `tools/harness/product_analytics/`, its entrypoint in `demo/product_analytics_agent/run.py`.
The two former flat Harness re-export shims were retired in the earlier layout cleanup.
Business files now use capability packages under `agent/`; demos, manual tools, frontend, configs,
databases and weights have designated locations. These moves do not imply implementation of every v3 target seam.

## Remaining design and evidence

| Area | Remaining question | Required validation before a future change |
| --- | --- | --- |
| Analytics / MCP | Deepen writer/query/transport interfaces without changing facts | scope/time, anonymous hash, idempotency/conflict, MCP tool/error schemas, Offline writer integration and privacy |
| Behavior / Offline | Separate tracking, association, interaction and publication interfaces where useful | Stage5/7C/8/9, authoritative PTS/EOF, same-run provenance, seal and publication fence |
| Identity | Deepen intake/assessment/finalization/projection seams | Primary selection, confirmed-only projection, immutable evaluation, exact token, heartbeat/recovery/fail-stop |
| Visit / Member / Effects | Separate transaction orchestration from narrow ports | canonical merge, provenance fence, ADR 0019/0020, effective arrival statistics and effect idempotency |
| API / Infrastructure | Reduce direct ORM/config coupling; ORM split last | schema compatibility on disposable DB, API read models, migrations, import/startup and rollback |
| Legacy / Demo / tools | Review actual consumers before retirement | runtime/CLI/test/config/dynamic-import reachability and hardware side effects |

## Gates and rollback

- Preserve CONTEXT, accepted ADRs, v2 ownership and all frozen Offline/Identity/Behavior contracts.
- Before a separately scoped implementation, capture HEAD, working-tree status, affected files and test baseline.
- Keep package movement separate from algorithm, threshold, schema and production enablement changes.
- Move one interface group at a time with old/new path mapping and recoverable original bytes; update known callers.
  Compatibility adapters are introduced only for proven consumers, not fabricated to recreate retired shims.
- Validate imports, public CLI/API contracts, test ownership, current doc links, dependency boundaries and focused regressions.
  Do not label existing baseline failures green. Use full regression when the affected surface requires it.
- Roll back the affected change set and paths; do not overwrite unrelated working changes or new database writes.
- Source/model/media/evidence assets are not disposable because they are untracked. Use per-file provenance and restoration records.

Current V1 ProductInteractionFactWriter performs its own scope/time validation and persistence.
No separate AnalyticsAdmissionService/AnalyticsAdmissionFence or sealed-artifact ingestion architecture is implemented.
Its normal caller requiring a sealed Run is distinct from introducing such a new admission subsystem.
Formal BehaviorEvent publication, Identity, Visit, Arrival and production gates remain separate.
