import test from "node:test";
import assert from "node:assert/strict";
import worker, { tick } from "./worker.mjs";

const now = Date.parse("2026-10-16T12:00:00Z");
const env = { ENABLED: "true", GITHUB_TOKEN: "fake-test-token" };
const run = (age, status = "completed", branch = "main") => ({
  head_branch: branch, created_at: new Date(now - age * 60000).toISOString(), status,
});
function mock(runs = [], status = 204) {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1 ? Response.json({ workflow_runs: runs }) : new Response(null, { status });
  };
  return { calls, fetcher };
}
test("disabled clock makes no requests", async () => {
  assert.deepEqual(await tick({}, () => { throw new Error("unexpected request"); }, now), { status: "disabled" });
});
test("missing secret fails before dispatch", async () => {
  await assert.rejects(tick({ ENABLED: "true" }, undefined, now), /missing/);
});
test("old completed run dispatches only fixed main workflow with five-question cap", async () => {
  const { calls, fetcher } = mock([run(90)]);
  assert.equal((await tick(env, fetcher, now)).status, "dispatched");
  assert.equal(calls.length, 2);
  assert.match(calls[1].url, /ai-superforecaster\/actions\/workflows\/run_bot_on_tournament.yaml\/dispatches$/);
  assert.deepEqual(JSON.parse(calls[1].options.body), { ref: "main", inputs: { max_questions: "5", trigger_source: "external-clock" } });
});
test("recent and busy runs suppress duplicate dispatches", async () => {
  for (const [runs, status] of [[[run(2)], "recent_run"], [[run(15, "in_progress")], "already_running"]]) {
    const { calls, fetcher } = mock(runs);
    assert.equal((await tick(env, fetcher, now)).status, status);
    assert.equal(calls.length, 1);
  }
});
test("stuck run raises an actionable error instead of building a queue", async () => {
  const { calls, fetcher } = mock([run(50, "queued")]);
  await assert.rejects(tick(env, fetcher, now), /40 minutes/);
  assert.equal(calls.length, 1);
});
test("GitHub API authorization failure is not reported as success", async () => {
  await assert.rejects(tick(env, async () => new Response(null, { status: 401 }), now), /HTTP 401/);
  const { fetcher } = mock([], 403);
  await assert.rejects(tick(env, fetcher, now), /HTTP 403/);
});
test("dispatch accepts both supported success response codes", async () => {
  const { fetcher } = mock([], 200);
  assert.equal((await tick(env, fetcher, now)).status, "dispatched");
});
test("HTTP interface cannot trigger any workflow", async () => {
  assert.equal((await worker.fetch()).status, 404);
});
