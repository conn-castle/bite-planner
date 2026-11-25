# Bite Planner conventions

- This repository is public. Merge pull requests with
  `gh pr merge --author-email "$(git config user.email)"`, after confirming
  that value is a GitHub noreply address, so a private address is not
  published.
- Scratch under `.agent-layer/tmp/` can hold private operational records.
  Keep their contents out of commits, pull requests and issues.
