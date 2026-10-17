// Independent clock only: it never receives Metaculus or OpenRouter keys.
const REPOSITORY = "kolosovskyi-kmp/ai-superforecaster";
const WORKFLOW = "run_bot_on_tournament.yaml";
const ENDPOINT = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/${WORKFLOW}`;
const BUSY = new Set(["queued", "in_progress", "waiting", "requested", "pending"]);

export async function tick(env, fetcher = fetch, now = Date.now()) {
  if (env.ENABLED !== "true") return { status: "disabled" };
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN secret is missing");
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "User-Agent": "futureeval-independent-clock",
    "X-GitHub-Api-Version": "2026-03-10",
  };
  const response = await fetcher(`${ENDPOINT}/runs?branch=main&per_page=10`, {
    headers, signal: AbortSignal.timeout(15000), redirect: "manual",
  });
  if (!response.ok) throw new Error(`Read workflow runs: HTTP ${response.status}`);
  const { workflow_runs: runs } = await response.json();
  if (!Array.isArray(runs)) throw new Error("Unexpected workflow-run response");
  const mainRuns = runs.filter(run => run.head_branch === "main");
  // Avoid adding work behind a long-running publication job. Never cancel it.
  if (mainRuns.some(run => BUSY.has(run.status))) {
    const oldest = Math.min(...mainRuns.filter(run => BUSY.has(run.status)).map(run => Date.parse(run.created_at)));
    if (!Number.isFinite(oldest) || now - oldest > 40 * 60 * 1000) {
      throw new Error("A forecast run has been pending for over 40 minutes; inspect GitHub Actions");
    }
    return { status: "already_running" };
  }
  const latest = Math.max(...mainRuns.map(run => Date.parse(run.created_at)));
  if (mainRuns.length && !Number.isFinite(latest)) throw new Error("Invalid workflow timestamps");
  if (now - latest < 8 * 60 * 1000) return { status: "recent_run" };
  const dispatched = await fetcher(`${ENDPOINT}/dispatches`, {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { max_questions: "5", trigger_source: "external-clock" } }),
    signal: AbortSignal.timeout(15000), redirect: "manual",
  });
  if (![200, 204].includes(dispatched.status)) throw new Error(`Dispatch workflow: HTTP ${dispatched.status}`);
  return { status: "dispatched" };
}

export default {
  async scheduled(controller, env) {
    const result = await tick(env);
    console.log(JSON.stringify({ ...result, scheduled_utc: new Date(controller.scheduledTime).toISOString() }));
  },
  async fetch() { return new Response("Not found", { status: 404 }); },
};
