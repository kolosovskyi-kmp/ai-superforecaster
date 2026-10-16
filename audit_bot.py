"""Read-only coverage/budget audit. Never calls an LLM or posts a forecast."""

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from operations import normalize_budget, utc


def get_json(url, token, scheme):
    allowed = "https://www.metaculus.com/api/" if scheme == "Token" else "https://openrouter.ai/api/v1/"
    if not url.startswith(allowed):
        raise ValueError("Audit request outside the expected API")
    request = Request(url, headers={"Authorization": f"{scheme} {token}",
                                   "Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Read-only API request returned HTTP {exc.code}") from None


def list_posts(project, token, user_id=None):
    posts = []
    for offset in range(0, 2000, 100):
        params = {"tournaments": project, "limit": 100, "offset": offset,
                  "order_by": "-created_at", "with_cp": "false"}
        if user_id is not None:
            params["forecaster_id"] = user_id
        data = get_json("https://www.metaculus.com/api/posts/?" + urlencode(params), token, "Token")
        page = data.get("results", [])
        posts.extend(page)
        if len(page) < 100:
            return posts
    raise RuntimeError("Audit pagination exceeded 2,000 posts")


def question_rows(posts, forecasted_post_ids):
    rows = []
    seen = set()
    for post in posts:
        root = post.get("conditional") or post.get("question") or {}
        conditional = root.get("type") == "conditional" or "question_yes" in root
        questions = ([root.get("question_yes"), root.get("question_no")] if conditional
                     else [root] if root else [])
        questions = [question for question in questions if question]
        if post.get("group_of_questions"):
            questions.extend(post["group_of_questions"].get("questions", []))
        for question in questions:
            if question.get("id") in seen:
                continue
            seen.add(question.get("id"))
            mine = question.get("my_forecasts") or {}
            forecasted = bool(mine.get("history") or mine.get("latest"))
            if post["id"] in forecasted_post_ids and len(questions) == 1:
                forecasted = True
            rows.append({
                "post_id": post["id"], "question_id": question.get("id"),
                "type": question.get("type"), "status": question.get("status"),
                "open_time": question.get("open_time"),
                "close_time": question.get("scheduled_close_time"),
                "resolution_time": question.get("scheduled_resolve_time"),
                "forecasted": forecasted,
                "practice_title": "practice" in question.get("title", "").lower(),
            })
    return rows


def main(preflight=False):
    report = {"audit_utc": datetime.now(timezone.utc).isoformat(), "errors": []}
    router_key = os.environ.get("OPENROUTER_API_KEY", "")
    try:
        key = get_json("https://openrouter.ai/api/v1/key", router_key, "Bearer")["data"]
        report["budget"] = normalize_budget(key)
        report["openrouter"] = {name: key.get(name) for name in (
            "limit", "limit_remaining", "limit_reset", "usage", "usage_daily",
            "usage_weekly", "usage_monthly", "expires_at", "include_byok_in_limit")}
        effective = key.get("effective_budget")
        if isinstance(effective, dict):
            report["openrouter"]["effective_budget"] = {name: effective.get(name) for name in (
                "limit_usd", "remaining_usd", "spend_usd", "reset_interval", "resets_at")}
    except Exception as exc:
        report["errors"].append({"area": "openrouter", "error_type": type(exc).__name__})

    token = os.environ.get("METACULUS_TOKEN", "")
    try:
        user = get_json("https://www.metaculus.com/api/users/me", token, "Token")
        report["bot"] = {"id": user["id"], "username": user.get("username")}
        if int(user["id"]) != 308654:
            raise ValueError("Token belongs to a different bot; coverage audit stopped")
        report["projects"] = {}
        for project in ("fall-futureeval-2026", "minibench"):
            posts = list_posts(project, token)
            predicted = list_posts(project, token, user["id"])
            predicted_ids = {post["id"] for post in predicted}
            rows = question_rows(posts, predicted_ids)
            missing_closed = [row for row in rows if not row["forecasted"]
                              and row["status"] in {"closed", "resolved"}
                              and not row["practice_title"]]
            report["projects"][project] = {
                "listed_posts": len(posts), "forecasted_posts": len(predicted_ids),
                "listed_questions": len(rows),
                "forecasted_questions": sum(row["forecasted"] for row in rows),
                "status_counts": dict(Counter(row["status"] for row in rows)),
                "missing_closed_nonpractice_title_count": len(missing_closed),
                "missing_closed_post_ids": sorted({row["post_id"] for row in missing_closed}),
                "questions": rows,
            }
    except Exception as exc:
        report["errors"].append({"area": "metaculus", "error_type": type(exc).__name__})

    now = datetime.now(timezone.utc)
    baseline = utc(os.getenv("MONITORING_SINCE", "2026-10-16T12:00:00Z"))
    pending, recent_missing = [], []
    for project in report.get("projects", {}).values():
        for row in project["questions"]:
            opened, closed = utc(row["open_time"]), utc(row["close_time"])
            if row["practice_title"] or row["forecasted"]:
                continue
            if row["status"] == "open" and opened and closed and opened <= now < closed:
                pending.append(row)
            elif closed and baseline <= closed <= now and row["status"] in {"closed", "resolved"}:
                recent_missing.append(row)
    report["pending_open"] = pending
    report["missing_since_monitoring_baseline"] = recent_missing
    report["monitoring_since"] = baseline.isoformat()
    output = Path(os.getenv("BOT_AUDIT_OUTPUT", "bot_audit.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    # No live question text or probabilities in monitoring output.
    print("READ_ONLY_AUDIT_RESULT " + json.dumps(report))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write("### Read-only coverage and credit check\n\n")
            file.write("No model calls or forecast submissions.\n\n")
            file.write("| Project | Forecasted questions | Listed questions | Closed without forecast |\n")
            file.write("|---|---:|---:|---:|\n")
            for name, project in report.get("projects", {}).items():
                file.write(f"| {name} | {project['forecasted_questions']} | {project['listed_questions']} | {project['missing_closed_nonpractice_title_count']} |\n")
            file.write(f"\nOpen without forecast: **{len(pending)}**. Closed without forecast since {baseline.isoformat()}: **{len(recent_missing)}**.\n\n")
            budget = report.get("budget", {})
            file.write(f"Remaining credit: **{budget.get('remaining_usd', 'unknown')} USD**; cumulative account spend: **{budget.get('spend_usd', 'unknown')} USD**.\n")
            if report["errors"]:
                file.write(f"\nAPI check errors: {report['errors']}\n")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
            file.write(f"has_pending={'true' if pending else 'false'}\n")
            file.write(f"pending_count={len(pending)}\n")
    metaculus_failed = any(error["area"] == "metaculus" for error in report["errors"])
    if metaculus_failed or (not preflight and report["errors"]):
        raise SystemExit(1)
    if not preflight and recent_missing:
        print("::warning::Questions have closed without a forecast since the monitoring baseline")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true", help="Set pending-question outputs; never call an LLM")
    args = parser.parse_args()
    main(preflight=args.preflight)
