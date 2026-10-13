# Reporting bugs and feature ideas to GitHub

Tell the agent what happened or what you want in ordinary language. Approximate
time, device, and screen help with bugs, but an idea does not need to be fully
specified. The issue records live in this repository's
[GitHub Issues](https://github.com/nicholasjconn/castle-chef/issues); this page
only describes how the agent files them.

The agent checks open and closed issues for duplicates, then files distinct
problems or independently deliverable features separately using the signed-in
owner's `gh` account. Use the `bug` label for broken behavior and `enhancement`
for requested behavior. Preserve what the user observed or requested without
inventing details or treating a hypothesis as a diagnosis.

For bugs, include **Observed**, **Expected**, **Evidence**, and **Investigation**.
Enrich with relevant app path, release if known, code path, and logs near the
reported time ([Finding a problem](aws.md#finding-a-problem)). Convert local
time to UTC for CloudWatch. Include useful event times, statuses, durations,
and job IDs, and distinguish what the logs prove from what they do not show. If
evidence is unavailable, say so and leave a focused question.

For features, include **Requested behavior**, **Current behavior**, **Acceptance
criteria**, and **Open questions** where useful. Include relevant existing code
or product constraints. File the request with unresolved questions instead of
guessing at missing requirements. Keep credentials, tokens, private log
payloads, and unrelated personal data out of issue bodies. Use `gh issue create
--repo nicholasjconn/castle-chef --label bug` (or `--label enhancement`) with
`--body-file` for multiline content, then give the user the GitHub issue links.
