# RetailVision Documentation

This page is the entry point for documentation authority. When documents
conflict, use the authority order below rather than inferring authority from a
file's location or recency.

## Authority Order

1. [CONTEXT.md](../CONTEXT.md)
2. [Accepted ADRs](adr/)
3. [AGENTS.md](../AGENTS.md)
4. Current production behavior and tests
5. [Architecture v2](architecture/v2/)
6. Approved [Architecture v3](architecture/v3/) design
7. [RETAILVISION_CURRENT_HANDOFF.md](handoff/RETAILVISION_CURRENT_HANDOFF.md)
8. Current validation reports
9. Historical documents

## Current Domain Authority

- [CONTEXT.md](../CONTEXT.md)
- [Accepted ADRs](adr/)
- [AGENTS.md](../AGENTS.md) provides repository operating instructions.

## Current Architecture

- [Architecture v2](architecture/v2/) is the current frozen logical
  architecture baseline. It does not authorize physical package migration.
- [Target Module Architecture v3](architecture/v3/TARGET_MODULE_ARCHITECTURE_V3.md),
  [Module Mapping v3](architecture/v3/MODULE_MAPPING_V3.md), and
  [Module Migration Plan v3](architecture/v3/MODULE_MIGRATION_PLAN_V3.md) are
  approved target/refactor design. Execution remains phase-gated.

## Current Handoff

- [RETAILVISION_CURRENT_HANDOFF.md](handoff/RETAILVISION_CURRENT_HANDOFF.md)

## Validation Evidence

Validation reports, P6S evidence, and Stage10 provenance are historical
artifacts. They are stored outside the repository under
`D:\GJJ\work\retailvision_archive\20260826\`; the current handoff retains the
authoritative summary and the repository must not depend on the archive at
startup.

## Verification

Verification baselines and inventories are historical evidence stored in the
external archive. Test source remains in `test/` and `tests/`.

## Source Refactor

- `REFACTOR-R2A`: `COMPLETED / PASS_WITH_WARNING`. The Product Analytics Python
  Harness is isolated from production Agent ownership at
  `tools/harness/product_analytics/`; its manual demo is
  `demo/product_analytics_agent/run.py`.
- `REFACTOR-R2B`: Demo / Tools boundary audit. It does not authorize source
  movement.
- `REFACTOR-R3`: the future phase-gated Analytics / MCP production migration.

These repository-refactor labels are distinct from the Identity/Track
Association business phases named Phase 2A/2B/2C.

## Repository Governance

- [Repository governance documentation](repository/)
- [Cleanup Inventory](repository/CLEANUP_INVENTORY.md)
- [Document Authority Matrix](repository/DOCUMENT_AUTHORITY_MATRIX.md)
- [Architecture v3 Migration Plan](architecture/v3/MODULE_MIGRATION_PLAN_V3.md)

Phase 0A, Phase 1A, Phase 1B.1, Phase 1B.2, Phase 1C.1, and Phase 1C.2 are
completed. Documentation Cleanup Phase 1 is completed. Phase 0B remains not
approved.

The three root Stage10 Review Manifests remain `MANUAL_REVIEW` provenance
manifests; they are not ordinary remaining Phase 1B moves.

## Historical

Historical handoffs, research, validation reports, and generated Stage10
artifacts are outside the repository in the external archive. Stage10 is
delivery/test provenance, not a current business module. Historical documents
provide context only and do not override current domain authority, accepted
ADRs, current production behavior/tests, Architecture v2, or approved and
phase-gated Architecture v3 design.
