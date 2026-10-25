---
name: groupbuy-collection
description: Configure, diagnose and run groupbuy-agent for public shop and group-buying product collection, then verify exports and resume unfinished tasks. Use for first-run setup, batch collection, local HAR input and operator handoff.
---

# Groupbuy collection workflow

Work from the project root. Read `docs/first-run.md` for installation and current
Reqable setup, `docs/model-providers.md` for API configuration, and
`docs/usage.md` only when using manual exports or scheduling. Use `docs/results.md`
to explain output files. The runtime runs independently of this skill or Codex.

First establish the requested city, target center, visible range/category, batch
size and output location. Keep task configurations and evidence local. On Windows
use D for the project, temporary files, caches and outputs. Start with one real
task, then a batch after comparing the first result with the interface.

Use the offline demonstration to verify parsing and exports without a model key.
It uses fictional data and scripted tool choices, so do not report it as a live
collection. Check the local environment, report connectivity and model tool call
separately. A passed API probe does not validate Reqable or the selected location.
No POST during a capture test means transport setup needs attention; do not try
to solve it with more paid model calls.

For live operation, calibrate the current window and actual visible control text.
A keyword does not change the location center. Do not invent a missing radius or
reuse coordinates from another computer. Configure only target-platform reports;
keep model/VPN traffic separate. Use the normal UI and authorized local inputs.

Run the CLI or web Agent, one entry point at a time. Inspect the latest stage and
tool result. Do not replay private endpoints, work around verification, clear
unknown locks, retry stopped tasks blindly or treat a scroll limit as the end.
When attention is required, explain the actual code and the next operator action.

After each task, verify the result's task binding, quality checks and manifest.
Explain shops.csv and products.csv separately; public products are not personal
orders. Unknown values stay blank. Only a committed, verified export can mark a
task complete. A model's final message is insufficient.

For handoff, share repository code, final guides and fictional templates. Each
operator supplies their own account session, key, configuration and progress.
Do not include HAR, credentials, real captures, locations, results or private logs
in Git. Prepare publication through the reviewed allowlist and sensitive scan;
push only when the user has authorized publication.
