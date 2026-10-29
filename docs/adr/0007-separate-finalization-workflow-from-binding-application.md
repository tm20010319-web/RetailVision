# Separate Finalization Workflow From Binding Application

Track Association Finalization uses a workflow status independently from its Binding Application Decision. A completed workflow may have applied, skipped, or rejected identity because of a conflict; only a completed workflow with an applied decision means that a Durable Track Identity Binding succeeded. This avoids treating business gate rejection as a technical failure or inventing a second set of overlapping terminal states.

The processing status means only that one Finalizer owns the current leased claim. Pending work, post-commit projection work, and an expired processing claim may all be claimed; post-commit failure remains binding-committed and never returns to pending. Recovery decides whether Binding Application already happened from the durable application decision and Binding reference, not from the transient processing status.
