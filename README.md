# Newsground Crossposter

Mirrors **new posts that you publish on X** to your Bluesky and Mastodon profiles. Scheduled every ten minutes using GitHub Actions. No Buffer needed.

## Setup

1. Open **Settings → Secrets and variables → Actions → New repository secret**. Add:
   - `X_USERNAME`: your X handle without the @
   - `X_BEARER_TOKEN`: read-capable bearer token from https://console.x.com/
   - `BLUESKY_HANDLE`: your Bluesky handle
   - `BLUESKY_APP_PASSWORD`: Bluesky app password (not your regular password)
   - `MASTODON_BASE_URL`: `https://journa.host`
   - `MASTODON_ACCESS_TOKEN`: Journa.host application access token with `write:statuses` and `write:media`
   - `X_USER_ID` (optional): your numeric X user ID, avoiding an initial lookup.
2. Under **Settings → Actions → General → Workflow permissions**, enable **Read and write permissions** so the bot can save its progress in `state.json`.
3. Under **Settings → Secrets and variables → Actions → Variables**, click **New repository variable**, set **Name** to `CROSSPOST_ENABLED` and **Value** to `true`. The workflow intentionally stays paused until this is set. Add it only after all six required secrets are ready.\n4. On the **Actions** tab, select **Mirror X to Bluesky and Mastodon** and choose **Run workflow**. The **first run initializes** at your current newest post, without republishing old posts.
5. Publish a NEW X post; run the workflow a second time or allow the next scheduled check. It should appear on both networks.

Credentials belong **only in GitHub Actions Secrets**. Never commit actual credentials. For Mastodon, use your Journa.host instance URL.

To temporarily pause all publishing, change `CROSSPOST_ENABLED` to `false` or delete the variable. The first successful run establishes a checkpoint: it does **not** crosspost any older posts. You can see the workflow at https://github.com/collectorfurthermore/crossposter1/actions.\n\n## Behavior

- Polls every ten minutes at :07, :17, :27, :37, :47, and :57 UTC, subject to GitHub scheduler delays.
- Mirrors original posts, expanded URLs, up to four photos (with alt text when provided), and own-account threads.
- Skips retweets/reposts, quote posts, and replies to other people. Videos/GIFs are linked back to X rather than uploaded.
- Trims longer posts with an X link where necessary.
- Records each X post's publishing status independently for Bluesky and Mastodon to reduce duplicate posts.
- Does not synchronize existing/edited/deleted X posts.

## Costs and availability

Public-repo standard GitHub-hosted Actions are currently free; your code and post-ID checkpoints are public. X API access has separate pay-per-use costs: monitor your credits. GitHub scheduled workflows can be delayed, skipped, or disabled after long inactivity. A weekly activity heartbeat commit reduces the last risk.

## Offline tests

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Troubleshooting

Go to **Actions** → select a run → inspect the **Crosspost newly published X posts** step. If the job is skipped, verify the `CROSSPOST_ENABLED` repository variable is exactly `true`. If X returns HTTP 401, 402, or 403, check your bearer token, permissions, and API billing. If a publisher returns an authorization error, check its app password/token and Mastodon scopes.

Do not share tokens in chat or commit a `.env` file.
