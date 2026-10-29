---
status: accepted
---

# Use device recordings as the source for evidence clips

Evidence Clips are produced from continuous camera recordings retained on the device. The system queries the device recording index, retrieves every Device Recording Segment overlapping the requested interval, and trims the material locally to the exact interval. HTTP-triggered recording may be added as an optimisation, but it is not the source of truth because it cannot satisfy retrospective requests such as the busiest period from the previous week.

## Consequences

- Cameras used for clipping must have continuous recording, sufficient retention, and clocks synchronised with the application.
- One Evidence Clip may span multiple Device Recording Segments.
- Device-specific retrieval and local media trimming are separate responsibilities.
