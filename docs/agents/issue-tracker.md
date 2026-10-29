# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`
- **Read an issue**: `gh issue view <number> --comments`
- **List issues**: use `gh issue list` with appropriate label and state filters
- **Comment**: `gh issue comment <number> --body "..."`
- **Apply or remove labels**: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Run commands inside this clone so `gh` infers `gjj042454-lang/RealtimeEntryPipeline`
from the Git remote.

## Pull requests as a triage surface

**PRs as a request surface: no.**

Set this to `yes` only if external pull requests should enter the issue triage queue.

## Skill operations

- When a skill says "publish to the issue tracker", create a GitHub issue.
- When a skill says "fetch the relevant ticket", run
  `gh issue view <number> --comments`.
- GitHub issues are the source of truth for issue status, labels, and comments.

## Wayfinding operations

The map is one issue labeled `wayfinder:map`; its child issues are tickets.

- Create maps and tickets with `gh issue create`.
- Link children using GitHub sub-issues when available.
- If sub-issues are unavailable, use a task list in the map and put
  `Part of #<map>` at the top of each child.
- Represent blockers with GitHub issue dependencies when available.
- If dependencies are unavailable, put `Blocked by: #<n>` at the top.
- Claim an unblocked ticket with
  `gh issue edit <number> --add-assignee @me`.
- Resolve it by commenting with the result and closing the issue.
