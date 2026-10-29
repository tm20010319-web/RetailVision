---
status: accepted
---

# Treat association outcome as a rebuildable projection

Visit, Member Arrival, Arrival Effects, canonicalization, formal association state, and member statistics are the strongly consistent facts committed by the resolver transaction. `association_outcome` is a projection for operational display; every fact change records a durable rebuild job, and projection failure must never roll back the committed business facts.

Projection workers recalculate from current canonical Visit and effective Arrival facts rather than replaying stale job conclusions. This preserves the Arrival ledger as the source of truth while allowing projection repair, retry, and dry-run verification.
