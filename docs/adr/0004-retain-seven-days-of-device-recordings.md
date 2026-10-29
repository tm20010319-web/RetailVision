---
status: accepted
---

# Retain seven days of continuous device recordings

Every behaviour-enabled pilot camera will retain at least seven days of continuous recordings. This is required because Peak Entry Window requests may be made retrospectively over a seven-day lookback, after the original Device Recording Segments would otherwise have been overwritten.

## Consequences

- Storage is sized from the configured stream bitrate with operational headroom; at approximately 2 Mbps, a camera produces about 151 GB over seven days, so 256 GB per camera is the initial target.
- Device time synchronisation and recording-coverage checks are part of acceptance.
- A seven-day query can report unavailable evidence when the source recording has a real coverage gap; it must not silently substitute another interval.
