"""Read-only coverage/budget audit. Never calls an LLM or posts a forecast."""

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


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
    for post in posts:
        questions = [post["question"]] if post.get("question") else []
        if post.get("group_of_questions"):
            questions.extend(post["group_of_questions"].get("questions", []))
        for question in questions:
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


def main():
    report = {"audit_utc": datetime.now(timezone.utc).isoformat(), "errors": []}
    router_key = os.environ.get("OPENROUTER_API_KEY", "")
    try:
        key = get_json("https://openrouter.ai/api/v1/key", router_key, "Bearer")["data"]
        report["openrouter"] = {name: key.get(name) for name in (
            "limit", "limit_remaining", "limit_reset", "usage", "usage_daily",
            "usage_weekly", "usage_monthly", "expires_at", "include_byok_in_limit")}
        effective = key.get("effective_budget")
        if isinstance(effective, dict):
            report["openrouter"]["effective_budget"] = {name: effective.get(name) for name in (
                "limit_usd", "remaining_usd", "spend_usd", "reset_interval", "resets_at")}
    except Exception as exc:
        report["errors"].append({"area": "openrouter", "error": str(exc)})

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
        report["errors"].append({"area": "metaculus", "error": str(exc)})

    # Only sanitized numeric/account metadata is saved; no keys or private text.
    Path("bot_audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("READ_ONLY_AUDIT_RESULT " + json.dumps(report))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as file:
            file.write("Read-only bot audit: no model calls or forecast submissions.\n\n```json\n")
            file.write(json.dumps(report, indent=2))
            file.write("\n```\n")


if __name__ == "__main__":
    main()
