# Bite Planner conventions

- This repository is public. Merge pull requests with
  `gh pr merge --merge --author-email "$(gh api user -q .login)@users.noreply.github.com"`
  so a private address is not published. GitHub rejects the ID-prefixed
  noreply address in `git config user.email` as a merge author.
- Scratch under `.agent-layer/tmp/` can hold private operational records.
  Keep their contents out of commits, pull requests and issues.
