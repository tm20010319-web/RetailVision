# Document Authority Registry

Status: `CURRENT_AUTHORITY`
Priority: `P0`
Role: `DOCUMENT_AUTHORITY_REGISTRY`
Content authority: `ROUTING_ONLY`

## Purpose

This is the first-read navigation entry for developers and AI coding agents. It
maps documents to their intended authority, design, evidence, research, or
historical role so that readers do not infer authority from file location,
recency, or filename.

The Registry does not define or duplicate business rules, database contracts,
API contracts, MCP contracts, schema behavior, or runtime behavior. Those
remain in the sources named below.

## What P0 Means

P0 means first-read navigation priority. It does not mean highest business
authority and it does not override accepted ADR decisions, `CONTEXT.md`,
current production behavior/tests, or architecture contracts.

## Authority Resolution Order

When sources disagree, resolve the subject in this order:

1. Current production runtime behavior and tests.
2. Accepted ADR decisions in [`docs/adr/`](../adr/).
3. Current business context: [`CONTEXT.md`](../../CONTEXT.md) and the
   [current handoff](../handoff/RETAILVISION_CURRENT_HANDOFF.md).
4. Current frozen logical architecture in
   [`docs/architecture/v2/`](../architecture/v2/).
5. Approved, phase-gated target design in
   [`docs/architecture/v3/`](../architecture/v3/).
6. Validation evidence, which proves results but does not define behavior.
7. Historical and research documents, which provide context only.

This routing order does not grant the Registry authority over any higher-level
source.

## Classifications

- `CURRENT_AUTHORITY`: active guidance for a bounded current responsibility.
- `CURRENT_DESIGN`: approved future design; execution may still be phase-gated.
- `VALIDATION_EVIDENCE`: proof, baseline, manifest, or execution result; not a
  business or implementation contract.
- `HISTORICAL`: superseded handoff, architecture, phase, or validation context.
- `RESEARCH`: learning or exploratory material; not an implementation rule.
- `DUPLICATE`: overlapping material with no independent decision value.
- `UNKNOWN`: not sufficiently understood to rely on safely.

### Conflict status

`CONFLICTING` is not a primary classification. It is a separate risk field.
Use it only when both conditions hold:

1. The document remains in an active reading path, such as a root/docs index,
   current handoff, or agent instruction.
2. It contains executable guidance inconsistent with current authority.

Use `REVIEW_REQUIRED` when a stale or ambiguous document needs owner review but
the strict active-path conflict test is not proven. Historical age alone is not
a conflict.

## Domain Routing

| Domain | Authority source |
| --- | --- |
| Business context and terminology | [`CONTEXT.md`](../../CONTEXT.md) and current handoff |
| Accepted decisions | [`docs/adr/`](../adr/) |
| Repository operating rules | [`AGENTS.md`](../../AGENTS.md) and [`docs/agents/`](../agents/) |
| Current logical architecture | [`docs/architecture/v2/`](../architecture/v2/) |
| Approved target/refactor design | [`docs/architecture/v3/`](../architecture/v3/), phase-gated |
| Product Analytics/MCP implementation | Current production code/tests; [MCP guide](../product_analytics_mcp_server.md) is subordinate and reviewable |
| Validation and qualification | Historical evidence is archived outside the repository; current qualification state is summarized in the current handoff |
| Repository governance | [`docs/repository/`](../repository/) |
| Historical context | External archive `D:\GJJ\work\retailvision_archive\20260826\historical-docs\` |
| Research | External archive `D:\GJJ\work\retailvision_archive\20260826\historical-docs\` |

## Non-Authority Boundaries

Do not use this Registry as a source for business, database, API, MCP, schema,
or implementation contracts. Do not copy domain prose into this file. Read the
routed authority source instead. A phase report or validation artifact may
prove what happened without authorizing a new implementation or migration.

For the complete physical inventory, classification rationale, conflict review,
and future consolidation candidates, see
[`REFACTOR_R4_05_DOCUMENT_AUTHORITY_AUDIT.md`](../repository/REFACTOR_R4_05_DOCUMENT_AUTHORITY_AUDIT.md).
