# FutureEval operations and evaluation

Production scope: `fall-futureeval-2026` and `minibench`, bot account ID 308654.
The sponsored `superforecaster.py` runner refuses the Metaculus Cup mode. Keep the
separate Cup workflow disabled. The research model, four binary roles, weights,
log-odds pooling and 0.85 shrinkage have not changed in these operations updates.
The binary parser accepts one explicit final `Probability: ZZ%` label even when
it ends the final sentence; it still rejects ranges, missing units and multiple
final labels. This does not ask another LLM to infer or alter the probability.
`forecasting-tools` is pinned to the already-used 0.2.92 for a stable baseline.

## What each run proves

1. Publishing-code updates on `main` also trigger the existing production job
   when `BOT_ENABLED=true`, so a deployed fix can check pending questions without
   waiting for the next cron event. This is a normal production run, not a live
   prediction preview. A standard-library preflight reads authenticated Metaculus history and the
   OpenRouter key budget. It costs no LLM credits. If no open question needs a
   forecast, it skips installing the forecasting dependencies and running models.
   Fall and MiniBench reads are isolated: a failing series is marked **unknown**,
   while available questions from the other series proceed. Read-only requests
   retry transient HTTP/network failures up to three attempts. Identity failure
   or failure of both projects still stops the run. Full audits fail visibly on
   incomplete coverage; preflight warns without blocking healthy projects.
2. Only `PUBLISH_TO_METACULUS=true` enables tournament model calls. Technical
   dry runs must use the testing area; do not inspect active tournament predictions
   and then modify the bot in response to those predictions.
3. The runner removes duplicate question IDs across projects, retains distinct
   group subquestions, and prioritizes the earliest closing deadline. The cap
   remains five by default. Additional eligible questions appear explicitly as
   `deferred`; a later run must pick them up before they close.
4. `run_metrics.json` records selected/deferred/skipped question identities,
   completion failures, model/configuration identity, account balances before and
   after calls, and a fresh API check for submitted forecasts. An unverified
   publication raises an error rather than silently reporting full success. No
   automatic publication retry is added when a response is uncertain.
5. Read-only bot audit runs daily and manually. `bot_audit.json` contains historical
   coverage, current unforecasted open questions, cumulative observed spend and
   missing deadlines since the monitoring baseline (2026-10-16 12:00 UTC).
   The existing `bot-review` tool evaluates resolved questions only. Artifacts are
   retained for 90 days. Historical misses remain visible and cannot be recovered.

`effective_budget.remaining_usd` / `spend_usd` take priority over ordinary `usage`
fields. Unknown cost stays unknown. Account charge deltas can lag provider billing,
include another consumer of the same key, and become incomparable after top-ups
or resets. Research-only and LiteLLM estimates are not the total invoice.

## Independent clock: prepared, not enabled

The repository's 20-minute cron is a fallback, not a delivery guarantee. Actual
October 8–16 runs had gaps longer than the three-hour question window. Increasing
LLM spending does not fix a late trigger.

`clock/worker.mjs` is a small Cloudflare Worker designed for the Workers Free plan.
It checks GitHub every ten minutes, dispatching the existing production workflow
on `main` if there is no recent or active run. It cannot call the Cup workflow,
change code, access bot credentials, or make forecasts itself. It never cancels a
running job. A job stuck over 40 minutes raises an error in Cloudflare logs.
Dispatch success only proves GitHub accepted the request; runner availability and
subsequent publication still require monitoring.

One-time account setup (do not paste tokens into chat):

1. In GitHub, create a **fine-grained personal access token** for only
   `kolosovskyi-kmp/ai-superforecaster`, repository permission **Actions: Read and
   write**. Metadata read is automatic. Set an expiry after the season, for example
   2027-01-15, and keep the token in your password manager.
2. Log in to a Cloudflare account with the Workers Free plan. From `ops/clock`,
   deploy the worker, initially disabled:

   ```bash
   npx wrangler login
   npx wrangler deploy
   npx wrangler secret put GITHUB_TOKEN
   ```

   Enter the token at the secret prompt. The worker never receives the OpenRouter
   or Metaculus keys. No paid Cloudflare plan, custom domain or KV database is needed.
3. Change `[vars] ENABLED` to `"true"` in `wrangler.toml` and run
   `npx wrangler deploy` again. Keep `BOT_ENABLED=true` and
   `PUBLISH_TO_METACULUS=true` in the GitHub repository. External-clock dispatches
   respect `BOT_ENABLED`; setting it to false stops paid automatic forecasting.
4. After propagation (up to 15 minutes), inspect Cloudflare Cron Events/logs and
   GitHub Actions. Verify an actual `workflow_dispatch` run on `main`, followed by
   coverage/publication evidence. Test two or more consecutive trigger intervals;
   no enabled or verified external clock should be claimed before that check.
5. Disable the clock with `ENABLED="false"`, or remove its cron triggers, at the
   end of the season. Revoke its GitHub token when no longer needed.

Sources checked 2026-10-16:
- [GitHub schedule delays and dropped events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Workflow dispatch API and Actions write permission](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/)
- [OpenRouter current-key budget](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)

## Quality gate before spending more

First collect at least 20 *new, resolved* forecasts under the stable configuration
and improved triggering. Four old warm-up scores cannot establish an advantage.
Measure operational coverage separately from predictive quality. Record the
number of resolved and omitted questions, peer score, the community comparison
where available, score by question type and observed account spend. Report a
bot's own average peer score as such; do not call it a tournament rank or a valid
community-relative aggregate without the corresponding scoring data.

Only then evaluate a predeclared change (research depth, role independence,
shrinkage, model) on disjoint practice/resolved data or prospective assignments.
Freeze assignment and configuration before seeing answers. Avoid date leakage
when testing old questions: present-day web search and model knowledge can reveal
outcomes, so an ordinary run on resolved questions is not a clean historical test.
Do not run multiple live versions of the primary bot on the same question.

Accept a change only if it has a defensible quality/cost benefit; report sample
size and uncertainty. Twenty is an initial review threshold, not proof of skill or
a guarantee of prizes. The sponsor's $100 is a spending ceiling, not a target.
