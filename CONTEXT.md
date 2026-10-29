# RetailVision

RetailVision observes device-reported face matches, resolves them to store members, and records durable member arrivals for downstream analysis.

## Language

**Device Recognition Event**:
A face-match observation reported by a store device, independent of whether it arrives by push or polling.
_Avoid_: Push event, polling record

**Event Time**:
The normalized UTC instant at which a device observed an event, with the device's original timestamp representation retained for audit. It is distinct from receipt and processing time.
_Avoid_: Received time, processing time, server local time

**Recognition Acceptance**:
The determination that a Device Recognition Event is eligible to establish or refresh a Visit and potentially create a Member Arrival. It is independent from whether the event can be associated with a Customer Track.
_Avoid_: Track match, face-to-person association

**Recognition Inbox**:
The durable receipt history for Device Recognition Events, including events that are ignored, waiting for member mapping, or fully processed.
_Avoid_: Push queue, polling queue

**Member Mapping**:
The association between a device-side person identity and one active local Member.
_Avoid_: Face match

**Member Arrival**:
The durable, at-most-once business outcome created when a Visit first gains a Member identity or a new Member Visit is created. Later recognition evidence for the same Visit does not create another Member Arrival.
_Avoid_: Detection, Track, repeated recognition

**Effective Member Arrival**:
A Member Arrival that has not been superseded, belongs to an unsuperseded Visit, and has the same Member as that Visit. Effective Member Arrivals are the sole source for member visit counts and last-visit statistics.
_Avoid_: Canonical Visit count, raw recognition count

**Realtime Arrival Effect**:
A time-sensitive operational action, such as a welcome display or member alert, caused by a Member Arrival while that arrival remains sufficiently recent. It is separate from the durable Arrival fact.
_Avoid_: Arrival retry, historical arrival processing

**Visit Observation**:
Trusted identity or behaviour evidence associated with an existing Reusable Visit without creating another Member Arrival or repeating arrival side effects.
_Avoid_: Repeat arrival, new visit

**Store**:
The physical retail location that owns a Visit and forms the isolation boundary for Visit reuse and reconciliation.
_Avoid_: Deployment, camera group, default location

**Store Resolution**:
The determination of one unambiguous Store for a Device Recognition Event or Visit from trusted persisted and configured sources. Missing or conflicting Store Resolution blocks downstream member identity business rather than selecting a location implicitly.
_Avoid_: First configured store, best-effort location

**Device Store Registration**:
A time-bounded assignment of a recognition device to one Store, used according to Event Time so that later device reassignment does not rewrite historical Store ownership.
_Avoid_: Current environment default, mutable device location

**Camera Store Registration**:
A time-bounded assignment of a behaviour camera to one Store, used according to Event Time so that later camera reassignment does not rewrite historical Store ownership.
_Avoid_: Current camera configuration, mutable camera location

**Device Camera Registration**:
A time-bounded association between one recognition device and one behaviour camera, used to decide whether a device event is eligible for same-camera Track Association.
_Avoid_: Same-store assumption, current camera list

**Customer Track**:
A stable observed customer trajectory that may remain anonymous or be associated with a Member.
_Avoid_: Member Arrival, Staff Role

**Track Association**:
The short-lived, same-camera determination of whether a Device Recognition Event belongs to one Customer Track. It controls track identity inheritance and behaviour attribution, not Recognition Acceptance.
_Avoid_: Member mapping, cross-camera identity

**Track Association Finalization**:
The durable workflow that attempts to apply one immutable Track Association Evaluation and then completes its required projections. Workflow completion does not imply that identity was applied.
_Avoid_: Binding result, association assessment

**Binding Application Decision**:
The domain outcome of attempting to apply a Track Association Evaluation: applied, skipped, or conflict. It is distinct from completion or failure of the surrounding Finalization workflow.
_Avoid_: Finalization status, job success

**Durable Track Identity Binding**:
The persistent fact that one Member identity was applied to one complete Customer Track token within its originating Runtime instance.
_Avoid_: Track Association Evaluation, identity cache entry

**Track Identity Runtime Projection**:
The ephemeral Active Confirmed Binding view for one exact Customer Track in its current Store Runtime. It contains no independent identity decision and never falls back across Track, camera, or Runtime scope.
_Avoid_: Durable Binding, probable identity, Track lookup

**Active Confirmed Binding**:
A confirmed Durable Track Identity Binding whose lifecycle has not been explicitly terminated and whose exact Customer Track has not expired. Active is a derived condition, not a persisted Binding status.
_Avoid_: Active status, cached identity

**Binding Lifecycle Heartbeat**:
A throttled observation that extends a confirmed Durable Track Identity Binding only while its exact Customer Track remains active in the originating Runtime instance.
_Avoid_: Visit activity, identity reassessment

**Confirmed Visit Activity Refresh**:
A throttled Event Time projection from one realtime exact Customer Track with an Active Confirmed Binding onto its reusable Canonical Visit's `last_seen_at`. It does not extend Binding lifecycle or create, close, reopen, merge, or supersede a Visit.
_Avoid_: Binding heartbeat, Behavior Backfill, Visit closure

**Behavior Identity Backfill**:
The bounded historical projection of one Durable Track Identity Binding onto eligible Behaviour Events from the same exact Customer Track. It preserves existing identity provenance and does not infer Visit entry evidence.
_Avoid_: Visit activity, entry association, identity reassessment

**Identity Confidence**:
The persisted confidence of the member face recognition that supplied a Durable Track Identity Binding; it is not a Track detection, containment, or geometry score.
_Avoid_: Track confidence, spatial match score

**Track Identity Conflict**:
A later confirmed assessment that makes one or more previously applied same-scope Track identities unsafe to propagate. Existing Bindings are terminated without applying the competing identity, while the immutable Evaluation and its Finalization preserve the conflict evidence.
_Avoid_: Binding replacement, failed Binding

**Face Spatial Evidence**:
Verified face location and image-coordinate metadata from the same camera view as a Customer Track observation, suitable for determining a confirmed Track Association.
_Avoid_: Face crop coordinates, inferred vendor fields

**Spatial Association Clock**:
The Face Spatial Evidence event time used to select and compare Customer Track snapshots; Recognition Effective Time is used only when no valid face event time exists.
_Avoid_: Processing time, nearest convenient frame time

**Primary Track Snapshot**:
The snapshot of one Customer Track nearest to the Spatial Association Clock, selected before spatial matching and used as the authoritative assessment frame.
_Avoid_: Best-overlap frame, latest frame

**Temporal Support Snapshot**:
A nearby snapshot retained only to explain temporal stability around a Primary Track Snapshot; it cannot replace the Primary or upgrade its failed assessment.
_Avoid_: Alternate primary, fallback match frame

**Business Event Clock**:
The effective Device Recognition Event time used for Visit, Member Arrival, age, skew, and realtime-effect business facts. It remains independent from the Spatial Association Clock.
_Avoid_: Face frame time, processing time

**Association Outcome**:
The recorded result of processing one accepted Device Recognition Event against a Visit and optional Customer Track, including whether it created a Member Arrival or only observed an existing Visit.
_Avoid_: Inbox processed state, Track status

**Customer Entrance**:
The designated store boundary whose inward crossing starts customer-arrival and greeting evaluation.
_Avoid_: Doorway, first detection

**Customer Entry Event**:
A durable record that one Customer Track crossed inward through the Customer Entrance; one visit contributes at most one event until the customer leaves and re-enters.
_Avoid_: Member Arrival, frame count

**Re-entry Evidence**:
Reliable evidence that a previous Visit ended before a later Customer Entry Event. A later inward crossing by itself is not Re-entry Evidence when no departure or closure was observed.
_Avoid_: Second entry event, new track

**Visit Entry Evidence**:
The one-to-many association that records each Customer Entry Event supporting a Visit as initial or repeated evidence; each entry event belongs to one final Canonical Visit.
_Avoid_: Entry-event overwrite, many-to-many entry relation

**Reusable Visit**:
The one open Visit for a Member at a store that remains recent enough to receive further trusted identity and behaviour observations. It is a timeout-based business session and does not assert that a physical exit was observed.
_Avoid_: Active Visit, latest Visit, physical presence

**Canonical Visit**:
The Reusable Visit selected to receive business associations when duplicate open Visits for the same Member and store are reconciled.
_Avoid_: Latest Visit, surviving row

**Visit Merge**:
The audit-preserving reconciliation of duplicate open Visits into one Canonical Visit by moving their business associations while retaining each Visit's original identity and entry evidence.
_Avoid_: Visit deletion, closing duplicates only

**Exact Anonymous Visit Convergence**:
The pre-Finalization promotion or merge of one entrance-backed Anonymous Visit
into a Member's Canonical Visit when strong confirmed Track Association evidence
matches the complete Store, camera, Runtime instance, Track id, and Track token.
It is blocked once any participating Visit is referenced by Phase 2B
Finalization or Durable Binding provenance.
_Avoid_: Nearest anonymous Visit, temporal Visit match, post-Binding repair

**Peak Entry Window**:
The rolling five-minute interval with the greatest number of Customer Entry Events within a requested three-day or seven-day lookback.
_Avoid_: Peak occupancy, busiest fixed bucket

**Identity Availability**:
Whether at least one recognition channel for a device is fresh enough to resolve a person identity.
_Avoid_: Camera online

**Staff Identity**:
The identity of a person who is registered as store staff; an unknown person cannot stand in for a Staff Identity.
_Avoid_: Employee track, nearby person

**Staff Role**:
The observed role assigned to a person wearing the configured staff uniform; it does not identify a named employee.
_Avoid_: Staff Identity, employee name

**Staff Greeting**:
An observed interaction in which a person in the Staff Role actively moves toward an arriving customer and reaches greeting proximity within the greeting window.
_Avoid_: Reception, staff presence
**Greeting Attribution**:
The recorded name and source for a confirmed Staff Greeting. In the single-camera demo it is a configured mock name and must not be treated as a Staff Identity.
_Avoid_: Staff Identity, Staff Role


**Merchandise Engagement**:
A customer's observable interaction with a merchandise display, including dwell and reach behaviours.
_Avoid_: Purchase intent, product interest

**Behaviour Event**:
A durable record that one observable behaviour was evaluated for a Customer Track, including its Behaviour Assessment and optional Behaviour Evidence.
_Avoid_: Behaviour Assessment, business outcome

**Merchandise Reach**:
An observed action in which a customer's arm or hand enters a merchandise display area; it does not assert that an item was removed.
_Avoid_: Product pickup, purchase

**Merchandise Display Area**:
A camera-specific area occupied by a merchandise display and associated with one display or product grouping.
_Avoid_: Shelf detection, Interaction Area

**Merchandise Interaction Area**:
A camera-specific area in front of a Merchandise Display Area where customer dwell is measured.
_Avoid_: Merchandise Display Area, store zone

**Merchandise Dwell**:
A continuous interval during which a Customer Track remains in a Merchandise Interaction Area long enough to exclude a brief pass-by.
_Avoid_: Zone visit, frame count

**Behaviour Assessment**:
The outcome of evaluating an observed behaviour as confirmed, not observed, or uncertain; uncertainty is not treated as success.
_Avoid_: Boolean detection, forced classification

**Behaviour Evidence**:
An Evidence Clip associated with a Behaviour Assessment so that the recorded outcome can be reviewed.
_Avoid_: Detection snapshot, Behaviour Assessment

**Business Stage Marker**:
A timestamped business signal that an order has entered a named fulfilment stage.
_Avoid_: Button click, video timestamp

**Fulfilment Stage**:
A named interval in an order journey that starts at one Business Stage Marker and ends at the next.
_Avoid_: Order status, Business Stage Marker

**Evidence Clip**:
A continuous excerpt from one camera recording covering a requested time interval.
_Avoid_: Highlight, edited video

**Device Recording Segment**:
A continuous source recording interval retained by a camera and discoverable through its recording index.
_Avoid_: Evidence Clip, live stream

**Recording Retention Window**:
The period for which Device Recording Segments remain available for retrospective clipping; the pilot requires at least seven days.
_Avoid_: Evidence Clip retention, archive duration

**Order Journey Video**:
An ordered presentation of Evidence Clips associated with one order's Business Stage Markers.
_Avoid_: Evidence Clip, source recording

**Clip Manifest**:
The ordered metadata record that identifies the Evidence Clips and Fulfilment Stages belonging to one video package.
_Avoid_: Order Journey Video, playlist

**Product Listing**:
A store's offer of one product with its image, category, colour, dimensions, and current availability.
_Avoid_: Product image, inventory transaction

**Product Search Criteria**:
The structured category, colour, and dimensional constraints used to find matching Product Listings.
_Avoid_: Natural-language query, visual similarity

**Video-enabled Store**:
A store whose configured cameras and recording retention can produce Evidence Clips for its orders.
_Avoid_: Store with product availability, online store

**Tracked Order**:
An order at a Video-enabled Store whose Fulfilment Stages are captured for an Order Journey Video; it does not imply payment or inventory reservation.
_Avoid_: Paid order, purchase record
