---
status: accepted
---

# Route all device recognition through the durable inbox

Push and polling are transport adapters for the same Device Recognition Event. Both must persist through the Recognition Inbox before acknowledgement or cursor advancement, and both must use the same acceptance, mapping, deduplication, capture-ownership, and processing semantics. We rejected the legacy direct-callback path and feature-flagged dual processing because adapter choice must not change business behavior.

## Consequences

- Structurally valid but ineligible events remain observable as `ignored` without creating a Member Arrival.
- A missing Member Mapping waits up to 24 hours before becoming terminal `unmapped`.
- Push acknowledges success and polling advances its cursor only after durable intake succeeds.
- Tests may use an in-memory database adapter, but they exercise the same Recognition Inbox path as production.
