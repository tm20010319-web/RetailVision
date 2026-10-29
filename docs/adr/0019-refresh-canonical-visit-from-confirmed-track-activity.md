---
status: accepted
---

# Refresh Canonical Visit Activity From Confirmed Exact Tracks

Realtime Visit activity is accepted only when one current Behavior Track resolves
through the Store Runtime's shared exact Track Identity Runtime Projection to an
Active Confirmed Binding. The observation retains its complete Store, camera,
Runtime instance, Track token, Binding, Member, Canonical Visit, and Track Event
Time provenance. Probable identity, missing or mismatched scope, and historical
Behavior Backfill do not produce Visit activity.

The video-frame path performs only exact Cache resolution and in-memory
coalescing. One StoreRuntime-owned refresher keeps the greatest Track Event Time
per Canonical Visit and exact Track token, then periodically submits a batch to
a short database transaction. The transaction resolves the final Canonical
Visit, applies the existing Reusable Visit eligibility validator at the activity
Event Time, rejects future-clock anomalies, and advances only
`Visit.last_seen_at` monotonically. Multiple cameras may independently advance
the same Canonical Visit; event-time ordering determines the final maximum.

Visit activity never creates, closes, reopens, merges, or supersedes a Visit and
never changes Binding lifecycle, Arrival, Member statistics, Evaluation,
Finalization, Inbox, or Behavior Backfill facts. The previous inactivity closure
branch in `VisitActivityService` is removed without replacement; future Visit
closure is a separate lifecycle decision. The refresher shares the existing
Phase 2B production capability gate and is stopped and joined before Runtime
teardown or persistent identity fail-stop.
