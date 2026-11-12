# Reliable 10-minute trigger (Cloudflare Workers)

GitHub Actions' native `schedule` event has sometimes not fired on this repository. This optional Cloudflare Cron Trigger dispatches the already-installed GitHub workflow without moving X, Bluesky, or Mastodon credentials out of GitHub.

## One-time setup

1. At https://github.com/settings/personal-access-tokens/new create a **fine-grained personal access token** with:
   - Resource owner: `collectorfurthermore`
   - Repository access: **Only select repositories** → `crossposter1`
   - Repository permissions: **Actions: Read and write** (and required metadata read).
   - Expiration: select a duration, then renew before expiry.
   The token is used **only to start a GitHub workflow**, never to publish directly to X, Bluesky, or Mastodon.
2. Visit https://dash.cloudflare.com/ and open **Workers & Pages**. Create a Worker named `newsground-crossposter-trigger`.
3. Use **Edit code** to replace the starter code with [worker.js](worker.js), then deploy it.
4. In **Settings → Variables and Secrets**, create an encrypted **Secret** named `GITHUB_ACTIONS_TOKEN` and paste the fine-grained GitHub token. **Do not** put the token in code, a plaintext variable, or GitHub repository files.
5. In the Worker **Settings → Triggers → Cron Triggers**, create the cron `8,18,28,38,48,58 * * * *` (UTC). It runs every ten minutes, staggered from GitHub's native schedule.
6. Wait for the next 10-minute trigger and inspect **Workers logs** and https://github.com/collectorfurthermore/crossposter1/actions . The GitHub run should show event `workflow_dispatch` and its actor will be the fine-grained token owner.
7. Once this route is validated, consider removing the GitHub-native `schedule` to avoid redundant checks, API usage, and possible race conditions. The existing `concurrency` group prevents simultaneous workflow execution but does not remove every duplicate queued trigger.

No public Worker HTTP endpoint is needed: this Worker exports only a `scheduled` handler. Never disclose the access token in chat.

### Costs

Cloudflare's Workers Free plan includes scheduled Workers subject to limits. The existing **public** GitHub repository can use standard GitHub-hosted Actions without billed Actions minutes; X API access remains usage based. Check the current provider limits before enabling billing.

### Troubleshooting

- 401: token missing, expired, or incorrect.
- 403: token is missing repository **Actions: write** permission, or blocked by policy.
- 404: repository or workflow inaccessible to the token.
- 204: GitHub has accepted the dispatch; inspect the Actions run for the crossposting result.
- If the job is **skipped**, set GitHub repository variable `CROSSPOST_ENABLED` to `true`.
