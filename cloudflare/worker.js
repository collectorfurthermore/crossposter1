/**
 * Cloudflare Cron Trigger for Newsground Crossposter.
 * Invokes the existing GitHub Actions workflow every 10 minutes.
 * Store a fine-grained GitHub token in the encrypted GITHUB_ACTIONS_TOKEN secret.
 * Do not expose the token through a public HTTP endpoint.
 */
const ENDPOINT =
  "https://api.github.com/repos/collectorfurthermore/crossposter1/actions/workflows/crosspost.yml/dispatches";

export default {
  async scheduled(_controller, env, _ctx) {
    const token = env.GITHUB_ACTIONS_TOKEN;
    if (!token) {
      throw new Error("Missing GITHUB_ACTIONS_TOKEN (add a Cloudflare secret)");
    }
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "newsground-crossposter-scheduler",
      },
      body: JSON.stringify({ ref: "main" }),
    });

    if (response.status !== 204) {
      const details = (await response.text()).slice(0, 400);
      throw new Error(`GitHub workflow dispatch returned HTTP ${response.status}: ${details}`);
    }
    console.log("GitHub crosspost workflow dispatched successfully");
  },
};
