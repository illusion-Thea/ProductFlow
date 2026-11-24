# Issue tracker: GitHub

Issues and task-level PRDs for this repo live as GitHub issues; local planning and execution artifacts follow the existing [Trellis workflow](../../.trellis/workflow.md). Use the `gh` CLI for issue-tracker operations.

## Product baseline and task PRDs

- The [garment product PRD](../garment/PRD.md) is the single extension product-behavior and acceptance baseline. The [technical design](../garment/TECH_DESIGN.md) supplies implementation contracts, and [DEVELOPMENT](../garment/DEVELOPMENT.md) maps the stages and evidence requirements.
- The original [product PRD](../PRD.md) describes current-product capabilities; [ROADMAP](../ROADMAP.md) remains a plan. Neither GitHub issues nor local task PRDs replace the product baseline.
- Each task-level PRD states its concrete scope, constraints, and acceptance criteria and references the applicable FR/AC IDs. A link alone is insufficient. Do not copy the entire product PRD into task records and maintain a separate rule set.
- Keep task progress, approval scope, implementation decisions, and verification evidence in the existing GitHub/Trellis records. Product-rule changes require the user's confirmed decision and a synchronized product-baseline update; implementation details belong in task design/implementation artifacts as required by Trellis.
- Documentation integration does not create or publish GitHub issues or authorize external actions by itself; follow the user's current authorized scope and the existing workflow.

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`. Use a heredoc for multi-line bodies.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` - `gh` does this automatically when run inside a clone.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.
