---
status: accepted
---

# Use one resolver for member association and arrival facts

Accepted device recognitions are delivered through the durable Recognition Inbox, but only `ReusableVisitResolver` may create, reuse, merge, or update member Visits, Member Arrivals, Arrival Effects, and session activity. The new Binder/fallback chain and the legacy `EntryEventCoordinator` are mutually exclusive runtime modes, because allowing both to process one database would create competing Visit and Arrival decisions.

Inbox delivery status remains separate from recognition acceptance, Store Resolution, and Track Association; `processed` is not a business-arrival fact. In `new` mode, `CustomerEntryVisit` persists entry evidence and anonymous Visits only, while the resolver is the sole member-association writer. A database-backed runtime lease prevents independent legacy and new processes from running against the same store/database.

## Phase 2C narrow supersede

When an exact active confirmed Track Identity Runtime Projection already names
an existing reusable Canonical Member Visit, `CustomerEntryVisit` may append a
`CustomerEntryEvent` and `VisitEntryEvidence` to that Visit. This is limited to
the Recognition-first, Entry-later ordering and requires exact store, camera,
runtime instance, track id, and full track token scope. The transaction
revalidates the resolved Canonical Visit, its member provenance, lifecycle, and
the Runtime Lease before commit.

This exception gives Visit Entry no member-association authority. It cannot
create or promote a Member Visit, merge Visits, decide identity, create an
Arrival or Effect, create a Finalization Job or Binding, or fall back to a
database member lookup. A missing, probable, expired, or invalid projection
continues to create an Anonymous Visit; a confirmed projection that conflicts
with durable Visit provenance fails closed.

The entry Event Time may also advance `Visit.last_seen_at`, but only through
the shared Confirmed Visit Activity contract. This is a monotonic activity
projection, not member association or Visit lifecycle authority.
