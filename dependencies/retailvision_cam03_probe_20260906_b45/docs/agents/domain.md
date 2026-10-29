# Domain Docs

This repository uses a single-context domain-documentation layout.

## Before exploring

Read these files when they exist:

- `CONTEXT.md` at the repository root
- Relevant ADRs under `docs/adr/`

If either location does not exist, proceed silently. Domain-modeling skills
create the files lazily when terminology or architectural decisions are
resolved.

## File structure

/
|-- CONTEXT.md
|-- docs/
|   `-- adr/
`-- src/

## Vocabulary

Use domain terms as defined in `CONTEXT.md`. Avoid synonyms that its glossary
explicitly rejects. If a required concept is missing, reconsider whether the
term belongs to the project or record the gap for domain modeling.

## ADR conflicts

Explicitly identify output that conflicts with an existing ADR rather than
silently overriding the recorded decision.
