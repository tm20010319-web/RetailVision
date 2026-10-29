# Represent Track Identity Conflicts With Terminated Bindings

When confirmed evidence conflicts with an active same-scope Durable Track Identity Binding, the existing Binding is terminated as a conflict and no Binding is created for the incoming identity. The incoming immutable Evaluation and Finalization Job preserve the competing evidence. Historical successful Jobs remain unchanged because they describe an application that was valid at that time; only lifecycle fields on affected Bindings may change.

A conflict decision is durable before Runtime Cache cleanup, but the Finalization workflow is not complete until every affected Cache identity is invalidated. Cache invalidation is idempotent and technically retryable without repeating the conflict decision; ordinary skipped decisions that terminate no prior Binding require no cleanup.
