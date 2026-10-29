---
status: accepted
---

# Converge exact anonymous entrance visits before finalization

`ReusableVisitResolver` may promote an Anonymous entrance-backed Visit to a
Member Visit only while resolving a strong finalizable confirmed Track
Association. The candidate is selected from `VisitEntryEvidence` and its
`CustomerEntryEvent`, never by recency: store, camera, runtime instance, both
track-id fields, and the full `store:camera:runtime:track` token must match.
Zero candidates preserve normal Resolver behaviour; more than one candidate is
diagnostic-only ambiguity.

For one candidate, the Resolver first builds a complete plan with the existing
reusable Member Visits and delegates any fact convergence to `VisitMergeService`.
It then checks every participating Visit for any
`TrackAssociationFinalizationJob` or `TrackIdentityBinding` reference. A hit is
a supported Phase 2C limitation, not a runtime integrity failure: no promotion,
merge, Cache update, Binding update, or historical Job rewrite occurs. This
provenance fence applies regardless of Job or application status.
The Resolver rolls back the planned convergence before business writes, emits a
structured diagnostic, and terminally records the incoming Recognition as
`no_business_outcome` so the known limitation is not retried as a transient
failure. Existing Visit, Arrival, Effect, statistics, Job, Binding, and Cache
facts remain unchanged.

If the fence passes, promotion/merge, canonical Visit selection, Arrival fact
convergence, Inbox projection, and any new Finalization Job occur inside the
Resolver's existing fenced `BEGIN IMMEDIATE` transaction. The Job is created
only after canonicalization and therefore records the final Canonical Visit.
Phase 2C never changes immutable Evaluation, Binding, Job, or Cache provenance.
