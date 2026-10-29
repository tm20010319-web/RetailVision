# Worklog: Product Analytics V1

Date: 2026-08-24  
Status: `COMPLETED`

## Scope

Implemented the approved anonymous Product Behavior Analytics projection. The
projection is separate from formal `BehaviorEvent`, Identity, Visit, and
Publication Gate components.

## Review of the pre-existing worktree implementation

The worktree already contained `ProductInteractionFact`, a writer, a query
service, seven MCP tools, an offline pipeline seam, and focused tests. The
initial structure matched the intended layering, but it had four gaps:

- facts were written before the Run artifact was sealed;
- projection rows lacked source/fact/manifest audit references;
- duplicate facts in one input batch could fail the database uniqueness check;
- query output did not expose all approved metric names.

## Changes

- Kept `ProductInteractionFact` as the sole analytics projection table.
- Added nullable additive audit columns: `source_video_id`,
  `normalized_fact_ref`, `artifact_manifest_hash`, and `projection_version`.
- Added `_product_analytics.py` schema evolution for existing SQLite databases.
- Moved pipeline projection after successful artifact sealing, while retaining
  eligibility for anonymous facts when formal identity/publication is blocked.
- Added batch idempotency and audit hash validation to
  `ProductInteractionFactWriter`.
- Exposed canonical metrics including `avg_holding_seconds` and
  `unique_person_track_count`, while retaining compatibility aliases.
- Kept the existing QueryService/MCP layering and seven tool contracts.

## Frozen boundaries

No changes were made to formal BehaviorEvent persistence, Identity association,
Visit handling, Stage5/7C/8/9 semantics, or the Publication Gate.

## Verification

- Product Analytics, MCP, and schema evolution: `31` tests passed.
- Offline behavior pipeline, publication, behavior, and B2.8 qualification:
  `37` tests passed.
- Full repository baseline was not run; existing worktree changes and known
  unrelated failures remain outside this worklog.

## Next step

Review the projection contract and approve production rollout/migration timing
before enabling it against a production database.
