# Separate Spatial Association Clock From Business Event Clock

Spatial Track Association uses a valid Face Spatial Evidence event time as its reference clock and falls back to Recognition Effective Time only when that face time is absent. Resolver business facts continue to use Recognition Effective Time. An anomalous but present face time is diagnosed and degrades spatial temporal evidence rather than silently falling back, preventing a convenient video frame from manufacturing a confirmed assessment.

Each Track's Primary Snapshot minimizes absolute distance from the Spatial Association Clock. Stable-frame count and confidence break equal-distance ties, followed by ascending observation time; earlier frames are preferred only at that final exact tie and never over a closer future frame.
