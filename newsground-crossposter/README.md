# Newsground Crossposter

**Publish on X as normal. New posts are automatically copied to Bluesky and Mastodon.**

A small, self-hosted Python automation. You do not need Buffer, a computer that stays on, or a monthly crossposting subscription. The default GitHub Actions workflow checks X every 10 minutes, at :07, :17, :27, :37, :47, and :57 each hour (UTC). GitHub may delay or occasionally skip runs, so these are scheduled checks, not guaranteed exact posting times. For **$0 in GitHub Actions fees with a 10-minute schedule**, use a **public** repository: standard GitHub-hosted Actions jobs are free in public repositories. The code, X post IDs, Bluesky and Mastodon post IDs, and activity heartbeat will be publicly visible. Credentials stay encrypted in GitHub Actions Secrets if you follow the setup. A private repository is an alternative if you want to keep the state private; for that option, you may need to slow polling to 30–60 minutes or pay for overage.

## What it does

- Mirrors your *new* original X posts, links, and up to 4 photos (preserving alt text when X provides it).
- Copies **threads that you reply to yourself** as threaded replies where their parents have already been mirrored.
- Skips reposts and replies to *other people's* posts.
- Mirrors video/GIF posts as text **with a link to the video on X**, rather than uploading the video.
- Shortens text exceeding Bluesky's 300-grapheme/3000-byte limit or Mastodon's (configurable) character limit, with a link to the full X post.
- Keeps a local `state.json` checkpoint so it does not intentionally post the same X item twice. If one platform fails, the other platform's success is saved before retrying.
- **First run is a safe initialization:** It records your most recent X post but does not publish your old posts. Only posts written *after* the first run are copied.

**Limitations:** Images are converted to JPEG to respect Bluesky image size constraints (some transparency or animation may be lost). Video is linked, not uploaded. Quote-post embeds are not reconstructed as native Bluesky quote cards. If GitHub is canceled or crashes after publishing but before its checkpoint is saved to the repo, the next run could produce a duplicate; this is rare but not impossible. Deleted or edited X posts are not reconciled, and older X-thread parents are not backfilled. The GitHub Actions schedule can be delayed or skipped; subsequent runs catch up.

## Setup (on Windows, about seven steps)

1. **Download the ZIP and extract it** on Windows. The extracted folder must contain `README.md`, `crosspost.py`, `state.json`, and the hidden `.github` directory. The GitHub website does **not extract a ZIP for you**; use GitHub Desktop to publish the folder. Sign in to [GitHub Desktop](https://desktop.github.com/), choose **File → Add local repository**, select the extracted `newsground-crossposter-free` folder, and select **Create a repository here** if prompted. Commit the files, then click **Publish repository**. Name it `newsground-crossposter` and **uncheck `Keep this code private`** if you want unlimited free public-repository Actions use. If you choose Private instead, see the pricing notes below.
2. At [X Developer Console](https://console.x.com/), create a developer app with read access. Obtain its **Bearer Token**. X uses pay-per-use API pricing: normal post reads are currently **$0.005 per returned post**, and qualifying owned reads are **$0.001 per post**. Authorization, billing eligibility, and current rates depend on your account. Enter a low spending limit. X may require credits or billing before API access works.
3. In **Bluesky → Settings → Privacy and security → App passwords**, create an app password. Use an **app password**, not your normal Bluesky password.
4. On your **Mastodon instance → Preferences → Development → New application**, create an app with `write:statuses` and `write:media` permissions. Generate an access token. Use the full instance URL, e.g. `https://mastodon.social`.
5. In the GitHub repository open **Settings → Secrets and variables → Actions → New repository secret**. Add the following **six required** names and values, plus the optional `X_USER_ID` if you know it:

   | Secret | Example / value |
   |---|---|
   | `X_USERNAME` | Your X handle (no `@` needed) |
   | `X_BEARER_TOKEN` | X app bearer token |
   | `BLUESKY_HANDLE` | Your full Bluesky handle, e.g. `yourname.bsky.social` |
   | `BLUESKY_APP_PASSWORD` | Bluesky app password |
   | `MASTODON_BASE_URL` | Mastodon server URL, including `https://` |
   | `MASTODON_ACCESS_TOKEN` | Mastodon app access token |
   | `X_USER_ID` | **Optional:** numeric X account ID, avoids even the one-time username lookup |

   If `X_USER_ID` is empty, it will resolve your handle **once** (the user lookup may cost $0.010), then cache the numeric ID in `state.json` for later runs. You can set it manually if you already know it.
6. Go to **Settings → Actions → General → Workflow permissions** and permit **Read and write permissions** so the workflow can commit its checkpoint to `state.json`. Then go to **Actions → Mirror X to Bluesky and Mastodon → Run workflow**. It should say `Initialized at X post ... Old posts were NOT published.`
7. Now create a short *new* post on X. Run the workflow manually a second time or allow the next scheduled execution. Check your Bluesky and Mastodon profiles. Done.

**Important:** Never put access tokens in source code, `state.json`, screenshots, or chat messages. Put them only in GitHub Actions Secrets. The ZIP contains no credentials. For another Mastodon instance, set its corresponding URL and token.

## Pricing and how to stay free on GitHub

- **Public GitHub repository with the default 10-minute schedule: $0 GitHub Actions charges.** Standard GitHub-hosted Actions runners are free in public repositories. Your source and checkpoint state are visible, but credentials remain private in repository Secrets. The workflow makes a harmless weekly heartbeat commit to reduce the risk that GitHub automatically disables public schedules after 60 days without activity. Scheduled runs may still be delayed or dropped.
- **Private GitHub repository:** GitHub Free includes $12/month of compute, equal to 2,000 minutes of standard two-core Linux runner time at $0.006/min (as of November 2026). At 144 runs/day, even one billed minute/job is 4,320 minutes/30 days, roughly $13.92 above the included allowance. For a private repository, modify the cron to `7,37 * * * *` for every 30 minutes (1,440 jobs/month if all run) or to `7 * * * *` for every 60 minutes (720 jobs/month). Those configurations are likely free if average job duration and other Actions use fit the allowance. Billing rounds each job up to one-minute units. Check actual usage and configure a spending budget.
- **X API:** Reading your timeline has its own pricing/credit requirements. Account lookup and returned post reads may be billable even when GitHub is free. The first initialization has been limited to one page of 5 newest X posts so it does not fetch your entire history. With 100 new posts/month, $0.005/read would be ~$0.50, excluding other charges and eligibility for owned-post discounts. Confirm current pricing in the X Developer Console and set a low X usage limit.

GitHub documentation: https://docs.github.com/en/actions/concepts/billing-and-usage and https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows.

## Run or test locally

Requires Python 3.11+. Copy `.env.example` to `.env`, fill in secrets, then:

```sh
pip install -r requirements.txt
python -m unittest discover -s tests -v
python crosspost.py
```

The first `python crosspost.py` call initializes, and subsequent calls mirror new posts. Never commit `.env`.

## Customize

- Change `7,17,27,37,47,57 * * * *` in `.github/workflows/crosspost.yml` for a different polling interval. For 30 minutes use `7,37 * * * *`; for 60 minutes use `7 * * * *`. GitHub supports a minimum 5-minute interval; faster checking may increase Actions usage.
- If your Mastodon instance supports more than 500 characters, change `MASTODON_MAX_CHARS` in the workflow.
- To reset without accidentally backfilling old X content, set `initialized` to `false` and `last_seen_id` to `null` in `state.json`, and leave `posts` as is. The next run primes instead of publishing old items.
- If the workflow fails, open the corresponding job under **Actions**; an X `401/403/402` usually means a token/access/credits issue, while a Mastodon `401/403` indicates token/scopes.

## Data and safety

The app uses X's official API and the official Bluesky/Mastodon APIs, not scraping, browser cookies, or password sharing. It intentionally **does not log secrets**. Check your X API billing dashboard. If you choose a public repository, do not place credentials, unpublished reporting, or confidential data in files or commit messages; use only GitHub Actions Secrets. Do not give the workflow more permissions than `contents: write`.
