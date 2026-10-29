## Agent skills

### Issue tracker

Issues and PRDs are tracked in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the five default triage labels. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.

### Architecture

The current logical module freeze is Target Architecture v2. Start at
`docs/architecture/v2/README.md`. It supersedes Phase-status and module-boundary
statements in the root Target Architecture v1 documents, but does not authorize
physical package migration.

### Current handoff

Read `docs/handoff/RETAILVISION_CURRENT_HANDOFF.md` for the current business context,
architecture map, Offline V1 freeze boundaries, test baseline, and P6S
qualification state. It is the first context document for a new session and
does not authorize new Offline V1 or Provider A work.

## Documentation Authority

### Registry First

Before coding, read `docs/authority/DOCUMENT_AUTHORITY_REGISTRY.md` to
understand where authoritative information lives, how documents are
classified, and how the routing rules work.

### Authority Order

When sources disagree, follow this order:

1. Current production runtime behavior and tests.
2. Accepted ADR decisions.
3. `CONTEXT.md` and the current handoff.
4. Current architecture documentation.
5. Validation evidence.
6. Historical and research documents.

The Registry is navigation-only. It does not override runtime behavior,
accepted ADR decisions, `CONTEXT.md`, or business contracts.

### Evidence Rule

Validation and evidence documents are proof of completed results and historical
verification records. They are not implementation instructions, current
business contracts, or architecture authority. This includes Stage reports,
P6S reports, and Offline validation reports.

## Development Worklogs

Before starting new development:

1. Read `docs/authority/DOCUMENT_AUTHORITY_REGISTRY.md`.
2. Read `docs/worklogs/WORKLOG_INDEX.md`.
3. Read the latest relevant worklog identified by the index.

Do not read all historical worklogs by default. Worklogs are historical
development records and decision context. They are not business contracts,
architecture authority, or a replacement for an ADR.
